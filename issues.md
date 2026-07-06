<!-- Drift control. Agents read this before working. Keep it short and current. -->

# Project Issues & Drift Control

last_verified_commit: fca6a73f166f70c00c620e7f3cc3e52c697468d9

> Everything up to `last_verified_commit` is considered verified.
> Do not re-review or re-fix verified code without a concrete reason.

## Open
<!-- known bugs / TODOs not yet done. Format: - [ ] short description (area) -->
- [ ] Pre-existing ruff violations: ambiguous variable `l` in `jobsearch/render.py` (E741), and unused imports in `tests/test_filter_debug_instrumentation.py`. Not introduced by recent work. Reported for awareness. (`field` import in models.py fixed as part of feat/dynamic-role-filter.)
- [ ] SG-03 migration 0008_runs_table.sql must be applied by hand in the Supabase SQL Editor (runs table + RLS + partial unique index). Code is merged but the table does not exist until applied.
- [ ] Migration 0009_matches_add_run_snapshot.sql must be applied by hand in Supabase (matches.run_id FK + index, runs.search_snapshot). Apply AFTER 0008. Code merged (71eaf20) but columns don't exist until applied.
- [x] Follow-up (Python, deferred): api/run.py RunStatus.status is `str` — tighten to `Literal["running","done","failed"]` to match the web Zod enum and catch drift statically. Done in branch refactor/runstatus-literal.
- [ ] Cleanup (optional): web/CLAUDE.md and web/AGENTS.md are not prettier-clean on main (pre-existing). `prettier --check .` flags them; reformat in a separate `style:` commit if desired.
- [x] Unstaged: tests/test_web_search_schemas.py — landed and updated in feat/dynamic-role-filter (now includes exclude_senior field assertions).

## In progress
<!-- - [~] description — owner/agent -->
- [~] feat: FILTER_DEBUG observability — additive env-flag instrumentation in sources.py + api/run.py (branch feat/filter-debug-instrumentation)
- [~] feat: dynamic role/seniority filter — matches_role/blocked parametrized, exclude_senior field, 0010 migration file. Branch feat/dynamic-role-filter. Awaiting PM gate before PR.
  Known limitation: substring keyword match may miss synonyms (e.g. "support engineer" won't match "Customer Care Specialist"). Mitigated by score_fit/analyze as authoritative relevance gate + loose=True flag.

## Closed (recent)
<!-- - [x] description — commit hash -->
- [x] feat(web): /search page — params form (chips/presets/segmented), run trigger, live progress polling (2.5s), 409 handling, reload-restore via /run/latest, temporary /results stub, nav links. Reviewers: security PASS; code + tester PASS after rework (removed setTimeout(0) via SearchForm child + navigate-once finalizedRef guard). Gates green (tsc/eslint/build/prettier). Merged to main (247e7bf).
- [x] Attribution seam: matches.run_id (FK->runs ON DELETE SET NULL) + runs.search_snapshot; POST /run snapshots search params, background stamps run_id on each match; GET /matches(/{id}) expose run_id, GET /run(/{id},/latest) expose search_snapshot. Reviewers (code CLEAN + security PASS). Merged to main (71eaf20). Migration 0009 awaits manual apply (see Open).
- [x] SG-03: async POST /run via FastAPI BackgroundTasks + runs table (history/status, one-active-run, startup orphan cleanup). Reviewers (code + security) PASS after rework. Merged to main. Migration 0008 awaits manual apply (see Open).
- [x] STEP 1: add edge-case tests for matches_role/blocked/classify_region/remote_ok — branch test/filters-edge-cases (PR #1)
