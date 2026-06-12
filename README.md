# Job Search Automation

Конвейер сбора удалённых/B2B вакансий (JobSpy: LinkedIn/Indeed/Google; RemoteOK, We Work
Remotely, Remotive; ATS: Greenhouse/Lever/Ashby) + скоринг и подгонка резюме через Anthropic
API (Claude: Haiku-предфильтр → Sonnet-тюнинг).

## Структура
Чистое ядро-библиотека `jobsearch/`, поверх которого позже встанет продукт. Вся I/O — на
краю (тонкие `finder`/`pipeline`); `scrape`/`score_fit`/`analyze`/`render_cv` чистые.
```
jobsearch/            # пакет-ядро
  models.py           # датаклассы: Job, SearchParams, PlatformConfig, PreScore, MatchResult, Package
  config.py           # PlatformConfig из env (секреты — только из env)
  filters.py          # чистые фильтры роли/remote/региона + ключи дедупа
  sources.py          # scrape(params, config) -> list[Job] (JobSpy/RemoteOK/WWR/Remotive/ATS)
  scoring.py          # score_fit/analyze + инжектируемый LLMClient (ключ из env)
  render.py           # render_cv/build_package -> артефакты в памяти (docx-bytes/cover/ATS)
  store.py            # JobStore + UserState (user_id везде, без кросс-юзерных чтений)
  finder.py           # тонкий CLI: сбор -> каталог -> jobs_*.csv
  pipeline.py         # тонкий CLI: пул -> скоринг (Haiku→Sonnet) -> комплекты
scripts/              # обёртки запуска (run / search / run_daily / push)
deploy/               # LaunchAgent (ежедневный автозапуск)
docs/ROADMAP.md       # дорожная карта продукта
tests/                # тесты ядра (filters, scoring, store, render, dedup, models)
master_cv.md          # мастер-резюме (источник правды; gitignored — PII)
pyproject.toml        # пакет, зависимости, entry points
```
Runtime-артефакты (`jobs_*.csv`, `review*/`, `applications.csv`, логи, файлы состояния) и
PII (`secrets.sh`, `master_cv.md`) не коммитятся — см. `.gitignore`.

## Установка
```bash
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"            # пакет + pytest
cp secrets.sh.example secrets.sh   # впиши ANTHROPIC_API_KEY, потом: chmod 600 secrets.sh
```

## Запуск
```bash
python -m jobsearch.finder 24                  # собрать вакансии за 24 ч -> jobs_YYYY-MM-DD.csv
python -m jobsearch.pipeline <csv> <out_dir>   # скоринг + комплекты в <out_dir>/
python -m pytest                               # тесты
bash scripts/run.sh 2                           # интерактивный запуск (меню режимов)
```
После `pip install -e .` доступны и консольные команды: `jobsearch-find`, `jobsearch-tailor`.

## Безопасность
`secrets.sh` (ключ Anthropic) и `master_cv.md` (контакты, PII) — gitignored, не коммитятся.
Ключ Anthropic читается LLM-клиентом только из окружения и не пишется в логи; описания
вакансий (недоверенный ввод) идут только в user-позицию промпта, пути санитайзятся.
