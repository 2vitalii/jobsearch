<!-- Drift control. Agents read this before working. Keep it short and current. -->

# Project Issues & Drift Control

last_verified_commit: fca6a73f166f70c00c620e7f3cc3e52c697468d9

> Everything up to `last_verified_commit` is considered verified.
> Do not re-review or re-fix verified code without a concrete reason.

## Open
<!-- known bugs / TODOs not yet done. Format: - [ ] short description (area) -->
- [ ] Pre-existing ruff violations: unused `field` import in `jobsearch/models.py` (F401) and ambiguous variable `l` in `jobsearch/render.py` (E741). Not introduced by recent work. Reported for awareness.
- [ ] SG-03 migration 0008_runs_table.sql must be applied by hand in the Supabase SQL Editor (runs table + RLS + partial unique index). Code is merged but the table does not exist until applied.

## In progress
<!-- - [~] description — owner/agent -->

## Closed (recent)
<!-- - [x] description — commit hash -->
- [x] SG-03: async POST /run via FastAPI BackgroundTasks + runs table (history/status, one-active-run, startup orphan cleanup). Reviewers (code + security) PASS after rework. Merged to main. Migration 0008 awaits manual apply (see Open).
- [x] STEP 1: add edge-case tests for matches_role/blocked/classify_region/remote_ok — branch test/filters-edge-cases (PR #1)
