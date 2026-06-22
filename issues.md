<!-- Drift control. Agents read this before working. Keep it short and current. -->

# Project Issues & Drift Control

last_verified_commit: fca6a73f166f70c00c620e7f3cc3e52c697468d9

> Everything up to `last_verified_commit` is considered verified.
> Do not re-review or re-fix verified code without a concrete reason.

## Open
<!-- known bugs / TODOs not yet done. Format: - [ ] short description (area) -->
- [ ] Pre-existing ruff violations: unused `field` import in `jobsearch/models.py` (F401) and ambiguous variable `l` in `jobsearch/render.py` (E741). Not introduced by STEP 1. Reported for awareness.

## In progress
<!-- - [~] description — owner/agent -->

## Closed (recent)
<!-- - [x] description — commit hash -->
- [x] STEP 1: add edge-case tests for matches_role/blocked/classify_region/remote_ok — branch test/filters-edge-cases
