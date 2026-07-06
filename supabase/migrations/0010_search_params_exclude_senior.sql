-- =============================================================================
-- 0010_search_params_exclude_senior.sql — add exclude_senior flag to saved search.
--
-- HOW TO APPLY
--   • Supabase SQL Editor: paste this whole file and run it, OR
--   • CLI:  supabase db push        (picks up files in supabase/migrations/)
--
-- Idempotent: ADD COLUMN IF NOT EXISTS is safe to re-run.
--
-- WHY
--   Senior / principal / director / head-of titles were previously hard-coded
--   out of every user's results (a per-author assumption baked into
--   NEGATIVE_TITLE_KEYWORDS). This migration adds an opt-in flag so that each
--   user can choose whether to exclude those seniority levels.
--   Default FALSE = do NOT exclude senior titles (safe for experienced candidates;
--   existing rows automatically inherit the permissive default).
--   Set TRUE to restore the old auto-exclusion behaviour for users who prefer it.
--
-- RLS / grants: no changes needed — the new column is a plain boolean with a
-- NOT NULL default and inherits the existing row-level security policy on
-- public.search_params (user can only read/write their own row).
-- =============================================================================

alter table public.search_params
    add column if not exists exclude_senior boolean not null default false;
