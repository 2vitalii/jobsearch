"""Integration tests for the Supabase JobStore / UserState impls.

SKIPPED by default: they need the ``supabase`` package AND a live project
(SUPABASE_URL + SUPABASE_SECRET_KEY in env). Plain ``pytest`` and CI stay green
without network or keys — nothing here runs unless a project is configured.

Per-user tables (matches/processed_jobs/cvs) FK into auth.users, so setup mints a
throwaway auth user via the admin API and tears it down (cascade cleans the rows).
Shared-pool jobs are prefixed ``test_`` and removed in teardown.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("supabase")

if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY")):
    pytest.skip(
        "needs SUPABASE_URL + SUPABASE_SECRET_KEY (live project)",
        allow_module_level=True,
    )

from jobsearch.models import Job, MatchResult
from jobsearch.supabase_store import (
    SupabaseJobStore,
    SupabaseUserState,
    make_supabase_client,
)


def _job(key: str) -> Job:
    return Job(
        dedup_key=key, source="LinkedIn", url="https://x/" + key, company="Acme",
        title="Support Engineer", location="Remote", region="WORLDWIDE",
        description="d", date_posted="2026-06-20",
    )


def _res() -> MatchResult:
    return MatchResult(
        fit_score=72, b2b="yes", reason="r", jd_keywords=["sql"], ats_present=["a"],
        ats_missing=["b"], tailored_summary="s", tailored_skills=["x"], gaps="g",
        recruiter_verdict="v", cover_letter="cover",
    )


@pytest.fixture(scope="module")
def client():
    return make_supabase_client()


@pytest.fixture()
def user_id(client):
    email = f"test_{uuid.uuid4().hex}@example.com"
    created = client.auth.admin.create_user(
        {"email": email, "password": uuid.uuid4().hex, "email_confirm": True}
    )
    uid = created.user.id
    yield uid
    client.auth.admin.delete_user(uid)  # cascade removes the user's rows


@pytest.fixture()
def job_key():
    key = f"test_{uuid.uuid4().hex}"
    yield key


@pytest.fixture()
def jobstore(client):
    created_keys: list[str] = []
    store = SupabaseJobStore(client)
    yield store, created_keys
    if created_keys:
        client.table("jobs").delete().in_("dedup_key", created_keys).execute()


def test_save_then_has_seen(jobstore, job_key):
    store, created = jobstore
    created.append(job_key)
    assert store.has_seen(job_key) is False
    store.save([_job(job_key)])
    assert store.has_seen(job_key) is True


def test_save_is_dedup(jobstore, job_key):
    store, created = jobstore
    created.append(job_key)
    store.save([_job(job_key)])
    store.save([_job(job_key)])  # no duplicate row
    res = store.client.table("jobs").select("id").eq("dedup_key", job_key).execute()
    assert len(res.data) == 1


def test_mark_processed_roundtrip(client, user_id):
    state = SupabaseUserState(client)
    key = f"test_{uuid.uuid4().hex}"
    assert state.is_processed(user_id, key) is False
    state.mark_processed(user_id, key)
    assert state.is_processed(user_id, key) is True
    state.mark_processed(user_id, key)  # idempotent
    assert state.is_processed(user_id, key) is True


def test_save_application_then_list(client, user_id, jobstore):
    _store, created = jobstore
    state = SupabaseUserState(client)
    key = f"test_{uuid.uuid4().hex}"
    created.append(key)
    state.save_application(user_id, _job(key), _res(), "review/072_Acme_Support")
    apps = state.list_applications(user_id)
    assert len(apps) == 1
    assert apps[0]["status"] == "GENERATED"
    assert apps[0]["fit_score"] == 72
    assert apps[0]["analysis"]["recruiter_verdict"] == "v"


def test_delete_user_data_clears_rows(client, user_id, jobstore):
    _store, created = jobstore
    state = SupabaseUserState(client)
    key = f"test_{uuid.uuid4().hex}"
    created.append(key)
    state.mark_processed(user_id, key)
    state.save_application(user_id, _job(key), _res(), "folder")

    state.delete_user_data(user_id)

    assert state.is_processed(user_id, key) is False
    assert state.list_applications(user_id) == []
