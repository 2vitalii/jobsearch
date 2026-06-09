# Job Search Automation
Конвейер сбора удалённых/B2B вакансий (JobSpy: LinkedIn/Indeed/Google; RemoteOK, WWR, Remotive; ATS) + скоринг и подгонка резюме через Anthropic API (Claude).
- job_finder.py — сбор вакансий
- pipeline.py — скоринг + резюме/cover letter
- run.sh / search.sh / run_daily.sh — запуск
- master_cv.md — мастер-резюме
secrets.sh с ключом не коммитится (см. .gitignore).
