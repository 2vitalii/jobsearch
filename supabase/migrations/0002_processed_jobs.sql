-- =============================================================================
-- 0002_processed_jobs.sql — per-user "already processed" log (was .processed_urls.txt).
--
-- HOW TO APPLY
--   • Supabase SQL Editor: paste this whole file and run it, OR
--   • CLI:  supabase db push        (picks up files in supabase/migrations/)
--
-- Idempotent: re-running is safe (IF NOT EXISTS guards, DROP POLICY IF EXISTS
-- before CREATE POLICY).
--
-- Backs UserState.is_processed / mark_processed (jobsearch/store.py). Per-user:
-- every row is scoped by user_id, dedup_key comes from filters.compute_dedup_key.
-- =============================================================================

create extension if not exists pgcrypto;

create table if not exists public.processed_jobs (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references auth.users(id) on delete cascade,
    dedup_key    text not null,                  -- filters.compute_dedup_key
    processed_at timestamptz not null default now(),
    unique (user_id, dedup_key)
);

create index if not exists idx_processed_jobs_user on public.processed_jobs(user_id);

-- RLS: owner-row policies (defense-in-depth; backend uses service_role anyway).
alter table public.processed_jobs enable row level security;

drop policy if exists processed_jobs_select on public.processed_jobs;
drop policy if exists processed_jobs_insert on public.processed_jobs;
drop policy if exists processed_jobs_update on public.processed_jobs;
drop policy if exists processed_jobs_delete on public.processed_jobs;
create policy processed_jobs_select on public.processed_jobs for select using (auth.uid() = user_id);
create policy processed_jobs_insert on public.processed_jobs for insert with check (auth.uid() = user_id);
create policy processed_jobs_update on public.processed_jobs for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy processed_jobs_delete on public.processed_jobs for delete using (auth.uid() = user_id);
