-- =============================================================================
-- 0001_init_schema.sql — initial Supabase schema for the jobsearch product.
--
-- HOW TO APPLY
--   • Supabase SQL Editor: paste this whole file and run it, OR
--   • CLI:  supabase db push        (picks up files in supabase/migrations/)
--
-- This migration is idempotent: re-running it is safe (IF NOT EXISTS guards,
-- DROP POLICY IF EXISTS before CREATE POLICY, ON CONFLICT DO NOTHING for storage).
--
-- DESIGN NOTES
--   Columns mirror the core dataclasses so the DB backs the existing contracts:
--     - jobs           <- jobsearch/models.py  Job            (shared pool)
--     - search_params  <- jobsearch/models.py  SearchParams   (per-user)
--     - cvs            <- master_cv.md          (per-user master CV)
--     - matches        <- jobsearch/models.py  MatchResult    (per-user)
--   Storage seams (jobsearch/store.py):
--     - JobStore  -> jobs            : platform-wide catalog + dedup (no user_id)
--     - UserState -> cvs/search_params/matches : every row scoped by user_id
--
-- SECURITY MODEL: backend-mediated.
--   The frontend talks to Supabase Auth only; all data access goes through our
--   backend using the service_role key (which bypasses RLS). We deliberately do
--   NOT grant anon/authenticated on these tables, so they are not reachable via
--   the auto-generated REST (Data API). RLS + owner policies are kept anyway as
--   defense-in-depth, so even an accidental exposure stays per-user safe.
-- =============================================================================

-- Needed for gen_random_uuid().
create extension if not exists pgcrypto;


-- ---------------------------------------------------------------------------
-- cvs — per-user master CV (the master_cv.md source of truth) + derived profile
-- ---------------------------------------------------------------------------
create table if not exists public.cvs (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references auth.users(id) on delete cascade,
    markdown      text not null,                 -- master CV, master_cv.md format
    short_profile text,                          -- condensed profile for score_fit (filled later)
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);


-- ---------------------------------------------------------------------------
-- search_params — per-user saved search (mirrors models.SearchParams)
-- ---------------------------------------------------------------------------
create table if not exists public.search_params (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references auth.users(id) on delete cascade,
    keywords     text[] not null default '{}',   -- SearchParams.keywords (role terms)
    locations    text[] not null default '{}',   -- SearchParams.locations
    period_hours int  not null default 168,       -- SearchParams.period_hours (search window)
    work_format  text not null default 'remote',  -- SearchParams.work_format
    loose        bool not null default false,      -- SearchParams.loose
    targeted     bool not null default false,      -- SearchParams.targeted
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);


-- ---------------------------------------------------------------------------
-- jobs — SHARED vacancy pool (NOT per-user, no user_id). Mirrors models.Job.
--   dedup_key is the cross-source identity from filters.compute_dedup_key
--   ("company|title"); UNIQUE enforces dedup-on-insert at the DB level.
-- ---------------------------------------------------------------------------
create table if not exists public.jobs (
    id          uuid primary key default gen_random_uuid(),
    dedup_key   text not null unique,            -- filters.compute_dedup_key
    source      text not null default '',         -- Job.source
    title       text not null default '',         -- Job.title
    company     text not null default '',         -- Job.company
    location    text not null default '',         -- Job.location
    region      text not null default '',         -- Job.region (WORLDWIDE/EUROPE/...)
    url         text not null default '',         -- Job.url
    date_posted text not null default '',         -- Job.date_posted (raw source string)
    description text not null default '',         -- Job.description (UNTRUSTED text)
    scraped_at  timestamptz not null default now(),
    first_seen  timestamptz not null default now()
);


-- ---------------------------------------------------------------------------
-- matches — per-user scoring/tailoring result for a job (mirrors MatchResult).
--   The full MatchResult lands in `analysis` (jsonb); the hot/queryable fields
--   (fit_score, b2b_eligible, status) are promoted to columns.
-- ---------------------------------------------------------------------------
create table if not exists public.matches (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references auth.users(id) on delete cascade,
    job_id        uuid not null references public.jobs(id) on delete cascade,
    fit_score     int,                            -- MatchResult.fit_score
    b2b_eligible  text,                            -- MatchResult.b2b
    status        text not null default 'NEW',     -- funnel: NEW/GENERATED/APPLIED/...
    -- Full MatchResult payload: jd_keywords / ats_present / ats_missing /
    -- tailored_summary / tailored_skills / gaps / recruiter_verdict / reason.
    analysis      jsonb not null default '{}'::jsonb,
    cover_letter  text,                            -- MatchResult.cover_letter
    ats_report    text,                            -- rendered ATS report (Package.ats_report)
    cv_docx_path  text,                            -- path/key in Storage 'packages' bucket
    created_at    timestamptz not null default now(),
    unique (user_id, job_id)
);


-- ---------------------------------------------------------------------------
-- Indexes (jobs.dedup_key already covered by its UNIQUE constraint)
-- ---------------------------------------------------------------------------
create index if not exists idx_matches_user           on public.matches(user_id);
create index if not exists idx_matches_user_status     on public.matches(user_id, status);
create index if not exists idx_search_params_user      on public.search_params(user_id);
create index if not exists idx_cvs_user                on public.cvs(user_id);


-- =============================================================================
-- Row Level Security
-- =============================================================================
-- Enable RLS everywhere (idempotent: ENABLE is a no-op if already on).
alter table public.cvs           enable row level security;
alter table public.search_params enable row level security;
alter table public.jobs          enable row level security;
alter table public.matches       enable row level security;

-- Owner-row policies for the per-user tables. Same predicate on USING (read/
-- delete visibility) and WITH CHECK (insert/update writes): a row belongs to the
-- caller iff auth.uid() = user_id. Split per command so each is explicit.

-- cvs
drop policy if exists cvs_select on public.cvs;
drop policy if exists cvs_insert on public.cvs;
drop policy if exists cvs_update on public.cvs;
drop policy if exists cvs_delete on public.cvs;
create policy cvs_select on public.cvs for select using (auth.uid() = user_id);
create policy cvs_insert on public.cvs for insert with check (auth.uid() = user_id);
create policy cvs_update on public.cvs for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy cvs_delete on public.cvs for delete using (auth.uid() = user_id);

-- search_params
drop policy if exists search_params_select on public.search_params;
drop policy if exists search_params_insert on public.search_params;
drop policy if exists search_params_update on public.search_params;
drop policy if exists search_params_delete on public.search_params;
create policy search_params_select on public.search_params for select using (auth.uid() = user_id);
create policy search_params_insert on public.search_params for insert with check (auth.uid() = user_id);
create policy search_params_update on public.search_params for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy search_params_delete on public.search_params for delete using (auth.uid() = user_id);

-- matches
drop policy if exists matches_select on public.matches;
drop policy if exists matches_insert on public.matches;
drop policy if exists matches_update on public.matches;
drop policy if exists matches_delete on public.matches;
create policy matches_select on public.matches for select using (auth.uid() = user_id);
create policy matches_insert on public.matches for insert with check (auth.uid() = user_id);
create policy matches_update on public.matches for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy matches_delete on public.matches for delete using (auth.uid() = user_id);

-- jobs: RLS is ON but there are intentionally NO per-user policies. The shared
-- pool is read/written only by the backend with the service_role key (which
-- bypasses RLS). With RLS on and no policy, every non-service role sees zero
-- rows — the pool is sealed off from the Data API. The frontend never touches
-- jobs directly.


-- =============================================================================
-- Storage: private 'packages' bucket for generated CV .docx (cv_docx_path).
--   Access is backend-only (service_role) + signed URLs handed to the client.
--   No anon/authenticated storage policies are added on purpose.
-- =============================================================================
insert into storage.buckets (id, name, public)
values ('packages', 'packages', false)
on conflict (id) do nothing;
