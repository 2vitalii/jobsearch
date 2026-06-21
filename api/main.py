"""FastAPI application entrypoint.

Run locally:  uvicorn api.main:app --reload

Only the skeleton lives here: an unauthenticated health probe and an
auth-gated identity echo. Business endpoints (search params, jobs, matches)
arrive in later steps.
"""

from __future__ import annotations

import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import CurrentUser, get_current_user
from .cv import router as cv_router
from .run import router as run_router
from .search_params import router as search_params_router

app = FastAPI(title="jobsearch API", version="0.1.0")

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


@app.get("/health")
def health() -> dict:
    """Unauthenticated liveness probe."""
    return {"status": "ok"}


@app.get("/me")
def me(user: CurrentUser = Depends(get_current_user)) -> dict:
    """Echo the authenticated caller's identity."""
    return {"user_id": user.user_id, "email": user.email}
