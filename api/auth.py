"""Request authentication.

``get_current_user`` reads the ``Authorization: Bearer <token>`` header and
validates it against Supabase. A missing or invalid token yields 401. A local
JWKS check (no round-trip) can replace this later without touching call sites.

Why a stateless HTTP call and not ``supabase.auth.get_user(token)``: the GoTrue
client mutates its own auth state on ``get_user`` / ``sign_in`` — calling it on
our shared service_role client would downgrade that singleton to the caller's
session (losing service_role, risking cross-user access under concurrency). So we
verify the token with a stateless GET /auth/v1/user that touches no shared client.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx
from fastapi import Header, HTTPException, status


@dataclass
class CurrentUser:
    """The authenticated caller, as resolved from the bearer token.

    ``token`` is the caller's verified access token (JWT). It is carried so the
    per-user path can build a user-scoped Supabase client (RLS applies). It is a
    short-lived bearer credential — never log it, never return it in a response.
    """
    user_id: str
    email: str
    token: str


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def _verify_token(token: str) -> dict | None:
    """Resolve the user for a bearer token via a stateless GET /auth/v1/user.
    Returns the user dict on success, ``None`` if the token is rejected. The
    project ``apikey`` is the backend secret (server-side only, never logged)."""
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        raise RuntimeError(
            "Нужны SUPABASE_URL и SUPABASE_SECRET_KEY в env для проверки токена."
        )
    resp = httpx.get(
        f"{url}/auth/v1/user",
        headers={"apikey": key, "Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if resp.status_code != 200:
        return None
    return resp.json()


def get_current_user(
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    # Validate the bearer token is present BEFORE any network call, so a missing
    # token is a clean 401 and never depends on the auth backend being reachable.
    token = _bearer_token(authorization)
    user = _verify_token(token)
    if user is None or not user.get("id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return CurrentUser(user_id=user["id"], email=user.get("email") or "", token=token)
