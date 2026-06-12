"""Pure filtering / classification helpers.

No network, no files, no globals mutated. These functions decide whether a
vacancy is relevant (role match, not blocked), strictly remote, and which region
it belongs to. Logic is copied verbatim from the original job_finder — behaviour
must not change.
"""

from __future__ import annotations

import datetime as dt
import html
import re

# ---------------------------------------------------------------------------
# Keyword tables (filter data)
# ---------------------------------------------------------------------------
ROLE_KEYWORDS = [
    "technical support", "support engineer", "customer support", "customer success",
    "success engineer", "integration", "solutions engineer", "implementation",
    "application support", "it support", "support specialist", "service desk",
    "help desk", "helpdesk", "support analyst",
    # шире под поддержку
    "customer experience", "client support", "client services", "onboarding",
    "technical account", "support consultant", "support representative",
    # project management (начальный уровень) — product management убран как нерелевантный
    "project manager", "project coordinator", "project lead", "delivery manager",
    "technical project", "implementation manager", "scrum master", "delivery lead",
    "associate project", "program coordinator",
    # консультанты/данные, где SQL — твой профиль (не путать с sales/management consultant)
    "technical consultant", "implementation consultant", "solutions consultant", "data support",
]
NEGATIVE_TITLE_KEYWORDS = [
    "vice president", "business development", "account manager", "account executive",
    "generalist", "recruiter", "talent acquisition", "horticultur", "agronom",
    "turfgrass", "ai data", "data training", "data annotation", "ai trainer", "sdr", "bdr",
    "site reliability", "sre", "network engineer", "security engineer", "process engineer",
    "research engineer", "verification engineer", "data engineer", "software engineer",
    "qa engineer", "quality assurance", "architect", "systems engineer", "test engineer",
    "devops engineer", "machine learning", "ml engineer", "sales engineer",
    # сеньорность — не твой уровень с 2 годами опыта, режем чтобы не жечь слоты
    "senior", "principal", "director", "head of", "staff ", "chief",
]

EU_COUNTRY_NAMES = {
    "poland", "germany", "netherlands", "spain", "portugal", "ireland", "france",
    "sweden", "italy", "belgium", "austria", "denmark", "finland", "czech", "romania",
    "united kingdom", "uk", "estonia", "lithuania", "latvia", "greece", "hungary",
}

WORLDWIDE_HINTS = ["worldwide", "anywhere", "work from anywhere", "global", "fully remote"]
EUROPE_HINTS = ["europe", "emea", "cet ", "eu remote", "european", "within the eu",
                "middle east", "united arab emirates", "uae", "dubai", "abu dhabi", "gulf"]
US_ONLY_HINTS = ["us only", "u.s. only", "united states only", "must be located in the us",
                 "us-based", "authorized to work in the united states", "must reside in the us"]

# --- строго удалённый формат ---
HYBRID_TITLE = ["hybrid", "on-site", "on site", "onsite", "in-office", "in office",
                "in-person", "in person"]
HYBRID_DESC = [
    # гибрид
    "hybrid role", "hybrid work", "hybrid model", "hybrid setup", "hybrid position",
    "hybrid working", "hybrid schedule", "this is a hybrid", "role is hybrid",
    "position is hybrid", "remote/hybrid", "hybrid/remote", "hybrid remote",
    "partially remote", "partial remote",
    # присутствие в офисе
    "days in the office", "days per week in the office", "days a week in the office",
    "days in office", "day in the office", "days on-site", "days onsite", "days on site",
    "days in person", "on-site presence", "onsite presence", "on site presence",
    "office-based", "office based", "based in the office", "work from the office",
    "office attendance", "come to the office", "commutable", "commuting distance",
    # явно не удалёнка
    "onsite only", "on-site only", "on site only", "in-office only",
    "fully on-site", "fully onsite", "100% on-site", "100% onsite", "no remote work",
    "not a remote position", "not a remote role", "this position is not remote",
    "no remote option",
    # формулировки-промахи + присутствие в описании
    "in person", "in-person", "work model: hybrid", "remote/ hybrid", "remote / hybrid",
    "hybrid / remote", "hybrid solution",
    # не английские (DE/FR/ES/IT)
    "hybride", "hybrides", "présentiel", "presentiel", "vor ort", "im büro", "büro tage",
    "híbrido", "hibrido", "ibrido", "in sede", "en oficina", "presencial",
    # маркеры из реальных описаний JobSpy
    "li-hybrid", "physical presence at the workplace", "presence at the workplace expected",
    "split your time between", "do not offer remote",
]
# Позитивные признаки удалёнки — без них (и без плашки remote) вакансию НЕ считаем удалённой.
REMOTE_SIGNALS = [
    "remote", "work from home", "wfh", "fully remote", "100% remote",
    "remote-first", "remote first", "work from anywhere", "telecommute", "distributed team",
]


# ---------------------------------------------------------------------------
# Title / role filters
# ---------------------------------------------------------------------------
def matches_role(text: str) -> bool:
    t = (text or "").lower()
    return any(kw in t for kw in ROLE_KEYWORDS)


def blocked(title: str) -> bool:
    t = (title or "").lower()
    return any(kw in t for kw in NEGATIVE_TITLE_KEYWORDS)


def classify_region(location, title, desc, hint_country=None) -> str:
    blob = " ".join(str(x).lower() for x in (location, title, desc) if x)
    if any(h in blob for h in US_ONLY_HINTS):
        return "US-ONLY"
    if any(h in blob for h in WORLDWIDE_HINTS):
        return "WORLDWIDE"
    if any(h in blob for h in EUROPE_HINTS) or any(c in blob for c in EU_COUNTRY_NAMES):
        return "EUROPE"
    if hint_country and hint_country.lower() in EU_COUNTRY_NAMES:
        return "EUROPE"
    return "UNKNOWN"


def remote_ok(title: str, desc: str, is_remote_flag=None) -> bool:
    """Строго удалённые. Режем hybrid/on-site по названию и фразам, и ТРЕБУЕМ позитивный
    признак удалёнки (плашка remote ИЛИ явное упоминание). Иначе считаем роль не удалённой —
    Indeed часто помечает гибрид/офис как 'remote'."""
    t = (title or "").lower().replace("\\", "")   # JobSpy отдаёт markdown: 'on\-site' -> 'on-site'
    d = (desc or "").lower().replace("\\", "")
    blob = t + " " + d
    if any(k in t for k in HYBRID_TITLE):
        return False
    if any(p in blob for p in HYBRID_DESC):
        return False
    if (("relocation required" in d or "must relocate" in d or "required to relocate" in d)
            and "no relocation" not in d):           # не путать с "no relocation required"
        return False
    if is_remote_flag is False:                      # плашка явно не remote
        return False
    if is_remote_flag is True:                        # плашка remote — доверяем
        return True
    return any(sig in blob for sig in REMOTE_SIGNALS)  # иначе нужен явный признак удалёнки


# ---------------------------------------------------------------------------
# Text / date / value utilities
# ---------------------------------------------------------------------------
def strip_html(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def within_hours(date_str: str, hours: int) -> bool:
    """True, если дата в пределах окна. Если дату не распарсить — НЕ отсекаем."""
    if not date_str:
        return True
    try:
        d = dt.datetime.fromisoformat(str(date_str).strip().replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return True
    return d >= dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)


def parse_remote_flag(v):
    """Бейдж is_remote из JobSpy -> True/False/None."""
    if v is None:
        return None
    sv = str(v).strip().lower()
    if sv in ("true", "1", "yes"):
        return True
    if sv in ("false", "0", "no"):
        return False
    return None


def s(val) -> str:
    if val is None:
        return ""
    txt = str(val)
    return "" if txt.lower() == "nan" else txt


# ---------------------------------------------------------------------------
# Dedup keying
# ---------------------------------------------------------------------------
def compute_dedup_key(company: str, title: str, url: str = "") -> str:
    """Ключ по компании+названию (нормализованным), чтобы одна вакансия из поиска
    по разным странам с разными ссылками считалась за одну."""
    t = re.sub(r"[^a-z0-9]+", "", (title or "").lower())
    c = re.sub(r"[^a-z0-9]+", "", (company or "").lower())
    if c or t:
        return f"{c}|{t}"
    return (url or "").lower()


def dedupe(jobs: list) -> list:
    """In-run dedup of Job objects by their precomputed dedup_key."""
    seen, out = set(), []
    for j in jobs:
        k = j.dedup_key
        if k in seen:
            continue
        seen.add(k)
        out.append(j)
    return out
