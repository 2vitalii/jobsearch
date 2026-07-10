"""CV intake endpoints: upload (PDF/docx) → LLM parse to master_cv.md → store.

All routes are behind ``get_current_user``. The ``cvs`` table is touched here in
the API layer through the injected Supabase client — we deliberately do NOT widen
the core ``UserState`` Protocol for this skeleton; CV is a product concern, not
part of the scraping/scoring state seam. The text extraction (PDF/docx) lives
here too; the pure LLM parsing lives in the core (jobsearch.cv).
"""

from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from jobsearch.cv import make_short_profile, parse_cv, suggest_search_roles
from jobsearch.models import PlatformConfig

from .auth import CurrentUser, get_current_user
from .deps import get_config, get_llm, get_user_client

router = APIRouter(prefix="/cv", tags=["cv"])

MAX_BYTES = 5 * 1024 * 1024  # ~5 MB upload cap
PDF_TYPES = {"application/pdf"}
DOCX_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class CvOut(BaseModel):
    markdown: str
    short_profile: str


class CvPut(BaseModel):
    markdown: str


class SuggestRolesOut(BaseModel):
    roles: list[str]


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()


def _extract_docx(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs).strip()


def _extract_text(file: UploadFile, data: bytes) -> str:
    name = (file.filename or "").lower()
    ctype = (file.content_type or "").lower()
    if ctype in PDF_TYPES or name.endswith(".pdf"):
        return _extract_pdf(data)
    if ctype in DOCX_TYPES or name.endswith(".docx"):
        return _extract_docx(data)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Only PDF or .docx files are accepted",
    )


def _upsert_cv(supabase, user_id: str, markdown: str, short_profile: str) -> None:
    (
        supabase.table("cvs")
        .upsert(
            {"user_id": user_id, "markdown": markdown, "short_profile": short_profile},
            on_conflict="user_id",
        )
        .execute()
    )


@router.post("/upload", response_model=CvOut)
def upload_cv(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
    supabase=Depends(get_user_client),
    llm=Depends(get_llm),
    config: PlatformConfig = Depends(get_config),
) -> CvOut:
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File too large (max 5 MB)",
        )
    resume_text = _extract_text(file, data)
    if not resume_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not extract any text from the file",
        )
    markdown = parse_cv(resume_text, llm, config)
    short_profile = make_short_profile(markdown, llm, config)
    _upsert_cv(supabase, user.user_id, markdown, short_profile)
    return CvOut(markdown=markdown, short_profile=short_profile)


@router.get("", response_model=CvOut)
def get_cv(
    user: CurrentUser = Depends(get_current_user),
    supabase=Depends(get_user_client),
) -> CvOut:
    res = (
        supabase.table("cvs")
        .select("markdown, short_profile")
        .eq("user_id", user.user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No CV yet")
    row = res.data[0]
    return CvOut(markdown=row["markdown"], short_profile=row.get("short_profile") or "")


@router.post("/suggest-roles", response_model=SuggestRolesOut)
def suggest_roles(
    user: CurrentUser = Depends(get_current_user),
    supabase=Depends(get_user_client),
    llm=Depends(get_llm),
    config: PlatformConfig = Depends(get_config),
) -> SuggestRolesOut:
    """Extract 5-8 searchable job titles from the user's CV using the LLM."""
    res = (
        supabase.table("cvs")
        .select("markdown")
        .eq("user_id", user.user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No CV yet")
    markdown = res.data[0]["markdown"] or ""
    roles = suggest_search_roles(markdown, llm, config)
    return SuggestRolesOut(roles=roles)


@router.put("", response_model=CvOut)
def update_cv(
    body: CvPut,
    user: CurrentUser = Depends(get_current_user),
    supabase=Depends(get_user_client),
    llm=Depends(get_llm),
    config: PlatformConfig = Depends(get_config),
) -> CvOut:
    markdown = (body.markdown or "").strip()
    if not markdown:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty markdown")
    short_profile = make_short_profile(markdown, llm, config)
    _upsert_cv(supabase, user.user_id, markdown, short_profile)
    return CvOut(markdown=markdown, short_profile=short_profile)
