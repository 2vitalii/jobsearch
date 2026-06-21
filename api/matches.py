"""Matches endpoints: list a user's generated matches and view one with a
short-lived signed URL to download the tailored CV.

SECURITY (SG-02): match rows are read through a USER-SCOPED Supabase client (anon
key + caller's JWT), so PostgREST runs as `authenticated` and the RLS policy
(auth.uid() = user_id) filters rows in the database — a real second layer. The
app-level ``.eq("user_id", …)`` is kept on top as defense-in-depth.

The shared ``jobs`` pool is NOT readable on this path (no grant + RLS-closed), so
display fields (title/company/url/region) are read from the denormalized columns
on ``matches`` (written in api/run.py), never via a join to jobs.

Storage signing needs service_role (the bucket is backend-only), so the signed
URL is produced with the service_role client — but only for ``cv_docx_path`` taken
from a row already proven to belong to the caller (RLS-filtered).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .auth import CurrentUser, get_current_user
from .deps import get_supabase, get_user_client

router = APIRouter(prefix="/matches", tags=["matches"])

SIGNED_URL_TTL = 300  # seconds — short-lived download link for the private .docx

# Denormalized job display fields live on matches (see migration 0007); the
# user-scoped path never touches the shared jobs pool.
_SELECT = "*"


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


def _build_job(row: dict) -> dict:
    """Display job object assembled from the denormalized columns on matches."""
    return {
        "title": row.get("job_title"),
        "company": row.get("job_company"),
        "url": row.get("job_url"),
        "region": row.get("job_region"),
    }


def _sign_cv(signer, path: str | None) -> str | None:
    """Short-lived signed URL for a private packages object, or None if no docx.
    Uses the service_role client (storage is backend-only); the path comes from a
    row already RLS-scoped to the caller."""
    if not path:
        return None
    res = signer.storage.from_("packages").create_signed_url(path, SIGNED_URL_TTL)
    # supabase-py returns a dict; the key has varied across versions.
    return res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")


@router.get("", response_model=list[dict])
def list_matches(
    user: CurrentUser = Depends(get_current_user),
    user_client=Depends(get_user_client),
) -> list[dict]:
    # User-scoped: RLS enforces user_id; the .eq is defense-in-depth.
    res = (
        user_client.table("matches")
        .select(_SELECT)
        .eq("user_id", user.user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return [{**row, "job": _build_job(row)} for row in (res.data or [])]


@router.get("/{match_id}", response_model=MatchDetail)
def get_match(
    match_id: str,
    user: CurrentUser = Depends(get_current_user),
    user_client=Depends(get_user_client),
    signer=Depends(get_supabase),
) -> MatchDetail:
    # User-scoped read: RLS + (id AND user_id). Not found OR not theirs -> 404.
    res = (
        user_client.table("matches")
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
        job=_build_job(row),
        signed_cv_url=_sign_cv(signer, row.get("cv_docx_path")),
    )
