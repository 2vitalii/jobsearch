#!/usr/bin/env python3
"""
jobsearch.finder — тонкий CLI поверх ядра сбора (sources) и каталога (store).

Парсит argv -> собирает SearchParams/PlatformConfig -> вызывает scrape() ->
отсеивает уже виденные через JobStore -> пишет jobs_YYYY-MM-DD.csv -> сохраняет
ключи в каталог. Вся логика сбора/фильтрации живёт в sources/filters.

Запуск:
  pip install -e .
  python -m jobsearch.finder            # за неделю (168ч)
  python -m jobsearch.finder 24         # за последние 24 часа
  python -m jobsearch.finder 6          # за последние 6 часов (свежак / мало откликов)
"""

import argparse
import csv
import datetime as dt

from .config import load_platform_config
from .models import SearchParams
from .sources import scrape
from .store import FlatFileJobStore

# Дефолтный персональный запрос (бывшие глобалы SEARCH_TERMS / COUNTRIES / HOURS_OLD).
DEFAULT_HOURS = 168
DEFAULT_KEYWORDS = [
    "support engineer",
    "application support",
    "support specialist",
    "integration specialist",
    "junior project manager",
    "project coordinator",
    "technical consultant",
    "implementation consultant",
    "solutions consultant",
    "data support specialist",
]
DEFAULT_LOCATIONS = ["Poland", "Germany", "Netherlands", "Spain", "Portugal", "Ireland",
                     "France", "Sweden", "United Kingdom", "Italy", "Belgium", "Czech Republic",
                     "United Arab Emirates", "Saudi Arabia", "Qatar"]

CSV_COLS = ["region", "title", "company", "location", "source", "date", "url",
            "description", "status", "tailored_cv", "applied_date", "notes"]
REGION_ORDER = {"WORLDWIDE": 0, "EUROPE": 1, "UNKNOWN": 2, "US-ONLY": 3}


def _split(items):
    out = []
    for it in items or []:
        out += [x.strip() for x in it.split(",") if x.strip()]
    return out


def build_search_params(args) -> SearchParams:
    terms = _split(args.term)
    locs = _split(args.location)
    return SearchParams(
        keywords=terms or DEFAULT_KEYWORDS,
        locations=locs or DEFAULT_LOCATIONS,
        period_hours=args.hours if args.hours else DEFAULT_HOURS,
        loose=args.loose,
        targeted=bool(terms or locs),   # целевой поиск -> только JobSpy по term×location
    )


def _row(job):
    return {
        "region": job.region, "title": job.title, "company": job.company,
        "location": job.location, "source": job.source, "date": job.date_posted,
        "url": job.url, "description": job.description,
        "status": "NEW", "tailored_cv": "", "applied_date": "", "notes": "",
    }


def main():
    p = argparse.ArgumentParser(description="Сбор вакансий. Без флагов — обычный широкий прогон.")
    p.add_argument("hours", nargs="?", type=int, default=None, help="окно в часах (по умолч. неделя)")
    p.add_argument("--term", action="append", help="ключевое слово/роль (можно несколько раз или через запятую)")
    p.add_argument("--location", action="append", help="локация (можно несколько раз или через запятую)")
    p.add_argument("--loose", action="store_true", help="доверять ключевому слову, не резать фильтром ролей")
    a = p.parse_args()

    params = build_search_params(a)
    config = load_platform_config()

    if params.targeted:
        print(f"Целевой поиск за {params.period_hours} ч | термины: {params.keywords} | "
              f"локации: {params.locations}" + (" | свободный фильтр" if params.loose else ""))
    else:
        print(f"Собираю вакансии за последние {params.period_hours} ч "
              f"(LinkedIn/Indeed/Google по странам + RemoteOK + WWR + Remotive + ATS)...")

    jobs = scrape(params, config)

    store = FlatFileJobStore()
    before = len(jobs)
    new = [j for j in jobs if not store.has_seen(j.dedup_key)]
    skipped = before - len(new)

    new.sort(key=lambda j: REGION_ORDER.get(j.region, 9))

    fname = f"jobs_{dt.date.today().isoformat()}.csv"
    with open(fname, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        w.writeheader()
        for j in new:
            w.writerow(_row(j))

    store.save(new)

    ww = sum(1 for j in new if j.region == "WORLDWIDE")
    eu = sum(1 for j in new if j.region == "EUROPE")
    by_src = {}
    for j in new:
        by_src[j.source] = by_src.get(j.source, 0) + 1
    print(f"\nГотово. Новых: {len(new)} | WORLDWIDE: {ww} | EUROPE: {eu}")
    print(f"По источникам: {by_src}")
    print(f"Пропущено как уже виденные: {skipped}")
    print(f"Файл-трекер (с описаниями): {fname}")


if __name__ == "__main__":
    main()
