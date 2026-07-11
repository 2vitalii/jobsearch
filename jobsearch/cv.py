"""CV intake: turn a raw résumé into the master_cv.md format, and condense it
into a short profile used by score_fit.

Pure functions behind the same injectable ``LLMClient`` seam as scoring — no
network, no files here; the real Anthropic call lives behind the client, so tests
run with a fake and zero cost. The tailoring model comes from
``PlatformConfig.model_tailor``.

Security: the résumé text is UNTRUSTED (user-uploaded). It goes ONLY into the
user message, never into the system instructions — same invariant as scoring, so
prompt-injection blast radius stays near zero.

HONESTY-FIRST: the model may only restate facts present in the source résumé. It
must never invent metrics, technologies, employers, dates, or experience. Missing
things stay missing — they are simply not written.
"""

from __future__ import annotations

from .models import PlatformConfig
from .scoring import LLMClient, parse_json

# Target shape — the exact section skeleton of master_cv.md.
PARSE_SYSTEM = (
    "You convert a raw résumé into clean Markdown following a FIXED structure. "
    "Output ONLY the Markdown document — no code fences, no preamble, no commentary.\n"
    "\n"
    "Use EXACTLY these sections, in this order (omit a section only if the résumé "
    "truly has nothing for it):\n"
    "# <Full Name>\n"
    "<one headline line: role / title>\n"
    "<one contact line: location · phone · email — only the contacts present>\n"
    "\n"
    "## Professional Summary\n"
    "## Core Skills\n"
    "## Professional Experience\n"
    "## Project Experience\n"
    "## Education\n"
    "## Additional Information\n"
    "\n"
    "Rules for Professional/Project Experience: one '### <Role> — <Company>' (or "
    "project name) heading per entry, a date/context line under it, then '- ' "
    "bullets. Core Skills and Additional Information are '- ' bullet lists.\n"
    "\n"
    "STRICT HONESTY: use ONLY facts that appear in the résumé. Do NOT invent or "
    "embellish metrics, technologies, employers, titles, dates, or achievements. "
    "If something is not in the source, leave it out. Do not add a section just to "
    "fill the template. Keep the candidate's wording where possible; only reshape "
    "it into the structure above. Write in English."
)

PROFILE_SYSTEM = (
    "You write a SHORT candidate profile (2-4 lines, plain text, no Markdown) that "
    "score_fit uses to judge job relevance. Summarize the candidate's real role, "
    "core stack, years of experience, and work arrangement.\n"
    "\n"
    "STRICT HONESTY: only facts from the provided CV. Invent nothing — no skills, "
    "metrics, or experience that are not already written. Output ONLY the profile "
    "text, no labels or preamble. Write in English."
)


def parse_cv(resume_text: str, llm: LLMClient, config: PlatformConfig) -> str:
    """Turn raw résumé text (extracted from PDF/docx) into master_cv.md-shaped
    Markdown. Honesty-first: facts only, nothing invented."""
    out = llm.complete(
        model=config.model_tailor,
        system=PARSE_SYSTEM,
        messages=[{"role": "user", "content": f"RÉSUMÉ TEXT:\n\n{resume_text}"}],
        max_tokens=4096,
    )
    return out.strip()


def make_short_profile(markdown: str, llm: LLMClient, config: PlatformConfig) -> str:
    """Condense a master CV (Markdown) into a 2-4 line profile for score_fit."""
    out = llm.complete(
        model=config.model_tailor,
        system=PROFILE_SYSTEM,
        messages=[{"role": "user", "content": f"MASTER CV:\n\n{markdown}"}],
        max_tokens=512,
    )
    return out.strip()


SUGGEST_ROLES_SYSTEM = (
    "Extract 5-8 JOB TITLES / ROLES that this candidate can realistically search for based "
    "on their ACTUAL work experience described in the CV.\n"
    "\n"
    "STRICT RULES:\n"
    "- Return ONLY job titles / role names — e.g. 'Technical Support Engineer', "
    "'Implementation Specialist', 'Project Coordinator'.\n"
    "- EXPLICITLY EXCLUDE bare skills, tools, and technologies. Do NOT return items like "
    "SQL, Azure, Python, MQTT, Git, Docker, Kubernetes, JavaScript, React, AWS — these are "
    "skills, not roles, and pollute job searches.\n"
    "- Only include roles grounded in the candidate's real experience. Do not invent titles "
    "or seniority levels the person never held.\n"
    "- Order by relevance: the strongest match (most experience evidence) first.\n"
    "- Return a JSON array of strings and NOTHING else — no prose, no code fences, no labels.\n"
    "\n"
    "Example of correct output:\n"
    '[\"Technical Support Engineer\", \"Integration Specialist\", \"Customer Success Manager\"]'
)


def suggest_search_roles(
    cv_markdown: str,
    llm: LLMClient,
    config: PlatformConfig,
) -> list[str]:
    """Extract 5-8 job-title suggestions from the candidate's CV.

    Uses the same injectable LLMClient seam as parse_cv/make_short_profile, so
    tests can run with a fake and zero cost. Returns [] on any parse failure
    (robust fallback). Honesty-first: roles come from real experience only."""
    raw = llm.complete(
        model=config.model_tailor,
        system=SUGGEST_ROLES_SYSTEM,
        messages=[{"role": "user", "content": f"MASTER CV:\n\n{cv_markdown}"}],
        max_tokens=512,
    )
    try:
        parsed = parse_json(raw)
    except Exception:
        # If the LLM returned a JSON array (not an object) parse_json may fail.
        # Try a direct json.loads fallback for the array case.
        import json
        try:
            parsed = json.loads(raw.strip())
        except Exception:
            return []

    if not isinstance(parsed, list):
        return []
    roles: list[str] = []
    for item in parsed:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                roles.append(stripped)
    return roles[:8]
