-- =============================================================================
-- 0007_matches_denormalize_job.sql — denormalize job fields onto matches.
--
-- HOW TO APPLY
--   • Supabase SQL Editor: paste this whole file and run it, OR
--   • CLI:  supabase db push
--
-- Idempotent: ADD COLUMN IF NOT EXISTS.
--
-- WHY (SG-02)
--   The user-scoped (authenticated) path must NOT read the shared `jobs` pool
--   (no grant + RLS-closed). The matches list/detail previously joined
--   jobs(title, company, url, region) via PostgREST embedding. We copy those few
--   display fields onto each match row so the per-user path renders entirely from
--   `matches`. Written at match-creation time in api/run.py (the job data is
--   already in hand there, under service_role).
--
--   This is display denormalization only — `job_id` remains the source of truth
--   and the FK to `jobs`; full job detail (if ever needed) is fetched by id via
--   service_role, narrowly, from an already-owned match.
-- =============================================================================

alter table public.matches add column if not exists job_title   text;
alter table public.matches add column if not exists job_company text;
alter table public.matches add column if not exists job_url     text;
alter table public.matches add column if not exists job_region  text;
