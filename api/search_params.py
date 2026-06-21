"""Saved-search endpoints: one SearchParams row per user.

All routes are behind ``get_current_user``. The ``search_params`` table is touched
here in the API layer through the injected Supabase client — same pattern as cvs;
the core ``UserState`` Protocol is not widened. Fields and defaults mirror
``jobsearch.models.SearchParams`` 1:1.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .auth import CurrentUser, get_current_user
from .deps import get_user_client

router = APIRouter(prefix="/search-params", tags=["search-params"])


class SearchParamsBody(BaseModel):
    """Mirrors models.SearchParams (same defaults)."""
    keywords: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    period_hours: int = 168
    work_format: str = "remote"
    loose: bool = False
    targeted: bool = False


@router.put("", response_model=SearchParamsBody)
def put_search_params(
    body: SearchParamsBody,
    user: CurrentUser = Depends(get_current_user),
    supabase=Depends(get_user_client),
) -> SearchParamsBody:
    row = {"user_id": user.user_id, **body.model_dump()}
    (
        supabase.table("search_params")
        .upsert(row, on_conflict="user_id")
        .execute()
    )
    return body


@router.get("", response_model=SearchParamsBody)
def get_search_params(
    user: CurrentUser = Depends(get_current_user),
    supabase=Depends(get_user_client),
) -> SearchParamsBody:
    res = (
        supabase.table("search_params")
        .select("keywords, locations, period_hours, work_format, loose, targeted")
        .eq("user_id", user.user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No saved search yet")
    return SearchParamsBody(**res.data[0])
