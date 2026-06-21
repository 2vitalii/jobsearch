"""RLS isolation acceptance test (SG-02): prove the user-scoped client is filtered
by Row Level Security in the database, not just by application-layer .eq().

SKIPPED by default; needs the ``supabase`` package AND a live project with
SUPABASE_URL + SUPABASE_SECRET_KEY + SUPABASE_ANON_KEY, AND migrations 0006/0007
applied (grant authenticated). Offline/CI stay green.

Two users A and B, each seeded (via service_role) with a cvs row and a matches
row. The user-scoped client for A (anon key + A's JWT → role `authenticated`)
must:
  (a) SELECT without any .eq("user_id") and get ONLY A's rows — RLS hid B's;
  (b) SELECT B's row explicitly by id and get nothing.
If RLS were not actually enforcing isolation (e.g. the path silently ran as
service_role), A would see B's rows and these tests FAIL — that is the point.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("supabase")

if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY")
        and os.environ.get("SUPABASE_ANON_KEY")):
    pytest.skip(
        "needs SUPABASE_URL + SUPABASE_SECRET_KEY + SUPABASE_ANON_KEY (live project, "
        "migrations 0006/0007 applied)",
        allow_module_level=True,
    )

from jobsearch.supabase_store import make_supabase_client, make_user_client


def _mk_user(admin):
    email = f"test_{uuid.uuid4().hex}@example.com"
    password = uuid.uuid4().hex
    uid = admin.auth.admin.create_user(
        {"email": email, "password": password, "email_confirm": True}
    ).user.id
    signer = make_supabase_client()
    token = signer.auth.sign_in_with_password(
        {"email": email, "password": password}
    ).session.access_token
    return uid, token


@pytest.fixture()
def two_users():
    admin = make_supabase_client()
    a_uid, a_tok = _mk_user(admin)
    b_uid, b_tok = _mk_user(admin)

    # one shared job for the matches rows (shared pool — fine)
    dedup = f"test_{uuid.uuid4().hex}"
    job_id = admin.table("jobs").upsert(
        {"dedup_key": dedup, "source": "x", "url": "https://x/1", "company": "Acme",
         "title": "Support Engineer", "location": "Remote", "region": "WORLDWIDE",
         "description": "remote"},
        on_conflict="dedup_key",
    ).execute().data[0]["id"]

    def seed(uid, tag):
        cv = admin.table("cvs").upsert(
            {"user_id": uid, "markdown": f"{tag} CV", "short_profile": tag},
            on_conflict="user_id",
        ).execute().data[0]["id"]
        m = admin.table("matches").upsert(
            {"user_id": uid, "job_id": job_id, "status": "GENERATED", "fit_score": 50,
             "job_title": "Support Engineer", "job_company": "Acme"},
            on_conflict="user_id,job_id",
        ).execute().data[0]["id"]
        return cv, m

    a_cv, a_m = seed(a_uid, "A")
    b_cv, b_m = seed(b_uid, "B")

    yield {"a_uid": a_uid, "a_tok": a_tok, "a_cv": a_cv, "a_m": a_m,
           "b_uid": b_uid, "b_tok": b_tok, "b_cv": b_cv, "b_m": b_m}

    admin.auth.admin.delete_user(a_uid)  # cascade clears cvs/matches
    admin.auth.admin.delete_user(b_uid)
    admin.table("jobs").delete().eq("dedup_key", dedup).execute()


# ── cvs ──────────────────────────────────────────────────────────────────────

def test_user_scoped_cvs_select_returns_only_own(two_users):
    ca = make_user_client(two_users["a_tok"])
    rows = ca.table("cvs").select("id, user_id").execute().data or []  # NO .eq
    ids = {r["id"] for r in rows}
    assert two_users["a_cv"] in ids, "A must see its own cv (grant+RLS allow own row)"
    assert two_users["b_cv"] not in ids, "RLS LEAK: A saw B's cv row"
    assert all(r["user_id"] == two_users["a_uid"] for r in rows), "RLS LEAK: foreign user_id present"


def test_user_scoped_cvs_cannot_read_other_by_id(two_users):
    ca = make_user_client(two_users["a_tok"])
    rows = ca.table("cvs").select("id").eq("id", two_users["b_cv"]).execute().data or []
    assert rows == [], "RLS LEAK: A read B's cv by explicit id"


# ── matches ──────────────────────────────────────────────────────────────────

def test_user_scoped_matches_select_returns_only_own(two_users):
    ca = make_user_client(two_users["a_tok"])
    rows = ca.table("matches").select("id, user_id").execute().data or []  # NO .eq
    ids = {r["id"] for r in rows}
    assert two_users["a_m"] in ids, "A must see its own match"
    assert two_users["b_m"] not in ids, "RLS LEAK: A saw B's match row"
    assert all(r["user_id"] == two_users["a_uid"] for r in rows), "RLS LEAK: foreign user_id present"


def test_user_scoped_matches_cannot_read_other_by_id(two_users):
    ca = make_user_client(two_users["a_tok"])
    rows = ca.table("matches").select("id").eq("id", two_users["b_m"]).execute().data or []
    assert rows == [], "RLS LEAK: A read B's match by explicit id"


# ── jobs stays closed to authenticated ───────────────────────────────────────

def test_authenticated_cannot_read_jobs_pool(two_users):
    """jobs has no grant to authenticated + RLS-closed → a user-scoped client must
    NOT be able to read the shared pool (permission denied or empty)."""
    ca = make_user_client(two_users["a_tok"])
    try:
        rows = ca.table("jobs").select("id").limit(5).execute().data or []
    except Exception:
        rows = []  # permission denied is the expected closed behavior
    assert rows == [], "jobs pool must not be readable by an authenticated user"
