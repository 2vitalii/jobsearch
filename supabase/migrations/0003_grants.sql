-- =============================================================================
-- 0003_grants.sql — table privileges for the backend role (service_role).
--
-- HOW TO APPLY
--   • Supabase SQL Editor: paste this whole file and run it, OR
--   • CLI:  supabase db push        (picks up files in supabase/migrations/)
--
-- Idempotent: GRANT is a no-op if the privilege is already held.
--
-- WHY THIS IS NEEDED
--   This project was created with "Automatically expose new tables" turned OFF,
--   so the tables added in 0001/0002 received NO grants for any Data API role —
--   including service_role, which is the role our backend's secret key runs as.
--
--   RLS-bypass is NOT a table grant: service_role skips Row Level Security, but
--   it still needs the underlying SQL privilege (SELECT/INSERT/UPDATE/DELETE) on
--   the table. Without an explicit grant the backend gets "permission denied for
--   table ..." even though RLS would have let the row through.
--
--   So we grant ONLY service_role here. We deliberately do NOT grant anon or
--   authenticated — the product is backend-mediated and the frontend must not
--   reach these tables directly (it talks to Auth only).
-- =============================================================================

grant select, insert, update, delete on public.jobs           to service_role;
grant select, insert, update, delete on public.cvs            to service_role;
grant select, insert, update, delete on public.search_params  to service_role;
grant select, insert, update, delete on public.matches        to service_role;
grant select, insert, update, delete on public.processed_jobs to service_role;

-- Future tables in `public` should open up to the backend automatically (but
-- still NOT to anon/authenticated — they are omitted here on purpose).
alter default privileges in schema public
    grant select, insert, update, delete on tables to service_role;
