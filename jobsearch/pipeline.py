#!/usr/bin/env python3
"""
jobsearch.pipeline — тонкий CLI поверх ядра скоринга (scoring), рендера (render)
и персонального состояния (store).

Читает накопленный пул jobs_*.csv -> применяет текущие фильтры -> по каждой
вакансии: Haiku-предфильтр -> Sonnet-тюнинг -> комплект (в памяти) -> запись на
диск + воронка. Вся LLM-логика и рендер живут в scoring/render; состояние — в
store. Сам этот модуль только оркеструет I/O на краю.

Запуск:
  pip install -e .
  export ANTHROPIC_API_KEY="sk-ant-..."
  python -m jobsearch.pipeline [jobs_YYYY-MM-DD.csv] [out_dir]
"""

import csv
import datetime
import glob
import os
import re
import sys
import time

from . import filters, render
from .config import load_platform_config
from .models import Job
from .scoring import AnthropicClient, analyze, score_fit
from .store import LOCAL_USER, FlatFileUserState

MASTER_CV = "master_cv.md"
DEFAULT_REVIEW_DIR = "review"
REGION_ORDER = {"WORLDWIDE": 0, "EUROPE": 1, "UNKNOWN": 2, "US-ONLY": 3}

# Короткий профиль для дешёвого предфильтра (Haiku) — для текущего (локального) юзера.
# В продукте выводится из CV конкретного пользователя, поэтому передаётся аргументом,
# а не зашит в score_fit.
SHORT_PROFILE = (
    "Technical Support & Integration Engineer, 2 года (Axxonsoft). 70-90 тикетов/нед, "
    ">85% self-resolution. SQL, REST API, MQTT, Azure, Git, Ruby DSL, JasperReports, "
    "Grafana/InfluxDB. Также реальный опыт Project Manager: 2 доведённых проекта, команды "
    "6-8, доставка, стейкхолдеры, кросс-функциональная координация. B2B через ИП в Армении. "
    "EN C1, RU native."
)


# ---------------------------------------------------------------------------
# Загрузка CV и чтение пула (I/O оркестрации)
# ---------------------------------------------------------------------------
def load_master(path: str = MASTER_CV) -> str:
    if not os.path.exists(path):
        sys.exit(f"Нет {path} рядом со скриптом — положи мастер-CV.")
    with open(path, encoding="utf-8") as f:
        txt = f.read()
    return re.sub(r"<!--.*?-->", "", txt, flags=re.S)  # убираем служебные комментарии


def _row_to_job(row) -> Job:
    return Job(
        dedup_key=filters.compute_dedup_key(row.get("company", ""), row.get("title", ""), row.get("url", "")),
        source=row.get("source", ""), url=row.get("url", ""), company=row.get("company", ""),
        title=row.get("title", ""), location=row.get("location", ""), region=row.get("region", ""),
        description=row.get("description", ""), date_posted=row.get("date", ""), scraped_at="",
    )


def write_package(pkg, job, score: int, review_dir: str) -> str:
    """Кладёт комплект (уже собранный в памяти) на диск. safe_name санитайзит любые
    скрейпленные строки в пути (защита от path traversal)."""
    folder = os.path.join(
        review_dir,
        f"{score:03d}_{render.safe_name(job.company or 'x')}_{render.safe_name(job.title or 'x')}",
    )
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "Vitalii_Vlasov_CV.docx"), "wb") as f:
        f.write(pkg.cv_docx)
    with open(os.path.join(folder, "cover_letter.txt"), "w", encoding="utf-8") as f:
        f.write(pkg.cover_letter)
    with open(os.path.join(folder, "ats_report.md"), "w", encoding="utf-8") as f:
        f.write(pkg.ats_report)
    return folder


# ---------------------------------------------------------------------------
def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Нет ANTHROPIC_API_KEY. Задай: export ANTHROPIC_API_KEY=sk-ant-...")

    config = load_platform_config()
    client = AnthropicClient()           # ключ — только из env
    cv_text = load_master()
    state = FlatFileUserState()
    user = LOCAL_USER

    review_dir = sys.argv[2].strip() if len(sys.argv) > 2 and sys.argv[2].strip() else DEFAULT_REVIEW_DIR

    # Читаем ВЕСЬ накопленный пул (все jobs_*.csv), а не только свежий файл —
    # чтобы прорабатывать бэклог день за днём и ничего не терять.
    if len(sys.argv) > 1 and sys.argv[1].strip():
        files = [sys.argv[1]]
    else:
        files = sorted(glob.glob("jobs_*.csv"))
        if not files:
            sys.exit("Не найден jobs_*.csv — сначала запусти jobsearch.finder")

    pool = []
    for fp in files:
        try:
            with open(fp, encoding="utf-8-sig") as f:
                pool += list(csv.DictReader(f))
        except Exception as e:
            print(f"  [пропуск {fp}: {e}]")

    # дедуп пула по ссылке
    seen_urls, jobs = set(), []
    for row in pool:
        u = row.get("url", "")
        if u and u in seen_urls:
            continue
        seen_urls.add(u)
        jobs.append(_row_to_job(row))

    # лучшие регионы — вперёд
    jobs.sort(key=lambda j: REGION_ORDER.get(j.region, 9))

    # Применяем к пулу ТЕКУЩИЕ фильтры (роль + стоп-слова + сеньорность), чтобы уже
    # собранные нерелевантные вакансии не доедались впустую.
    _loose = os.environ.get("LOOSE_FILTER") == "1"

    def _passes(j: Job) -> bool:
        return ((not filters.blocked(j.title)) and filters.remote_ok(j.title, j.description, None)
                and (_loose or filters.matches_role(j.title)))

    def _fresh(j: Job) -> bool:
        return (j.region in config.process_regions
                and not state.is_processed(user, j.url)
                and _passes(j))

    # Свежие — первыми: где новее вакансия, там меньше откликов/конкурентов.
    _today = datetime.date.today().isoformat()
    queue = sorted((j for j in jobs if _fresh(j)),
                   key=lambda j: j.date_posted or _today, reverse=True)[:config.max_jobs]
    print(f"К обработке: {len(queue)} (необработанных в пуле всего: "
          f"{sum(1 for j in jobs if _fresh(j))})")

    done = 0
    for job in queue:
        title = job.title[:50]
        try:
            # Шаг 1 (дёшево, Haiku): предварительная оценка fit
            pre = score_fit(job, SHORT_PROFILE, config, client)
            if pre.fit_score < config.pre_min_fit:
                state.mark_processed(user, job.url)
                print(f"  [{pre.fit_score:>3}] {title} — предфильтр отсеял (Haiku)")
                time.sleep(1)
                continue

            # Шаг 2 (дорого, Sonnet): полный тюнинг только для прошедших
            res = analyze(job, cv_text, config, client)
            if res.fit_score < config.min_fit:
                state.mark_processed(user, job.url)
                print(f"  [{res.fit_score:>3}] {title} — ниже порога после тюнинга, пропуск")
                time.sleep(1)
                continue

            pkg = render.build_package(job, res, cv_text)
            folder = write_package(pkg, job, res.fit_score, review_dir)
            state.save_application(user, job, res, folder)
            state.mark_processed(user, job.url)
            done += 1
            print(f"  [{res.fit_score:>3}] {title} -> {folder}/")
            time.sleep(1)
        except Exception as e:
            print(f"  [ERR] {title}: {e}")

    print(f"\nГотово: {done} комплектов в ./{review_dir}/")
    print("В каждой папке: подогнанное резюме (.docx) + cover_letter.txt + ats_report.md")


if __name__ == "__main__":
    main()
