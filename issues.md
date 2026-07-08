<!-- Drift control. Agents read this before working. Keep it short and current. -->

# Project Issues & Drift Control

last_verified_commit: fca6a73f166f70c00c620e7f3cc3e52c697468d9

> Everything up to `last_verified_commit` is considered verified.
> Do not re-review or re-fix verified code without a concrete reason.

## Open
<!-- known bugs / TODOs not yet done. Format: - [ ] short description (area) -->
- [ ] Pre-existing ruff violations: ambiguous variable `l` in `jobsearch/render.py` (E741), and unused imports in `tests/test_filter_debug_instrumentation.py`. Not introduced by recent work. Reported for awareness. (`field` import in models.py fixed as part of feat/dynamic-role-filter.)
- [ ] Per-run results filtering on /results is a future extension. GET /matches currently returns all user matches (no ?run_id filter); RunStatus does not expose the run id. To scope results to a specific run, either: (a) add ?run_id query param to GET /matches + pass run_id in the /search→/results redirect, or (b) expose run_id in RunStatus and let the frontend filter client-side. Not a blocker for 5d (all-matches view works correctly). (backlog, web)
- [ ] Pagination on /results is deferred — match sets are small (unique(user_id, job_id) dedup). Implement if sets grow beyond ~50. (backlog, web)
- [ ] SG-03 migration 0008_runs_table.sql must be applied by hand in the Supabase SQL Editor (runs table + RLS + partial unique index). Code is merged but the table does not exist until applied.
- [ ] Migration 0009_matches_add_run_snapshot.sql must be applied by hand in Supabase (matches.run_id FK + index, runs.search_snapshot). Apply AFTER 0008. Code merged (71eaf20) but columns don't exist until applied.
- [ ] CORS headers missing on backend error responses (api/main.py CORSMiddleware). When an endpoint returns 500 (or any unhandled exception), the response carries NO `Access-Control-Allow-Origin` header, so the browser reports an opaque "Load failed" / "No Access-Control-Allow-Origin header" instead of the real status/cause — masking backend errors and making them hard to diagnose from the frontend (observed on GET /search-params, /run/latest when the backend ran without Supabase env → 500). Fix: ensure CORS headers are emitted on error responses too (e.g. a custom exception handler that adds the ACAO header, or the Starlette CORS-on-error pattern), so future backend errors surface a real status/message in the browser. Separate task (branch + gates); backend, api/main.py. (tech-debt, backend/observability)

## Flagged — Product Differentiator (near-term backlog, NOT ordinary tech-debt)

**Processing limit (cost-control differentiator) — implement after 5c/5d**

The "processing limit" is the product's core cost-control mechanism: the user sees and sets
a cap on the number of jobs to process (score via LLM) BEFORE launching a run, and is billed
only for processed results. This makes cost transparent and gives users direct control over
spend per search.

What is missing:
- Backend: a `processing_limit` field in `SearchParamsBody` + `search_params` table column
  (migration required) + enforcement in `api/run.py` background task.
- Frontend (5c UI): a stepper control `[ − | value | + ]` in the search form (deliberately
  omitted in 5c because the backend field does not exist yet — rendering it would be a
  no-op contract drift).

Priority: implement in the next sprint after 5c/5d are merged. Needs: SearchParams migration
(backend) + stepper component (frontend, per web/CLAUDE.md §3 Stepper pattern). Do NOT treat
this as a minor TODO — it directly drives monetisation and the user's trust in the product.
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
