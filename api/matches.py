"""Matches endpoints: list a user's generated matches and view one with a
short-lived signed URL to download the tailored CV.

SECURITY — READ THIS: the backend uses the service_role key, which BYPASSES RLS.
There is no per-user database policy doing the scoping for us here, so EVERY query
in this module MUST filter by the current user's id. Selecting a match by id alone
(without ``.eq("user_id", current)``) would leak another user's data. The detail
route checks ownership by querying on (id AND user_id) and 404s otherwise.

All routes are behind ``get_current_user``. Access to matches / Storage is done in
the API layer through the injected Supabase client.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .auth import CurrentUser, get_current_user
from .deps import get_supabase

router = APIRouter(prefix="/matches", tags=["matches"])

SIGNED_URL_TTL = 300  # seconds — short-lived download link for the private .docx

# Job columns embedded via the matches.job_id FK (PostgREST resource embedding).
_SELECT = "*, jobs(title, company, url, region)"


class MatchDetail(BaseModel):
    id: str
    status: str | None = None
    fit_score: int | None = None
    b2b_eligible: str | None = None
    analysis: dict | None = None
    cover_letter: str | None = None
    ats_report: str | None = None
    job: dict | None = None
    signed_cv_url: str | None = None


def _sign_cv(supabase, path: str | None) -> str | None:
    """Short-lived signed URL for a private packages object, or None if no docx."""
    if not path:
        return None
    res = supabase.storage.from_("packages").create_signed_url(path, SIGNED_URL_TTL)
    # supabase-py returns a dict; the key has varied across versions.
    return res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")


@router.get("", response_model=list[dict])
def list_matches(
    user: CurrentUser = Depends(get_current_user),
    supabase=Depends(get_supabase),
) -> list[dict]:
    # MUST scope by user_id — service_role bypasses RLS.
    res = (
        supabase.table("matches")
        .select(_SELECT)
        .eq("user_id", user.user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data or []


@router.get("/{match_id}", response_model=MatchDetail)
def get_match(
    match_id: str,
    user: CurrentUser = Depends(get_current_user),
    supabase=Depends(get_supabase),
) -> MatchDetail:
    # Ownership check: id AND user_id. Not found OR not theirs -> 404.
    res = (
        supabase.table("matches")
        .select(_SELECT)
        .eq("id", match_id)
        .eq("user_id", user.user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
    row = res.data[0]
    return MatchDetail(
        id=row["id"],
        status=row.get("status"),
        fit_score=row.get("fit_score"),
        b2b_eligible=row.get("b2b_eligible"),
        analysis=row.get("analysis"),
        cover_letter=row.get("cover_letter"),
        ats_report=row.get("ats_report"),
        job=row.get("jobs"),
        signed_cv_url=_sign_cv(supabase, row.get("cv_docx_path")),
    )
