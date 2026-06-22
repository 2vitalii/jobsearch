"""Integration tests for matches list + detail (GET /matches, /matches/{id}).

SKIPPED by default, same gate as the other Supabase integration tests: needs the
``supabase`` + ``fastapi`` packages AND a live project (SUPABASE_URL +
SUPABASE_SECRET_KEY in env).

No scrape/LLM here — we seed directly: a job in the pool, a tiny .docx in the
packages bucket, and a matches row pointing at both. Teardown deletes the auth
user (cascade clears the match), removes the Storage object, and clears test_ jobs.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("supabase")
pytest.importorskip("fastapi")

if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY")
        and os.environ.get("SUPABASE_ANON_KEY")):
    pytest.skip(
        "needs SUPABASE_URL + SUPABASE_SECRET_KEY + SUPABASE_ANON_KEY (live project)",
        allow_module_level=True,
    )

from fastapi.testclient import TestClient

from api.main import app
from jobsearch.supabase_store import make_supabase_client

DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TINY_DOCX = b"PK\x03\x04 tiny placeholder docx bytes"  # content is irrelevant to signing

ANALYSIS = {
    "reason": "strong match",
    "jd_keywords": ["sql", "support"],
    "ats_present": ["sql"],
    "ats_missing": [],
    "tailored_summary": "Support engineer.",
    "tailored_skills": ["Technical Support: SQL"],
    "gaps": "none",
    "recruiter_verdict": "shortlist",
}


@pytest.fixture(scope="module")
def tc():
    return TestClient(app)


@pytest.fixture()
def seeded():
    """A user with one seeded match (+ job in pool + docx in storage)."""
    admin = make_supabase_client()
    email = f"test_{uuid.uuid4().hex}@example.com"
    password = uuid.uuid4().hex
    uid = admin.auth.admin.create_user(
        {"email": email, "password": password, "email_confirm": True}
    ).user.id
    signer = make_supabase_client()
    token = signer.auth.sign_in_with_password({"email": email, "password": password}).session.access_token

    dedup_key = f"test_{uuid.uuid4().hex}"
    job_id = admin.table("jobs").upsert(
        {"dedup_key": dedup_key, "source": "LinkedIn", "url": "https://x/1",
         "company": "Acme", "title": "Technical Support Engineer",
         "location": "Remote", "region": "WORLDWIDE", "description": "remote role"},
        on_conflict="dedup_key",
    ).execute().data[0]["id"]

    cv_path = f"{uid}/test.docx"
    admin.storage.from_("packages").upload(
        path=cv_path, file=TINY_DOCX,
        file_options={"content-type": DOCX_CT, "upsert": "true"},
    )

    match_id = admin.table("matches").upsert(
        {"user_id": uid, "job_id": job_id, "status": "GENERATED", "fit_score": 88,
         "b2b_eligible": "yes", "analysis": ANALYSIS, "cover_letter": "Dear team",
         "ats_report": "# ATS report", "cv_docx_path": cv_path,
         "job_title": "Technical Support Engineer", "job_company": "Acme",
         "job_url": "https://x/1", "job_region": "WORLDWIDE"},
        on_conflict="user_id,job_id",
    ).execute().data[0]["id"]

    yield {"uid": uid, "token": token, "match_id": match_id, "dedup_key": dedup_key,
           "cv_path": cv_path, "admin": admin}

    try:
        admin.storage.from_("packages").remove([cv_path])
    except Exception:
        pass
    admin.auth.admin.delete_user(uid)  # cascade removes the match
    admin.table("jobs").delete().eq("dedup_key", dedup_key).execute()


def _auth(token) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_list_requires_token(tc):
    assert tc.get("/matches").status_code == 401


def test_list_returns_user_match_with_denormalized_job(tc, seeded):
    r = tc.get("/matches", headers=_auth(seeded["token"]))
    assert r.status_code == 200, r.text
    rows = r.json()
    ours = [m for m in rows if m["id"] == seeded["match_id"]]
    assert len(ours) == 1
    job = ours[0]["job"]
    assert job["title"] == "Technical Support Engineer"
    assert job["company"] == "Acme"


def test_detail_returns_fields_and_signed_url(tc, seeded):
    r = tc.get(f"/matches/{seeded['match_id']}", headers=_auth(seeded["token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "GENERATED"
    assert body["fit_score"] == 88
    assert body["analysis"]["recruiter_verdict"] == "shortlist"
    assert body["cover_letter"] == "Dear team"
    assert body["job"]["company"] == "Acme"
    assert body["signed_cv_url"]  # non-empty signed download URL
    assert seeded["cv_path"] in body["signed_cv_url"]
    # Attribution seam (0009): run_id is present in the response (may be None for
    # rows seeded before the migration added the column, but the field must exist).
    assert "run_id" in body


def test_detail_404_for_other_id(tc, seeded):
    r = tc.get(f"/matches/{uuid.uuid4()}", headers=_auth(seeded["token"]))
    assert r.status_code == 404
