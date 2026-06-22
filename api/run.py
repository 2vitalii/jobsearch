"""The Run endpoint — orchestrates the per-user scrape/score/package loop.

Mirrors jobsearch.pipeline.main, but: one user at a time, state in Supabase
(processed_jobs / matches) instead of flat files, the tailored .docx in the
private ``packages`` bucket instead of a local folder, and asynchronous via
FastAPI BackgroundTasks (SG-03).

Background task design
-----------------------
POST /run returns 202 immediately with a ``run_id``. The scrape→score→package
loop runs in the background, writing progress to the ``runs`` table. The
client polls GET /run/{run_id} or GET /run/latest for status.

The background function uses a dedicated service_role client (NOT the
request-scoped user session). This is the single documented exception to the
"user-scoped client for user data" rule, justified by the same reasoning as
JobStore: the background task executes after the HTTP response has been sent,
so no request-scoped JWT is available. We scope by ``user_id`` in every
write to enforce isolation in code, exactly as the service_role-backed
UserState does for processed_jobs and matches.

GET endpoints use the user-scoped client (RLS active, authenticated role),
so the DB enforces that a user can only read their own run rows.

KEY DIFFERENCE from the flat-file pipeline: the processing key is ``dedup_key``,
not ``url``. processed_jobs and matches are keyed on dedup_key, so the whole
fresh/processed bookkeeping uses it.

Access to cvs / search_params / matches / Storage is done here in the API
layer through the injected Supabase client (same pattern as cvs/search_params);
the core UserState Protocol is not widened.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from jobsearch import filters, render
from jobsearch.models import Job, PlatformConfig, SearchParams
from jobsearch.scoring import analyze, score_fit
from jobsearch.supabase_store import _job_row, make_supabase_client

from .auth import CurrentUser, get_current_user
from .deps import (
    get_config,
    get_job_store,
    get_llm,
    get_scraper,
    get_supabase,
    get_user_client,
    get_user_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["run"])

# Best regions first, then freshest — same ordering as the CLI pipeline.
REGION_ORDER = {"WORLDWIDE": 0, "EUROPE": 1, "UNKNOWN": 2, "US-ONLY": 3}
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class RunStarted(BaseModel):
    """Returned by POST /run (202). The client uses run_id to poll status."""
    run_id: str


class RunStatus(BaseModel):
    """Returned by GET /run/{run_id} and GET /run/latest."""
    status: str          # running | done | failed
    scraped: int
    processed: int       # was "queued" in the old synchronous RunSummary
    generated: int
    skipped_low_fit: int
    summary: dict | None = None
    error: str | None = None


class RunSummary(BaseModel):
    """Legacy shape kept for the final summary jsonb stored in the runs row.
    The background task writes this as ``runs.summary`` on completion."""
    scraped: int
    queued: int          # kept as "queued" in the jsonb for backwards compat
    generated: int
    skipped_low_fit: int


# ---------------------------------------------------------------------------
# Internal helpers (unchanged from the synchronous version)
# ---------------------------------------------------------------------------

def _resolve_job_id(supabase, job: Job) -> str:
    """Job id from the shared pool by dedup_key; insert the job if the pool has
    never seen it (a match always needs a job to point at). Same approach as
    SupabaseUserState._resolve_job_id, kept in the API layer for self-containment."""
    res = supabase.table("jobs").select("id").eq("dedup_key", job.dedup_key).limit(1).execute()
    if res.data:
        return res.data[0]["id"]
    ins = supabase.table("jobs").upsert(_job_row(job), on_conflict="dedup_key").execute()
    if ins.data:
        return ins.data[0]["id"]
    again = supabase.table("jobs").select("id").eq("dedup_key", job.dedup_key).limit(1).execute()
    return again.data[0]["id"]


def _upload_docx(supabase, user_id: str, job: Job, score: int, data: bytes) -> str:
    """Upload the tailored .docx to the private packages bucket under the user's
    prefix and return the storage path. safe_name() guards the path (traversal)."""
    path = f"{user_id}/{score:03d}_{render.safe_name(job.company or 'x')}_{render.safe_name(job.title or 'x')}.docx"
    supabase.storage.from_("packages").upload(
        path=path,
        file=data,
        file_options={"content-type": DOCX_CT, "upsert": "true"},
    )
    return path


def _write_match(supabase, user_id: str, job: Job, res, ats_report: str, cv_docx_path: str) -> None:
    job_id = _resolve_job_id(supabase, job)
    row = {
        "user_id": user_id,
        "job_id": job_id,
        "status": "GENERATED",
        "fit_score": res.fit_score,
        "b2b_eligible": res.b2b,
        "cover_letter": res.cover_letter,
        "ats_report": ats_report,
        "cv_docx_path": cv_docx_path,
        # Denormalized job display fields (SG-02): the per-user matches path reads
        # these instead of joining the RLS-closed jobs pool.
        "job_title": job.title,
        "job_company": job.company,
        "job_url": job.url,
        "job_region": job.region,
        "analysis": {
            "reason": res.reason,
            "jd_keywords": res.jd_keywords,
            "ats_present": res.ats_present,
            "ats_missing": res.ats_missing,
            "tailored_summary": res.tailored_summary,
            "tailored_skills": res.tailored_skills,
            "gaps": res.gaps,
            "recruiter_verdict": res.recruiter_verdict,
        },
    }
    supabase.table("matches").upsert(row, on_conflict="user_id,job_id").execute()


def _load_search_params(supabase, user_id: str) -> SearchParams:
    res = (
        supabase.table("search_params")
        .select("keywords, locations, period_hours, work_format, loose, targeted")
        .eq("user_id", user_id).limit(1).execute()
    )
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No saved search — set /search-params first")
    r = res.data[0]
    return SearchParams(
        keywords=r.get("keywords") or [],
        locations=r.get("locations") or [],
        period_hours=r.get("period_hours") or 168,
        work_format=r.get("work_format") or "remote",
        loose=bool(r.get("loose")),
        targeted=bool(r.get("targeted")),
    )


def _load_cv(supabase, user_id: str) -> tuple[str, str]:
    res = (
        supabase.table("cvs").select("markdown, short_profile")
        .eq("user_id", user_id).limit(1).execute()
    )
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No CV — upload one at /cv/upload first")
    row = res.data[0]
    return row["markdown"], (row.get("short_profile") or "")


# ---------------------------------------------------------------------------
# Runs table helpers (service_role writes)
# ---------------------------------------------------------------------------

def _update_run(supabase, run_id: str, **fields: Any) -> None:
    """Bump updated_at + supplied fields on the runs row.

    Always called via service_role — see module docstring for why this is the
    approved exception. Every call explicitly targets a single run_id row and
    never touches other users' rows (the run_id is an unguessable UUID).
    """
    supabase.table("runs").update(
        {"updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), **fields}
    ).eq("id", run_id).execute()


def _mark_run_failed(supabase, run_id: str, error: str) -> None:
    """Set status='failed' and record the error message. Secrets must NOT appear
    in ``error`` — callers are responsible for sanitising before passing here."""
    _update_run(supabase, run_id, status="failed", error=error)


# ---------------------------------------------------------------------------
# Background task — the actual scrape/score/package loop
# ---------------------------------------------------------------------------

def _run_background(
    run_id: str,
    user_id: str,
    params: SearchParams,
    cv_markdown: str,
    short_profile: str,
    config: PlatformConfig,
    scraper,
    job_store,
    user_state,
    llm,
) -> None:
    """Execute the scrape→filter→score→package loop as a background task.

    Service_role client note: this function is called AFTER the HTTP response
    has been sent. There is no active user JWT at this point, so we build a
    fresh service_role client here. All writes are scoped to ``user_id`` in
    code. This is the single deliberate exception to the 'user-scoped writes'
    rule, mirroring the JobStore pattern (see module docstring and 0008 SQL).
    """
    # Build a fresh service_role client for this background task.
    # Do NOT reuse the lru_cache singleton — background threads must not share
    # a stateful GoTrue session with the main request thread.
    supabase = make_supabase_client()

    try:
        # -----------------------------------------------------------------
        # Scrape and fold into the shared pool.
        # -----------------------------------------------------------------
        jobs = scraper(params, config)
        job_store.save(jobs)
        _update_run(supabase, run_id, scraped=len(jobs))

        # -----------------------------------------------------------------
        # Same filters as the CLI pipeline, keyed on dedup_key.
        # -----------------------------------------------------------------
        def _passes(j: Job) -> bool:
            return (
                (not filters.blocked(j.title))
                and filters.remote_ok(j.title, j.description, None)
                and (params.loose or filters.matches_role(j.title))
            )

        def _fresh(j: Job) -> bool:
            return (
                j.region in config.process_regions
                and not user_state.is_processed(user_id, j.dedup_key)
                and _passes(j)
            )

        fresh = [j for j in jobs if _fresh(j)]
        fresh.sort(key=lambda j: REGION_ORDER.get(j.region, 9))
        queue = sorted(fresh, key=lambda j: j.date_posted or "", reverse=True)[: config.max_jobs]

        _update_run(supabase, run_id, processed=len(queue))

        # -----------------------------------------------------------------
        # Score / analyze / package loop.
        # -----------------------------------------------------------------
        generated = 0
        skipped_low_fit = 0
        for job in queue:
            # Step 1 (cheap, Haiku): pre-filter.
            pre = score_fit(job, short_profile, config, llm)
            if pre.fit_score < config.pre_min_fit:
                user_state.mark_processed(user_id, job.dedup_key)
                skipped_low_fit += 1
                _update_run(supabase, run_id, skipped_low_fit=skipped_low_fit)
                continue

            # Step 2 (expensive, Sonnet): full tailoring only for survivors.
            res = analyze(job, cv_markdown, config, llm)
            if res.fit_score < config.min_fit:
                user_state.mark_processed(user_id, job.dedup_key)
                skipped_low_fit += 1
                _update_run(supabase, run_id, skipped_low_fit=skipped_low_fit)
                continue

            pkg = render.build_package(job, res, cv_markdown)
            cv_docx_path = _upload_docx(supabase, user_id, job, res.fit_score, pkg.cv_docx)
            _write_match(supabase, user_id, job, res, pkg.ats_report, cv_docx_path)
            user_state.mark_processed(user_id, job.dedup_key)
            generated += 1
            _update_run(supabase, run_id, generated=generated)

        # -----------------------------------------------------------------
        # Finalise the run row.
        # -----------------------------------------------------------------
        summary = RunSummary(
            scraped=len(jobs),
            queued=len(queue),
            generated=generated,
            skipped_low_fit=skipped_low_fit,
        )
        _update_run(
            supabase,
            run_id,
            status="done",
            scraped=len(jobs),
            processed=len(queue),
            generated=generated,
            skipped_low_fit=skipped_low_fit,
            summary=summary.model_dump(),
        )

    except Exception as exc:
        # Do NOT let the background task crash the worker process.
        # Error stored in the DB is user-readable via GET /run/{id}.  We must
        # NOT echo arbitrary exception messages (which may contain scraped text,
        # URLs, internal paths, or other sensitive substrings).  Instead we
        # store only the exception class name.  The full traceback is emitted
        # to the server log (logger.exception below) for operator debugging.
        safe_error = type(exc).__name__
        logger.exception(
            "Background run %s failed for user %s (%s)",
            run_id, user_id, safe_error,
        )
        try:
            _mark_run_failed(supabase, run_id, safe_error)
        except Exception:
            logger.exception("Failed to mark run %s as failed", run_id)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/run", response_model=RunStarted, status_code=status.HTTP_202_ACCEPTED)
def run(
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
    supabase=Depends(get_supabase),
    job_store=Depends(get_job_store),
    user_state=Depends(get_user_state),
    llm=Depends(get_llm),
    config: PlatformConfig = Depends(get_config),
    scraper=Depends(get_scraper),
) -> RunStarted:
    """Start an async run. Returns 202 + run_id immediately.

    The handler is responsible for:
      1. Auth (via Depends).
      2. Loading search_params + CV (404 if missing).
      3. 409 if the user already has a 'running' run.
      4. INSERT a new 'running' row.
      5. Schedule the background task.
      6. Return 202 {run_id}.

    The actual scrape/score/package loop runs in _run_background() after the
    response is sent.
    """
    uid = user.user_id

    # Load params and CV now (in the handler) so we can return 404 immediately
    # if they are missing, before we create the run row or schedule any work.
    params = _load_search_params(supabase, uid)
    cv_markdown, short_profile = _load_cv(supabase, uid)

    # One active run per user.
    #
    # Fast-path pre-check: return 409 in the common (non-concurrent) case so
    # the client gets a useful message immediately.
    # This SELECT is NOT the authoritative guard — it has a race window if two
    # requests arrive simultaneously and both see no active row here.
    #
    # Authoritative guard: the partial unique index
    #   idx_runs_one_active_per_user ON runs(user_id) WHERE status='running'
    # means only one INSERT with status='running' can succeed per user.  If the
    # concurrent request wins the INSERT race, the second INSERT raises a unique
    # violation (PostgreSQL error code 23505), which we catch below and convert
    # to the same HTTP 409.
    active = (
        supabase.table("runs")
        .select("id")
        .eq("user_id", uid)
        .eq("status", "running")
        .limit(1)
        .execute()
    )
    if active.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A run is already in progress. Poll GET /run/latest for status.",
        )

    # Insert the initial 'running' row so the client can start polling.
    # Catch a unique-violation from the partial index (concurrent POST race).
    try:
        ins = (
            supabase.table("runs")
            .insert({"user_id": uid, "status": "running"})
            .execute()
        )
    except Exception as exc:
        # PostgreSQL unique-violation error code is 23505.  The supabase-py
        # client surfaces this as an APIError whose message contains "23505"
        # or "unique" / "duplicate".  We convert any such violation to 409.
        exc_str = str(exc)
        if "23505" in exc_str or "duplicate" in exc_str.lower() or "unique" in exc_str.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A run is already in progress. Poll GET /run/latest for status.",
            ) from exc
        raise
    run_id: str = ins.data[0]["id"]

    # Schedule the loop. BackgroundTasks runs this after the response is sent.
    background_tasks.add_task(
        _run_background,
        run_id=run_id,
        user_id=uid,
        params=params,
        cv_markdown=cv_markdown,
        short_profile=short_profile,
        config=config,
        scraper=scraper,
        job_store=job_store,
        user_state=user_state,
        llm=llm,
    )

    return RunStarted(run_id=run_id)


@router.get("/run/latest", response_model=RunStatus)
def get_run_latest(
    user: CurrentUser = Depends(get_current_user),
    user_client=Depends(get_user_client),
) -> RunStatus:
    """Return the user's most recent run (by created_at). 404 if none.

    Uses the user-scoped client (RLS active, authenticated role) so the
    database enforces that a user can only read their own rows.
    """
    res = (
        user_client.table("runs")
        .select("*")
        .eq("user_id", user.user_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No runs found")
    return _row_to_status(res.data[0])


@router.get("/run/{run_id}", response_model=RunStatus)
def get_run_status(
    run_id: str,
    user: CurrentUser = Depends(get_current_user),
    user_client=Depends(get_user_client),
) -> RunStatus:
    """Return the status of a specific run. 404 if not found or not owned by the caller.

    Uses the user-scoped client (RLS active, authenticated role) so the
    database enforces that a user can only read their own rows. If the run_id
    belongs to a different user, the RLS policy returns zero rows → 404.
    """
    res = (
        user_client.table("runs")
        .select("*")
        .eq("id", run_id)
        .eq("user_id", user.user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return _row_to_status(res.data[0])


def _row_to_status(row: dict) -> RunStatus:
    """Map a ``runs`` table row to a RunStatus response model."""
    return RunStatus(
        status=row["status"],
        scraped=row.get("scraped") or 0,
        processed=row.get("processed") or 0,
        generated=row.get("generated") or 0,
        skipped_low_fit=row.get("skipped_low_fit") or 0,
        summary=row.get("summary"),
        error=row.get("error"),
    )
