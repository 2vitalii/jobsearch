"""Integration tests for the Run endpoint (POST /run).

SKIPPED by default, same gate as the other Supabase integration tests: needs the
``supabase`` + ``fastapi`` packages AND a live project (SUPABASE_URL +
SUPABASE_SECRET_KEY in env).

Neither the real scraper nor the real LLM is called: ``get_scraper`` is overridden
with a fake returning canonical Jobs that pass the filters, and ``get_llm`` with a
deterministic FakeLLM returning one rich JSON (high fit) that satisfies both
PreScore and MatchResult. So the full loop runs end-to-end, free and stable.

Teardown: delete the auth user (cascade clears matches/processed_jobs/cvs/
search_params), remove the user's Storage objects, and clear test_* jobs.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

pytest.importorskip("supabase")
pytest.importorskip("fastapi")
pytest.importorskip("docx")

if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY")):
    pytest.skip(
        "needs SUPABASE_URL + SUPABASE_SECRET_KEY (live project)",
        allow_module_level=True,
    )

from fastapi.testclient import TestClient

from api.main import app
from api.deps import get_llm, get_scraper
from jobsearch.models import Job
from jobsearch.supabase_store import make_supabase_client

SEED_MD = (
    "# Jane Doe\n"
    "Support Engineer\n"
    "EU (Remote) · jane@example.com\n\n"
    "## Professional Summary\nSupport engineer with 2 years experience.\n\n"
    "## Core Skills\n- Technical Support: troubleshooting, SQL, REST APIs\n\n"
    "## Professional Experience\n### Support Engineer — Acme\n2024 - 2026\n- Resolved tickets.\n\n"
    "## Education\n### B.Sc. IT\nSome University\n\n"
    "## Additional Information\n- Languages: English (C1)\n"
)
SEED_PROFILE = "Support engineer, 2 years, SQL/REST/MQTT, B2B via sole proprietor."

# One rich JSON that satisfies PreScore.from_dict AND MatchResult.from_dict.
RICH = {
    "fit_score": 88,
    "b2b_eligible": "yes",
    "reason": "strong match",
    "jd_keywords": ["sql", "rest api", "support"],
    "ats_present": ["sql", "support"],
    "ats_missing": [],
    "tailored_summary": "Support engineer aligned to the role.",
    "tailored_skills": ["Technical Support: troubleshooting, SQL, REST APIs"],
    "gaps": "none",
    "recruiter_verdict": "shortlist",
    "cover_letter": "Dear team, I am a strong fit for this role.",
}


class FakeLLM:
    def complete(self, *, model, system, messages, max_tokens) -> str:
        return json.dumps(RICH)


def _fake_jobs() -> list[Job]:
    out = []
    for i in range(2):
        key = f"test_{uuid.uuid4().hex}"
        out.append(Job(
            dedup_key=key, source="LinkedIn", url=f"https://x/{key}",
            company=f"Acme{i}", title="Technical Support Engineer",
            location="Remote", region="WORLDWIDE",
            description="Fully remote support role. Work from anywhere. SQL, REST APIs.",
            date_posted="2026-06-20",
        ))
    return out


@pytest.fixture()
def jobs():
    return _fake_jobs()


@pytest.fixture(scope="module")
def tc():
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    client = TestClient(app)
    yield client
    app.dependency_overrides.pop(get_llm, None)


def _override_scraper(jobs):
    app.dependency_overrides[get_scraper] = lambda: (lambda params, config: jobs)


@pytest.fixture()
def auth_user():
    admin = make_supabase_client()
    email = f"test_{uuid.uuid4().hex}@example.com"
    password = uuid.uuid4().hex
    created = admin.auth.admin.create_user(
        {"email": email, "password": password, "email_confirm": True}
    )
    uid = created.user.id
    signer = make_supabase_client()
    token = signer.auth.sign_in_with_password({"email": email, "password": password}).session.access_token
    yield {"user_id": uid, "email": email, "token": token, "admin": admin}
    # remove the user's Storage objects (not cascade-deleted), then the user.
    try:
        objs = admin.storage.from_("packages").list(uid) or []
        paths = [f"{uid}/{o['name']}" for o in objs if o.get("name")]
        if paths:
            admin.storage.from_("packages").remove(paths)
    except Exception:
        pass
    admin.auth.admin.delete_user(uid)


def _seed(user):
    sb = make_supabase_client()
    sb.table("cvs").upsert(
        {"user_id": user["user_id"], "markdown": SEED_MD, "short_profile": SEED_PROFILE},
        on_conflict="user_id",
    ).execute()
    sb.table("search_params").upsert(
        {"user_id": user["user_id"], "keywords": ["support engineer"], "locations": ["Worldwide"]},
        on_conflict="user_id",
    ).execute()


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


def _cleanup_jobs(jobs):
    sb = make_supabase_client()
    keys = [j.dedup_key for j in jobs]
    if keys:
        sb.table("jobs").delete().in_("dedup_key", keys).execute()


def test_run_requires_token(tc):
    r = tc.post("/run")
    assert r.status_code == 401


def test_run_404_without_cv_or_search(tc, auth_user):
    # search params + no CV -> 404; then no search params -> 404
    sb = make_supabase_client()
    sb.table("search_params").upsert(
        {"user_id": auth_user["user_id"], "keywords": ["x"], "locations": ["y"]},
        on_conflict="user_id",
    ).execute()
    r = tc.post("/run", headers=_auth(auth_user))
    assert r.status_code == 404  # no CV yet

    sb.table("search_params").delete().eq("user_id", auth_user["user_id"]).execute()
    sb.table("cvs").upsert(
        {"user_id": auth_user["user_id"], "markdown": SEED_MD, "short_profile": SEED_PROFILE},
        on_conflict="user_id",
    ).execute()
    r2 = tc.post("/run", headers=_auth(auth_user))
    assert r2.status_code == 404  # no search params


def test_run_full_loop(tc, auth_user, jobs):
    """POST /run → 202 {run_id}; poll until 'done'; assert counters + side effects.

    Updated to the SG-03 async contract:
      POST  /run            → 202  { run_id: "<uuid>" }
      GET   /run/<run_id>   → 200  RunStatus (poll until status != 'running')
    """
    _seed(auth_user)
    _override_scraper(jobs)
    try:
        # Step 1: start the run.
        r = tc.post("/run", headers=_auth(auth_user))
        assert r.status_code == 202, r.text
        body = r.json()
        assert "run_id" in body
        run_id = body["run_id"]

        # Step 2: poll until the background task finishes.
        # TestClient runs background tasks synchronously, so a single GET
        # after the POST is sufficient; add a small retry loop for resilience.
        import time
        run_status = None
        for _ in range(20):
            rs = tc.get(f"/run/{run_id}", headers=_auth(auth_user))
            assert rs.status_code == 200, rs.text
            run_status = rs.json()
            if run_status["status"] != "running":
                break
            time.sleep(0.5)

        assert run_status is not None
        assert run_status["status"] == "done", f"run did not finish: {run_status}"
        assert run_status["scraped"] == 2
        assert run_status["processed"] == 2
        assert run_status["generated"] >= 1
        assert run_status["generated"] + run_status["skipped_low_fit"] == run_status["processed"]
        # Attribution seam (0009): search_snapshot must be present in the status response.
        assert "search_snapshot" in run_status
        assert run_status["search_snapshot"] is not None
        snapshot = run_status["search_snapshot"]
        assert "keywords" in snapshot
        assert "locations" in snapshot
        assert "period_hours" in snapshot
        assert "work_format" in snapshot
        assert "loose" in snapshot
        assert "targeted" in snapshot

        uid = auth_user["user_id"]
        sb = make_supabase_client()

        # matches: GENERATED rows with non-empty cv_docx_path
        m = sb.table("matches").select("status, cv_docx_path, fit_score").eq("user_id", uid).execute()
        assert len(m.data) == run_status["generated"]
        for row in m.data:
            assert row["status"] == "GENERATED"
            assert row["cv_docx_path"]
            assert row["fit_score"] == 88

        # processed_jobs: every queued job's dedup_key is marked processed
        p = sb.table("processed_jobs").select("dedup_key").eq("user_id", uid).execute()
        processed = {row["dedup_key"] for row in p.data}
        for j in jobs:
            assert j.dedup_key in processed

        # Storage: the .docx really exists under {user_id}/
        objs = sb.storage.from_("packages").list(uid) or []
        assert any(o.get("name", "").endswith(".docx") for o in objs)
    finally:
        app.dependency_overrides.pop(get_scraper, None)
        _cleanup_jobs(jobs)
