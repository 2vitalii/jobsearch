-- =============================================================================
-- 0009_matches_add_run_snapshot.sql — link matches to their producing run and
-- snapshot the search params used by that run.
--
-- HOW TO APPLY
--   • Supabase SQL Editor: paste this whole file and run it, OR
--   • CLI:  supabase db push        (picks up files in supabase/migrations/)
--
-- Idempotent: re-running is safe (ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT
-- EXISTS).
--
-- WHY
--   Attribution seam: each generated match now carries the run_id of the
--   background task that produced it, and each run row carries a JSON snapshot
--   of the six search-param fields that were active at the time the run started.
--   This is the minimal foundation for a future funnel dashboard and per-run
--   notifications. No full multi-search model is introduced — search_params
--   stays one-per-user.
--
-- matches.run_id
--   Foreign key → runs(id) ON DELETE SET NULL so deleting a run does NOT delete
--   the match. The match outlives its run. Nullable: existing rows with
--   run_id = NULL remain valid; the column is populated only for runs created
--   after this migration is applied.
--
-- idx_matches_user_run
--   Composite index on (user_id, run_id) to support per-run match queries in
--   the future (e.g. "show me all matches from run X").
--
-- runs.search_snapshot
--   JSONB column capturing the six search-param fields (keywords, locations,
--   period_hours, work_format, loose, targeted) at the moment POST /run is
--   called. Nullable: existing runs rows without a snapshot remain valid.
--
-- ACCESS / RLS
--   No new policies or grants are required:
--   • matches already has full RLS (SELECT/INSERT/UPDATE/DELETE all use
--     auth.uid() = user_id) and the `authenticated` SELECT grant from 0006.
--     The new run_id column on matches is automatically covered by the existing
--     SELECT grant and RLS policy — authenticated users can read it from their
--     own match rows.
--   • runs already grants SELECT to authenticated (0008). The new
--     search_snapshot column on runs is likewise covered — authenticated users
--     can read it from their own run rows via the existing runs_select policy
--     (auth.uid() = user_id).
--   No new GRANT or CREATE POLICY statements are needed or added here.
-- =============================================================================

alter table public.matches
    add column if not exists run_id uuid references public.runs(id) on delete set null;

create index if not exists idx_matches_user_run
    on public.matches (user_id, run_id);

alter table public.runs
    add column if not exists search_snapshot jsonb;
