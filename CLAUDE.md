# CLAUDE.md — конституция проекта для Claude Code

## О проекте
Два параллельных трека, общая AI-инфраструктура:
1. **Личный пайплайн поиска работы** (основной, рабочий) — сбор вакансий + скоринг + подгонка резюме.
2. **Продукт** — многопользовательское приложение на базе того же ядра (см. `docs/ROADMAP.md`).

## Язык и коммуникация
- Общение со мной (владельцем) — **по-русски**.
- Код, комментарии в коде, CV и cover letters — **по-английски**.

## Структура репозитория
```
jobsearch/            # пакет-ядро (библиотека; поверх него позже встанет продукт)
  models.py           # датаклассы: Job, SearchParams, PlatformConfig, PreScore, MatchResult, Package
  config.py           # PlatformConfig из env (секреты — НЕ здесь, только из env)
  filters.py          # чистые фильтры роли/remote/региона + ключи дедупа (без сети/файлов)
  sources.py          # scrape(params, config) -> list[Job]: JobSpy/RemoteOK/WWR/Remotive/ATS
  scoring.py          # score_fit/analyze + инжектируемый LLMClient (ключ Anthropic из env)
  render.py           # render_cv/build_package -> артефакты в памяти (docx-bytes/cover/ATS)
  store.py            # JobStore + UserState (user_id во всех методах, без кросс-юзерных чтений)
  finder.py           # тонкий CLI: scrape -> каталог (JobStore) -> jobs_*.csv
  pipeline.py         # тонкий CLI: пул jobs_*.csv -> scoring -> комплекты на диск
scripts/              # обёртки запуска (run / search / run_daily / push)
deploy/               # LaunchAgent
docs/ROADMAP.md       # дорожная карта продукта (см. Фазу 0 — чистое ядро)
tests/                # тесты ядра (filters, scoring, store, render, dedup, models)
master_cv.md          # мастер-резюме (источник правды; gitignored — содержит PII)
pyproject.toml        # пакет, зависимости, entry points (jobsearch-find / jobsearch-tailor)
```
I/O живёт на краю (тонкие `finder`/`pipeline`); ядро (`scrape`/`score_fit`/`analyze`/`render_cv`)
чистое — вызывается без argv/env и без чтения файлов изнутри.

Runtime-артефакты (`jobs_*.csv`, `review*/`, `applications.csv`, логи, файлы состояния) и
PII (`secrets.sh`, `master_cv.md`) — gitignored, в репо не коммитятся.

## Как запускать
```bash
python -m jobsearch.finder 24                 # собрать вакансии за 24 ч
python -m jobsearch.pipeline <csv> <out_dir>  # скоринг + комплекты
python -m pytest -q                           # тесты
bash scripts/run.sh 2                          # интерактивный запуск
```

## ЖЁСТКИЕ правила для движка подгонки резюме (scoring.py SYSTEM-промпт + render.py)
- Использовать **только реальные факты** из `master_cv.md`. Не выдумывать метрики, технологии, опыт.
- Тело опыта/проектов/образования **не переписывать**. Тюнятся только summary и порядок/формулировки навыков — там, где это правда.
- Чего у кандидата нет — идёт в `ats_missing`/`gaps`, никогда в summary/skills/письмо.

## Рабочие договорённости
- Изменения отдавать **целыми файлами**, не патчами-обрывками (для лёгкой замены).
- **Перед коммитом — всегда `python -m pytest -q`.** Если тесты красные — не коммитить.
- Сообщения коммитов в стиле Conventional Commits (`feat:`, `fix:`, `chore:`, `refactor:`).
- Известная особенность: JobSpy отдаёт markdown с экранированными дефисами (`on\-site`) — любое сопоставление по дефисным ключевым словам нормализует `\\` перед матчингом.

## Чего НЕ делать
- Никогда не коммитить `secrets.sh` (ключ Anthropic).
- `master_cv.md` содержит контакты (PII) — учитывать при работе с публичностью репо.
- Не трогать ежедневный автозапуск (LaunchAgent) без явной просьбы.
