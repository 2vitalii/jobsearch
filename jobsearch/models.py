"""Data and configuration types for the jobsearch core.

Pure dataclasses — no logic, no I/O. These replace the loose dicts and module
globals that the original CLI prototype passed around.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Domain data
# ---------------------------------------------------------------------------
@dataclass
class Job:
    """A single scraped vacancy. ``dedup_key`` is the cross-source identity
    (company|title, see filters.compute_dedup_key)."""
    dedup_key: str
    source: str
    url: str
    company: str
    title: str
    location: str
    region: str
    description: str
    date_posted: str = ""
    scraped_at: str = ""


# ---------------------------------------------------------------------------
# Two independent axes of configuration (kept separate on purpose)
# ---------------------------------------------------------------------------
@dataclass
class SearchParams:
    """What the *user* asks for. Replaces the SEARCH_TERMS / COUNTRIES / LOOSE /
    TARGETED globals of the original finder."""
    keywords: list[str]
    locations: list[str]
    period_hours: int = 168
    work_format: str = "remote"
    loose: bool = False
    targeted: bool = False
    exclude_senior: bool = False  # when True, SENIORITY_KEYWORDS are applied by filters.blocked()


@dataclass
class PlatformConfig:
    """How the *platform* runs. Replaces the MODEL_* / MIN_FIT / PRE_MIN_FIT /
    MAX_JOBS globals. The Anthropic API key is NOT here — it is read from the
    environment by the LLM client (see scoring.AnthropicClient)."""
    model_score: str = "claude-haiku-4-5-20251001"
    model_tailor: str = "claude-sonnet-4-6"
    min_fit: int = 45
    pre_min_fit: int = 35
    max_jobs: int = 250
    process_regions: frozenset[str] = frozenset({"WORLDWIDE", "EUROPE", "UNKNOWN"})


# ---------------------------------------------------------------------------
# Scoring / tailoring results
# ---------------------------------------------------------------------------
@dataclass
class PreScore:
    """Cheap Haiku pre-filter result."""
    fit_score: int
    b2b: str
    reason: str

    @classmethod
    def from_dict(cls, d: dict) -> "PreScore":
        return cls(
            fit_score=int(d.get("fit_score", 0) or 0),
            b2b=d.get("b2b_eligible", ""),
            reason=d.get("reason", ""),
        )


@dataclass
class MatchResult:
    """Full Sonnet tailoring result (mirrors the JSON schema from the original
    pipeline SYSTEM prompt)."""
    fit_score: int
    b2b: str
    reason: str
    jd_keywords: list
    ats_present: list
    ats_missing: list
    tailored_summary: str
    tailored_skills: list
    gaps: str
    recruiter_verdict: str
    cover_letter: str

    @classmethod
    def from_dict(cls, d: dict) -> "MatchResult":
        return cls(
            fit_score=int(d.get("fit_score", 0) or 0),
            b2b=d.get("b2b_eligible", ""),
            reason=d.get("reason", ""),
            jd_keywords=d.get("jd_keywords", []) or [],
            ats_present=d.get("ats_present", []) or [],
            ats_missing=d.get("ats_missing", []) or [],
            tailored_summary=d.get("tailored_summary", ""),
            tailored_skills=d.get("tailored_skills", []) or [],
            gaps=d.get("gaps", ""),
            recruiter_verdict=d.get("recruiter_verdict", ""),
            cover_letter=d.get("cover_letter", ""),
        )


@dataclass
class Package:
    """A generated application kit, held entirely in memory. Where the bytes are
    written (local folder now, object storage later) is the orchestration's job,
    not render's."""
    cv_docx: bytes
    cover_letter: str
    ats_report: str
