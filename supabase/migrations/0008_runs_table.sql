-- =============================================================================
-- 0008_runs_table.sql — per-user run history and live progress tracking.
--
-- HOW TO APPLY
--   • Supabase SQL Editor: paste this whole file and run it, OR
--   • CLI:  supabase db push        (picks up files in supabase/migrations/)
--
-- Idempotent: re-running is safe (IF NOT EXISTS guards, DROP POLICY IF EXISTS
-- before CREATE POLICY).
--
-- WHY (SG-03)
--   POST /run was synchronous-blocking (~250 LLM calls inline → timeout + cost
--   spiral). Converting to BackgroundTasks requires a status row so clients can
--   poll for progress. Each row is ONE run; rows accumulate as history — nothing
--   is overwritten across runs. The `updated_at` column serves as a heartbeat:
--   the background task bumps it on every progress update so a liveness probe
--   can detect stale/orphaned runs.
--
-- Access model
--   • STATUS READS (GET /run/{id}, GET /run/latest): user-scoped client (RLS
--     active, authenticated role) — `authenticated` gets SELECT only.
--   • ALL WRITES (INSERT of the initial row, UPDATE during the loop, orphan
--     cleanup on startup): service_role client, filtering by user_id in code.
--     No authenticated write path exists by design — see "Writes are
--     service_role only" note below.
--   Only `authenticated` SELECT is granted below; INSERT/UPDATE/DELETE stay
--   with service_role (no `authenticated` grant for writes), which is
--   intentional.
-- =============================================================================

create table if not exists public.runs (
    id              uuid        primary key default gen_random_uuid(),
    user_id         uuid        not null references auth.users(id) on delete cascade,
    status          text        not null check (status in ('running', 'done', 'failed')),
    scraped         int         not null default 0,
    processed       int         not null default 0,
    generated       int         not null default 0,
    skipped_low_fit int         not null default 0,
    error           text,
    summary         jsonb,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create index if not exists idx_runs_user_id    on public.runs(user_id);
create index if not exists idx_runs_user_status on public.runs(user_id, status);
create index if not exists idx_runs_created_at  on public.runs(user_id, created_at desc);

-- BLOCKER 1: Partial unique index — enforces one 'running' row per user at
-- the database level. This is the authoritative concurrency guard: even two
-- simultaneous POST /run requests that both pass the pre-check SELECT cannot
-- both succeed on INSERT because the second one will trip this index and raise
-- a unique-violation error (PostgreSQL error code 23505). Historical rows with
-- status='done' or status='failed' are excluded from the index so they
-- accumulate as full run history.
create unique index if not exists idx_runs_one_active_per_user
    on public.runs (user_id)
    where status = 'running';

-- RLS: owner-row read-only policy.
--
-- Writes (INSERT/UPDATE/DELETE) are intentionally service_role ONLY.
-- The background task runs after the HTTP response and has no user JWT, so it
-- must use service_role. The initial INSERT (POST /run handler) also uses
-- service_role so it can rely on the partial unique index as the authoritative
-- concurrency guard. The startup orphan-cleanup sweep is likewise service_role.
--
-- IMPORTANT: We define ONLY the SELECT policy here. There are NO
-- runs_insert / runs_update / runs_delete policies for `authenticated`.
-- Defining write policies without the corresponding GRANT would be dead
-- code today, but could become a privilege-escalation vector if someone
-- accidentally adds a GRANT in the future. Keeping write policies absent
-- makes the intent unambiguous: the authenticated role has zero write
-- access regardless of any future GRANT changes.
alter table public.runs enable row level security;

drop policy if exists runs_select on public.runs;
drop policy if exists runs_insert on public.runs;
drop policy if exists runs_update on public.runs;
drop policy if exists runs_delete on public.runs;
create policy runs_select on public.runs for select using (auth.uid() = user_id);

-- Grant SELECT to authenticated for the status-polling GET endpoints.
-- INSERT/UPDATE/DELETE are NOT granted: the background task and handler use
-- service_role (which bypasses RLS entirely), so no `authenticated` grant for
-- writes is needed or wanted.
grant usage on schema public to authenticated;
grant select on public.runs to authenticated;
