"""Supabase-backed implementations of the storage seams (JobStore / UserState).

Drop-in replacements for the FlatFile* impls in ``store.py``: same method
signatures, same contracts. The flat-file versions stay as the local single-user
mode; these back the multi-user product.

Security model (see supabase/migrations): the backend talks to Supabase with the
**service_role** secret key, which bypasses RLS. The per-user scoping is enforced
here in code — every UserState method filters by ``user_id`` and never reads
across users — exactly as the FlatFile contract promised. The shared ``jobs``
pool has no ``user_id`` and is read/written platform-wide.

The Anthropic-style rule applies to the Supabase key too: it is read from the
environment only and never logged.
"""

from __future__ import annotations

import os

from .models import Job, MatchResult

# Bucket for generated application kits (CV .docx). Per-user objects live under a
# ``{user_id}/`` prefix so a user's storage can be wiped on GDPR delete.
PACKAGES_BUCKET = "packages"


def make_supabase_client():
    """Build a ``supabase.Client`` from ``SUPABASE_URL`` + ``SUPABASE_SECRET_KEY``.

    The secret key (service_role) is read from the environment only and is never
    written to logs. Raises a clear error if either variable is missing.
    """
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        raise RuntimeError(
            "Нужны SUPABASE_URL и SUPABASE_SECRET_KEY в env. "
            "Задай: export SUPABASE_URL=... ; export SUPABASE_SECRET_KEY=<service_role>"
        )
    from supabase import create_client  # lazy: keeps the dep optional for flat-file mode
    return create_client(url, key)


def _job_row(job: Job) -> dict:
    """Map a Job onto a ``jobs`` row. scraped_at/first_seen use DB defaults."""
    return {
        "dedup_key": job.dedup_key,
        "source": job.source,
        "url": job.url,
        "company": job.company,
        "title": job.title,
        "location": job.location,
        "region": job.region,
        "description": job.description,
        "date_posted": job.date_posted,
    }


# ---------------------------------------------------------------------------
# JobStore — shared vacancy pool (platform-wide, no user_id)
# ---------------------------------------------------------------------------
class SupabaseJobStore:
    """JobStore backed by the ``jobs`` table. Dedup lives in the UNIQUE
    constraint on ``dedup_key``; the client is injected for testability."""

    def __init__(self, client):
        self.client = client

    def has_seen(self, dedup_key: str) -> bool:
        res = (
            self.client.table("jobs")
            .select("id")
            .eq("dedup_key", dedup_key)
            .limit(1)
            .execute()
        )
        return bool(res.data)

    def save(self, jobs: list) -> None:
        # Dedup-on-insert: upsert ignoring rows whose dedup_key already exists.
        rows = [_job_row(j) for j in jobs if j.dedup_key]
        if not rows:
            return
        (
            self.client.table("jobs")
            .upsert(rows, on_conflict="dedup_key", ignore_duplicates=True)
            .execute()
        )


# ---------------------------------------------------------------------------
# UserState — per-user personal state (every method scoped by user_id)
# ---------------------------------------------------------------------------
class SupabaseUserState:
    """UserState backed by ``processed_jobs`` + ``matches`` (and, on delete,
    ``search_params`` / ``cvs`` / the packages bucket). The client is injected."""

    def __init__(self, client):
        self.client = client

    def is_processed(self, user_id: str, dedup_key: str) -> bool:
        res = (
            self.client.table("processed_jobs")
            .select("id")
            .eq("user_id", user_id)
            .eq("dedup_key", dedup_key)
            .limit(1)
            .execute()
        )
        return bool(res.data)

    def mark_processed(self, user_id: str, dedup_key: str) -> None:
        # Upsert with do-nothing on conflict: marking twice is a no-op.
        (
            self.client.table("processed_jobs")
            .upsert(
                {"user_id": user_id, "dedup_key": dedup_key},
                on_conflict="user_id,dedup_key",
                ignore_duplicates=True,
            )
            .execute()
        )

    def _resolve_job_id(self, job: Job) -> str:
        """Find the shared-pool job id by dedup_key; insert the job first if the
        pool has never seen it (a match always needs a job to point at)."""
        res = (
            self.client.table("jobs")
            .select("id")
            .eq("dedup_key", job.dedup_key)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]["id"]
        ins = self.client.table("jobs").upsert(
            _job_row(job), on_conflict="dedup_key"
        ).execute()
        if ins.data:
            return ins.data[0]["id"]
        # Lost an upsert race: the row exists now, fetch it.
        again = (
            self.client.table("jobs")
            .select("id")
            .eq("dedup_key", job.dedup_key)
            .limit(1)
            .execute()
        )
        return again.data[0]["id"]

    def save_application(self, user_id: str, job: Job, res: MatchResult, folder: str) -> None:
        """Persist a generated kit as a ``matches`` row. ``folder`` is a local
        filesystem path — ignored in the DB. ``cv_docx_path`` / ``ats_report``
        stay null here; the docx is wired in at the FastAPI layer."""
        job_id = self._resolve_job_id(job)
        row = {
            "user_id": user_id,
            "job_id": job_id,
            "status": "GENERATED",
            "fit_score": res.fit_score,
            "b2b_eligible": res.b2b,
            "cover_letter": res.cover_letter,
            "analysis": {
                "reason": res.reason,
                "jd_keywords": res.jd_keywords,
                "ats_present": res.ats_present,
                "ats_missing": res.ats_missing,
                "tailored_summary": res.tailored_summary,
                "tailored_skills": res.tailored_skills,
                "gaps": res.gaps,
                "recruiter_verdict": res.recruiter_verdict,
            },
        }
        (
            self.client.table("matches")
            .upsert(row, on_conflict="user_id,job_id")
            .execute()
        )

    def delete_user_data(self, user_id: str) -> None:
        """GDPR delete: wipe every per-user row (matches, processed_jobs,
        search_params, cvs) and the user's objects in the packages bucket. The
        shared ``jobs`` pool is left untouched — it is not user data."""
        for table in ("matches", "processed_jobs", "search_params", "cvs"):
            self.client.table(table).delete().eq("user_id", user_id).execute()
        self._delete_storage(user_id)

    def _delete_storage(self, user_id: str) -> None:
        """Remove the user's objects from the packages bucket (prefix user_id/)."""
        bucket = self.client.storage.from_(PACKAGES_BUCKET)
        objects = bucket.list(user_id) or []
        paths = [f"{user_id}/{o['name']}" for o in objects if o.get("name")]
        if paths:
            bucket.remove(paths)

    def list_applications(self, user_id: str) -> list[dict]:
        """The user's matches as a list of dicts (for the UI)."""
        res = (
            self.client.table("matches")
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )
        return res.data or []
