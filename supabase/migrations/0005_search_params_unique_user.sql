-- =============================================================================
-- 0005_search_params_unique_user.sql — one saved search per user.
--
-- HOW TO APPLY
--   • Supabase SQL Editor: paste this whole file and run it, OR
--   • CLI:  supabase db push        (picks up files in supabase/migrations/)
--
-- Idempotent: the UNIQUE constraint is added only if it is not already present.
--
-- WHY
--   The product keeps a single saved search per user (mirrors models.SearchParams),
--   so the CRUD endpoint can upsert on user_id. This adds the UNIQUE(user_id) that
--   on_conflict='user_id' relies on.
-- =============================================================================

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'search_params_user_id_key'
    ) then
        alter table public.search_params add constraint search_params_user_id_key unique (user_id);
    end if;
end $$;
