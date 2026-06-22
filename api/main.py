"""FastAPI application entrypoint.

Run locally:  uvicorn api.main:app --reload

Only the skeleton lives here: an unauthenticated health probe and an
auth-gated identity echo. Business endpoints (search params, jobs, matches)
arrive in later steps.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import CurrentUser, get_current_user
from .cv import router as cv_router
from .matches import router as matches_router
from .run import router as run_router
from .search_params import router as search_params_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifespan handler.

    On startup: mark any orphaned 'running' runs as 'failed' with a
    descriptive error. Orphans arise when the process is killed (e.g. a
    restart or OOM) while a background task is executing. Without this
    cleanup, those rows would hang in 'running' forever and block the
    one-active-run-per-user guard indefinitely.

    This is a global service_role sweep across ALL users — an acceptable
    exception because it is startup maintenance (not a user request) and
    because the service_role client is already used for background task
    writes. We log a count so operators can spot unexpectedly frequent
    restarts. No user data is exposed; the update only changes status/error.
    """
    # Lazy import: keeps the Supabase dep optional for unit-test runs where
    # SUPABASE_URL / SUPABASE_SECRET_KEY are absent.
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_SECRET_KEY", "")

    if supabase_url and supabase_key:
        try:
            import datetime

            from jobsearch.supabase_store import make_supabase_client

            sb = make_supabase_client()
            result = (
                sb.table("runs")
                .update(
                    {
                        "status": "failed",
                        "error": "interrupted by restart",
                        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    }
                )
                .eq("status", "running")
                .execute()
            )
            count = len(result.data) if result.data else 0
            if count:
                logger.warning(
                    "Startup cleanup: marked %d orphaned 'running' run(s) as 'failed'.",
                    count,
                )
            else:
                logger.info("Startup cleanup: no orphaned runs found.")
        except Exception:
            # Do not prevent startup if cleanup fails (e.g. DB unreachable).
            logger.exception("Startup cleanup failed; continuing anyway.")
    else:
        logger.info("Startup cleanup skipped: SUPABASE_URL/SUPABASE_SECRET_KEY not set.")

    yield  # application runs here

    # Shutdown: nothing to teardown currently.


app = FastAPI(title="jobsearch API", version="0.1.0", lifespan=lifespan)

# Frontend origin for CORS (single-page app talks to Auth + this API).
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(cv_router)
app.include_router(search_params_router)
app.include_router(run_router)
app.include_router(matches_router)


@app.get("/health")
def health() -> dict:
    """Unauthenticated liveness probe."""
    return {"status": "ok"}


@app.get("/me")
def me(user: CurrentUser = Depends(get_current_user)) -> dict:
    """Echo the authenticated caller's identity."""
    return {"user_id": user.user_id, "email": user.email}
