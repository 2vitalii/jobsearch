"""Matches endpoints: list a user's assessed matches and generate packages on demand.

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

POST /matches/{id}/generate design
------------------------------------
- Ownership: verified via user-scoped client (RLS) before scheduling background work.
- Atomic 409 guard: UPDATE SET generation_status='generating' WHERE ... AND
  generation_status <> 'generating' RETURNING id. Zero rows → already generating → 409.
  This is race-free (atomic DB operation), mirroring the /run 409 pattern.
- Background: fresh service_role client reads the match row + jobs.description + CV,
  calls generate(), build_package(), uploads .docx, updates the match row to 'done'.
  On any exception → 'failed'.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from jobsearch import render
from jobsearch.models import Assessment, Generation, Job, MatchResult, PlatformConfig
from jobsearch.scoring import generate as scoring_generate
from jobsearch.supabase_store import make_supabase_client

from .auth import CurrentUser, get_current_user
from .deps import get_config, get_llm, get_supabase, get_user_client
from .run import _load_cv, _upload_docx

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/matches", tags=["matches"])

SIGNED_URL_TTL = 300  # seconds — short-lived download link for the private .docx

# Denormalized job display fields live on matches (see migration 0007); the
# user-scoped path never touches the shared jobs pool.
_SELECT = "*"

DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class MatchDetail(BaseModel):
    id: str
    run_id: str | None = None
    status: str | None = None
    fit_score: int | None = None
    b2b_eligible: str | None = None
    job_posted_date: str | None = None
    analysis: dict | None = None
    cover_letter: str | None = None
    ats_report: str | None = None
    job: dict | None = None
    signed_cv_url: str | None = None
    generation_status: str | None = None


class GenerateStarted(BaseModel):
    """Returned by POST /matches/{id}/generate (202)."""
    match_id: str
    generation_status: str


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
        run_id=row.get("run_id"),
        status=row.get("status"),
        fit_score=row.get("fit_score"),
        b2b_eligible=row.get("b2b_eligible"),
        job_posted_date=row.get("job_posted_date"),
        analysis=row.get("analysis"),
        cover_letter=row.get("cover_letter"),
        ats_report=row.get("ats_report"),
        job=_build_job(row),
        signed_cv_url=_sign_cv(signer, row.get("cv_docx_path")),
        generation_status=row.get("generation_status"),
    )


@router.post("/{match_id}/generate", response_model=GenerateStarted, status_code=status.HTTP_202_ACCEPTED)
def generate_package(
    match_id: str,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
    user_client=Depends(get_user_client),
    supabase=Depends(get_supabase),
    llm=Depends(get_llm),
    config: PlatformConfig = Depends(get_config),
) -> GenerateStarted:
    """Start async package generation for a match. Returns 202 immediately.

    1. Verify ownership (user-scoped client / RLS): 404 if not found or not theirs.
    2. Atomic 409 guard: UPDATE SET generation_status='generating' WHERE id=:id
       AND user_id=:uid AND generation_status <> 'generating' RETURNING id.
       Zero rows → already generating → 409.
    3. Return 202 + {match_id, generation_status:'generating'}.
    4. Schedule _generate_background.
    """
    uid = user.user_id

    # Step 1: verify ownership via user-scoped client (RLS).
    ownership_res = (
        user_client.table("matches")
        .select("id, generation_status")
        .eq("id", match_id)
        .eq("user_id", uid)
        .limit(1)
        .execute()
    )
    if not ownership_res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")

    # Step 2: atomic 409 guard — set 'generating' only if not already generating.
    # Uses service_role client for the write (ownership already verified via RLS above).
    # The WHERE generation_status <> 'generating' makes this race-free: two concurrent
    # requests will both see the ownership check pass, but only the first UPDATE wins.
    update_res = (
        supabase.table("matches")
        .update({"generation_status": "generating"})
        .eq("id", match_id)
        .eq("user_id", uid)
        .neq("generation_status", "generating")
        .execute()
    )
    if not update_res.data:
        # Zero rows updated → already 'generating' (or the match disappeared, but
        # ownership was verified above so it must be already in-progress).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Package generation is already in progress for this match.",
        )

    # Step 3: load CV for the background task (404 if missing — check before scheduling).
    cv_markdown, _ = _load_cv(supabase, uid)

    # Step 4: schedule background generation.
    background_tasks.add_task(
        _generate_background,
        match_id=match_id,
        user_id=uid,
        cv_markdown=cv_markdown,
        config=config,
        llm=llm,
    )

    return GenerateStarted(match_id=match_id, generation_status="generating")


def _generate_background(
    match_id: str,
    user_id: str,
    cv_markdown: str,
    config: PlatformConfig,
    llm,
) -> None:
    """Execute package generation as a background task.

    Loads the match (assessment) and the job description from the shared jobs table,
    calls generate(), build_package(), uploads the .docx, and updates the match row
    to generation_status='done'. On any exception → 'failed'.

    Fresh service_role client: called after HTTP response is sent; no request JWT.
    All reads from matches are filtered by match_id (no cross-user access).
    Job description is read from the shared jobs table (service_role; RLS-ok for jobs).
    """
    supabase = make_supabase_client()

    try:
        # Load the match row (assessment data + denorm job fields + job_id).
        match_res = (
            supabase.table("matches")
            .select("*")
            .eq("id", match_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not match_res.data:
            logger.error("_generate_background: match %s not found for user %s", match_id, user_id)
            supabase.table("matches").update({"generation_status": "failed"}).eq("id", match_id).eq("user_id", user_id).execute()
            return

        match_row = match_res.data[0]
        job_id = match_row.get("job_id")

        # Load job description from the shared jobs table.
        # matches.job_id → jobs ON DELETE CASCADE: if the job row exists, it's always present.
        # If description is empty, proceed in degraded mode (LLM will rely on CV + assessment).
        job_description = ""
        if job_id:
            jobs_res = (
                supabase.table("jobs")
                .select("description, title, company, location, url, region")
                .eq("id", job_id)
                .limit(1)
                .execute()
            )
            if jobs_res.data:
                job_row = jobs_res.data[0]
                job_description = job_row.get("description") or ""

        # Reconstruct Job from denormalized match fields + jobs.description.
        job = Job(
            dedup_key="",  # not needed for generation
            source="",
            url=match_row.get("job_url") or "",
            company=match_row.get("job_company") or "",
            title=match_row.get("job_title") or "",
            location="",  # not stored in matches; generate() uses title+description
            region=match_row.get("job_region") or "",
            description=job_description,
        )

        # Reconstruct Assessment from analysis JSONB field.
        analysis = match_row.get("analysis") or {}
        assessment = Assessment(
            fit_score=int(match_row.get("fit_score") or 0),
            b2b=match_row.get("b2b_eligible") or "",
            reason=analysis.get("reason", ""),
            jd_keywords=analysis.get("jd_keywords") or [],
            ats_present=analysis.get("ats_present") or [],
            ats_missing=analysis.get("ats_missing") or [],
            gaps=analysis.get("gaps", ""),
            recruiter_verdict=analysis.get("recruiter_verdict", ""),
        )

        # Generate tailored CV text + cover letter.
        gen: Generation = scoring_generate(job, cv_markdown, assessment, config, llm)

        # Build the full MatchResult for render.build_package.
        res = MatchResult.from_assessment_and_generation(assessment, gen)

        # Render the package (CV .docx bytes + cover letter + ATS report).
        pkg = render.build_package(job, res, cv_markdown)

        # Upload the .docx and get the storage path.
        cv_docx_path = _upload_docx(supabase, user_id, job, res.fit_score, pkg.cv_docx)

        # Update the match row: merge tailored fields into analysis, write cover_letter,
        # ats_report, cv_docx_path, and set generation_status='done'.
        updated_analysis = {
            **analysis,
            "tailored_summary": gen.tailored_summary,
            "tailored_skills": gen.tailored_skills,
        }
        supabase.table("matches").update({
            "generation_status": "done",
            "cover_letter": pkg.cover_letter,
            "ats_report": pkg.ats_report,
            "cv_docx_path": cv_docx_path,
            "analysis": updated_analysis,
        }).eq("id", match_id).eq("user_id", user_id).execute()

    except Exception as exc:
        # Sanitize: only log the class name to the user-facing field.
        safe_error = type(exc).__name__
        logger.exception(
            "_generate_background: match %s for user %s failed (%s)",
            match_id, user_id, safe_error,
        )
        try:
            supabase.table("matches").update({"generation_status": "failed"}).eq("id", match_id).eq("user_id", user_id).execute()
        except Exception:
            logger.exception("_generate_background: failed to set generation_status=failed for match %s", match_id)
