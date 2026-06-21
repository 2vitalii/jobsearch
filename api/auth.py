"""Request authentication.

``get_current_user`` reads the ``Authorization: Bearer <token>`` header and
validates the token against Supabase (``auth.get_user(token)``). A missing or
invalid token yields 401. We verify server-side via Supabase for now; a local
JWKS check (no round-trip) can replace this later without touching call sites.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException, status

from .deps import get_supabase


@dataclass
class CurrentUser:
    """The authenticated caller, as resolved from the bearer token."""
    user_id: str
    email: str


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


def get_current_user(
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    # Validate the bearer token is present BEFORE touching Supabase, so a missing
    # token is a clean 401 and never depends on the auth backend being reachable.
    token = _bearer_token(authorization)
    supabase = get_supabase()
    try:
        resp = supabase.auth.get_user(token)
    except Exception:
        resp = None
    user = getattr(resp, "user", None)
    if user is None or not getattr(user, "id", None):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return CurrentUser(user_id=user.id, email=getattr(user, "email", "") or "")
