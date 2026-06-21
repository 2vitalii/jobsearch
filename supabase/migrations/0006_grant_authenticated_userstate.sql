-- =============================================================================
-- 0006_grant_authenticated_userstate.sql — activate RLS as a real second layer.
--
-- HOW TO APPLY
--   • Supabase SQL Editor: paste this whole file and run it, OR
--   • CLI:  supabase db push        (picks up files in supabase/migrations/)
--
-- Idempotent: GRANT is a no-op if the privilege is already held.
--
-- WHY (SG-02)
--   The per-user tables already have RLS enabled + owner policies (auth.uid() =
--   user_id) from 0001/0002, but every request runs under service_role, which
--   BYPASSES RLS — so the policies never fire. To make RLS a genuine second layer,
--   the backend now also has a user-scoped path: a client on the anon key with the
--   caller's verified JWT attached, which runs as the `authenticated` role.
--
--   `authenticated` is a normal role subject to RLS, but it needs table privileges
--   to reach the tables at all. This grant gives it exactly that — gated by the
--   EXISTING restrictive policies (no new policy is added here). A user can then
--   touch ONLY their own rows; insert/update are bounded by the WITH CHECK
--   (auth.uid() = user_id) policies.
--
--   `jobs` is deliberately NOT granted to `authenticated`: the shared pool stays
--   reachable only via service_role. With RLS on and no policy, `authenticated`
--   sees zero rows AND has no table privilege — doubly closed. The user-scoped
--   path must therefore never read `jobs` (match rows carry denormalized job
--   fields instead — see 0007).
-- =============================================================================

-- authenticated needs schema usage to reference these tables (no-op if present).
grant usage on schema public to authenticated;

grant select, insert, update, delete on public.cvs            to authenticated;
grant select, insert, update, delete on public.search_params  to authenticated;
grant select, insert, update, delete on public.matches        to authenticated;
grant select, insert, update, delete on public.processed_jobs to authenticated;

-- NOTE: public.jobs is intentionally omitted — authenticated must not reach it.
