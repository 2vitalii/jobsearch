"""Integration tests for SearchParams CRUD (PUT/GET /search-params).

SKIPPED by default, same gate as the other Supabase integration tests: needs the
``supabase`` + ``fastapi`` packages AND a live project (SUPABASE_URL +
SUPABASE_SECRET_KEY in env). A throwaway auth user is minted on dedicated clients
(see test_api_auth) and deleted in teardown — the cascade removes the row.
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


@pytest.fixture(scope="module")
def tc():
    return TestClient(app)


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
    signed = signer.auth.sign_in_with_password({"email": email, "password": password})
    token = signed.session.access_token
    yield {"user_id": uid, "email": email, "token": token}
    admin.auth.admin.delete_user(uid)  # cascade removes the search_params row


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


def test_put_requires_token(tc):
    r = tc.put("/search-params", json={"keywords": ["x"], "locations": ["y"]})
    assert r.status_code == 401


def test_get_404_when_absent(tc, auth_user):
    r = tc.get("/search-params", headers=_auth(auth_user))
    assert r.status_code == 404


def test_put_persists_and_get_returns_same(tc, auth_user):
    payload = {
        "keywords": ["support engineer", "integration"],
        "locations": ["EU", "Worldwide"],
        "period_hours": 72,
        "work_format": "remote",
        "loose": True,
        "targeted": False,
    }
    r = tc.put("/search-params", json=payload, headers=_auth(auth_user))
    assert r.status_code == 200, r.text
    assert r.json() == payload

    # row really landed
    sb = make_supabase_client()
    row = (
        sb.table("search_params")
        .select("keywords, locations, period_hours, work_format, loose, targeted")
        .eq("user_id", auth_user["user_id"]).single().execute()
    )
    assert row.data == payload

    g = tc.get("/search-params", headers=_auth(auth_user))
    assert g.status_code == 200
    assert g.json() == payload


def test_put_defaults_match_model(tc, auth_user):
    # Only required-ish lists provided; the rest fall back to SearchParams defaults.
    r = tc.put("/search-params", json={"keywords": ["a"], "locations": ["b"]},
               headers=_auth(auth_user))
    assert r.status_code == 200
    body = r.json()
    assert body["period_hours"] == 168
    assert body["work_format"] == "remote"
    assert body["loose"] is False
    assert body["targeted"] is False


def test_put_updates_in_place(tc, auth_user):
    tc.put("/search-params", json={"keywords": ["a"], "locations": ["b"]},
           headers=_auth(auth_user))
    tc.put("/search-params", json={"keywords": ["c"], "locations": ["d"], "period_hours": 24},
           headers=_auth(auth_user))

    g = tc.get("/search-params", headers=_auth(auth_user))
    assert g.json()["keywords"] == ["c"]
    assert g.json()["period_hours"] == 24

    # exactly one row for this user — upsert updated, did not duplicate
    sb = make_supabase_client()
    rows = sb.table("search_params").select("id").eq("user_id", auth_user["user_id"]).execute()
    assert len(rows.data) == 1
