"""Integration tests for the FastAPI auth skeleton (/health, /me).

SKIPPED by default, same gate as the other Supabase integration tests: needs the
``supabase`` and ``fastapi`` packages AND a live project (SUPABASE_URL +
SUPABASE_SECRET_KEY in env). Plain ``pytest`` and CI stay green offline.

Mints a throwaway confirmed auth user, signs in for a real access token, and
checks the bearer flow end-to-end. Teardown deletes the user.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("supabase")
pytest.importorskip("fastapi")

if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY")):
    pytest.skip(
        "needs SUPABASE_URL + SUPABASE_SECRET_KEY (live project)",
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
    # Dedicated clients, NOT the app's get_supabase() singleton: GoTrue mutates a
    # client's auth state on sign_in/get_user, so the admin lifecycle (create /
    # delete) must run on a client that nothing else downgrades. Signing in uses a
    # throwaway client so it never pollutes the admin one.
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
    admin.auth.admin.delete_user(uid)  # cascade removes the user's rows


def test_health_is_open(tc):
    r = tc.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_me_requires_token(tc):
    r = tc.get("/me")
    assert r.status_code == 401


def test_me_with_bearer_returns_identity(tc, auth_user):
    r = tc.get("/me", headers={"Authorization": f"Bearer {auth_user['token']}"})
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == auth_user["user_id"]
    assert body["email"] == auth_user["email"]
