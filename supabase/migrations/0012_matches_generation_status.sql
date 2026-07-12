-- APPLY BY HAND in Supabase SQL Editor, migration 0012
-- Adds generation_status column to matches table to track async package generation.
--
-- Apply AFTER 0011. Idempotent (uses ADD COLUMN IF NOT EXISTS).
-- UNIQUE(user_id, job_id) already exists (0001) — not re-added here.
-- cover_letter/cv_docx_path are already nullable (0001) — not changed here.
-- signed_cv_url is computed (not a column) — not in this migration.
-- RLS policies on matches are unchanged.

alter table public.matches
    add column if not exists generation_status text not null default 'none'
    check (generation_status in ('none', 'generating', 'done', 'failed'));
