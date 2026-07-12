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
    pre_min_fit: int = 20
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
class Assessment:
    """Full Sonnet assessment result — ONLY scoring/analysis fields.
    Structurally cannot contain tailored_summary/tailored_skills/cover_letter,
    so honesty invariant #1 is enforced at the type level."""
    fit_score: int
    b2b: str
    reason: str
    jd_keywords: list
    ats_present: list
    ats_missing: list
    gaps: str
    recruiter_verdict: str

    @classmethod
    def from_dict(cls, d: dict) -> "Assessment":
        return cls(
            fit_score=int(d.get("fit_score", 0) or 0),
            b2b=d.get("b2b_eligible", ""),
            reason=d.get("reason", ""),
            jd_keywords=d.get("jd_keywords", []) or [],
            ats_present=d.get("ats_present", []) or [],
            ats_missing=d.get("ats_missing", []) or [],
            gaps=d.get("gaps", ""),
            recruiter_verdict=d.get("recruiter_verdict", ""),
        )


@dataclass
class Generation:
    """Sonnet generation result — ONLY tailored CV/letter fields.
    Structurally cannot contain fit_score or assessment fields."""
    tailored_summary: str
    tailored_skills: list
    cover_letter: str

    @classmethod
    def from_dict(cls, d: dict) -> "Generation":
        return cls(
            tailored_summary=d.get("tailored_summary", ""),
            tailored_skills=d.get("tailored_skills", []) or [],
            cover_letter=d.get("cover_letter", ""),
        )


@dataclass
class MatchResult:
    """Full Sonnet tailoring result (mirrors the JSON schema from the original
    pipeline SYSTEM prompt). Used as the combined render input for build_package."""
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

    @classmethod
    def from_assessment_and_generation(cls, a: "Assessment", g: "Generation") -> "MatchResult":
        """Build a MatchResult from a separate Assessment + Generation pair.
        This allows render.build_package to remain unchanged while the upstream
        pipeline uses the split assess/generate functions."""
        return cls(
            fit_score=a.fit_score,
            b2b=a.b2b,
            reason=a.reason,
            jd_keywords=a.jd_keywords,
            ats_present=a.ats_present,
            ats_missing=a.ats_missing,
            tailored_summary=g.tailored_summary,
            tailored_skills=g.tailored_skills,
            gaps=a.gaps,
            recruiter_verdict=a.recruiter_verdict,
            cover_letter=g.cover_letter,
        )


@dataclass
class Package:
    """A generated application kit, held entirely in memory. Where the bytes are
    written (local folder now, object storage later) is the orchestration's job,
    not render's."""
    cv_docx: bytes
    cover_letter: str
    ats_report: str
