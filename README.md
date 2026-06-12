# Job Search Automation

Конвейер сбора удалённых/B2B вакансий (JobSpy: LinkedIn/Indeed/Google; RemoteOK, We Work
Remotely, Remotive; ATS: Greenhouse/Lever/Ashby) + скоринг и подгонка резюме через Anthropic
API (Claude: Haiku-предфильтр → Sonnet-тюнинг).

## Структура
```
jobsearch/            # пакет с логикой
  finder.py           # сбор вакансий
  pipeline.py         # скоринг + генерация комплектов (CV/cover/ATS-отчёт)
scripts/              # обёртки запуска (run / search / run_daily / push)
deploy/               # LaunchAgent (ежедневный автозапуск)
docs/ROADMAP.md       # дорожная карта продукта
tests/                # тесты фильтров
master_cv.md          # мастер-резюме (источник правды)
pyproject.toml        # пакет, зависимости, entry points
```
Runtime-артефакты (`jobs_*.csv`, `review*/`, `applications.csv`, логи, файлы состояния)
не коммитятся — см. `.gitignore`.

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
`secrets.sh` (ключ Anthropic) — gitignored, никогда не коммитится. `master_cv.md` содержит
контакты (PII) — учитывать при публичности репозитория.
