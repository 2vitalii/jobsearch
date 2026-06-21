"""The Run endpoint — the heart of the loop, per-user against Supabase + Storage.

Mirrors jobsearch.pipeline.main, but: one user at a time, state in Supabase
(processed_jobs / matches) instead of flat files, the tailored .docx in the
private ``packages`` bucket instead of a local folder, and synchronous (no CLI
sleeps). The pure core (scrape / score_fit / analyze / build_package) is reused
unchanged; this module only orchestrates the per-user I/O at the edge.

KEY DIFFERENCE from the flat-file pipeline: the processing key is ``dedup_key``,
not ``url``. processed_jobs and matches are keyed on dedup_key, so the whole
fresh/processed bookkeeping uses it.

Access to cvs / search_params / matches / Storage is done here in the API layer
through the injected Supabase client (same pattern as cvs/search_params); the core
UserState Protocol is not widened.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from jobsearch import filters, render
from jobsearch.models import Job, PlatformConfig, SearchParams
from jobsearch.scoring import analyze, score_fit
from jobsearch.supabase_store import _job_row

from .auth import CurrentUser, get_current_user
from .deps import (
    get_config,
    get_job_store,
    get_llm,
    get_scraper,
    get_supabase,
    get_user_state,
)

router = APIRouter(tags=["run"])

# Best regions first, then freshest — same ordering as the CLI pipeline.
REGION_ORDER = {"WORLDWIDE": 0, "EUROPE": 1, "UNKNOWN": 2, "US-ONLY": 3}
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class RunSummary(BaseModel):
    scraped: int
    queued: int
    generated: int
    skipped_low_fit: int


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


@router.post("/run", response_model=RunSummary)
def run(
    user: CurrentUser = Depends(get_current_user),
    supabase=Depends(get_supabase),
    job_store=Depends(get_job_store),
    user_state=Depends(get_user_state),
    llm=Depends(get_llm),
    config: PlatformConfig = Depends(get_config),
    scraper=Depends(get_scraper),
) -> RunSummary:
    uid = user.user_id
    params = _load_search_params(supabase, uid)
    cv_markdown, short_profile = _load_cv(supabase, uid)

    # Scrape and fold into the shared pool (dedup-on-insert lives in the store).
    jobs = scraper(params, config)
    job_store.save(jobs)

    # Same filters as the CLI pipeline, but keyed on dedup_key and using
    # params.loose instead of the LOOSE_FILTER env var.
    def _passes(j: Job) -> bool:
        return ((not filters.blocked(j.title)) and filters.remote_ok(j.title, j.description, None)
                and (params.loose or filters.matches_role(j.title)))

    def _fresh(j: Job) -> bool:
        return (j.region in config.process_regions
                and not user_state.is_processed(uid, j.dedup_key)
                and _passes(j))

    fresh = [j for j in jobs if _fresh(j)]
    fresh.sort(key=lambda j: REGION_ORDER.get(j.region, 9))
    queue = sorted(fresh, key=lambda j: j.date_posted or "", reverse=True)[: config.max_jobs]

    generated = 0
    skipped_low_fit = 0
    for job in queue:
        # Step 1 (cheap, Haiku): pre-filter.
        pre = score_fit(job, short_profile, config, llm)
        if pre.fit_score < config.pre_min_fit:
            user_state.mark_processed(uid, job.dedup_key)
            skipped_low_fit += 1
            continue

        # Step 2 (expensive, Sonnet): full tailoring only for survivors.
        res = analyze(job, cv_markdown, config, llm)
        if res.fit_score < config.min_fit:
            user_state.mark_processed(uid, job.dedup_key)
            skipped_low_fit += 1
            continue

        pkg = render.build_package(job, res, cv_markdown)
        cv_docx_path = _upload_docx(supabase, uid, job, res.fit_score, pkg.cv_docx)
        _write_match(supabase, uid, job, res, pkg.ats_report, cv_docx_path)
        user_state.mark_processed(uid, job.dedup_key)
        generated += 1

    return RunSummary(
        scraped=len(jobs),
        queued=len(queue),
        generated=generated,
        skipped_low_fit=skipped_low_fit,
    )
