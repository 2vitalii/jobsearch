"""Job collection from every source: JobSpy (LinkedIn/Indeed/Google), RemoteOK,
We Work Remotely, Remotive, and ATS boards (Greenhouse/Lever/Ashby).

``scrape(params, config) -> list[Job]`` is **pure relative to state**: it never
reads or writes the seen/processed logs. Cache invalidation ("did we scrape this
query recently?", query_hash + TTL) is the orchestration layer's job (it consults
JobStore) and MUST NOT be wired inside scrape().

Scraped descriptions are UNTRUSTED text; downstream they only ever go into the
LLM user position and through safe_name() before touching a filesystem path.
"""

from __future__ import annotations

import datetime as dt
import html
import os
import re
import sys

try:
    import requests
except ImportError:
    sys.exit("Нет requests. Установи: pip install python-jobspy feedparser requests")
try:
    import feedparser
except ImportError:
    sys.exit("Нет feedparser. Установи: pip install feedparser")
try:
    from jobspy import scrape_jobs
except ImportError:
    sys.exit("Нет JobSpy. Установи: pip install python-jobspy")

from . import filters
from .models import Job, SearchParams, PlatformConfig


# ---------------------------------------------------------------------------
# Debug flag helper (purely additive — no effect when env var is absent/off)
# ---------------------------------------------------------------------------
def _filter_debug() -> bool:
    """Return True when FILTER_DEBUG env var is set to a truthy value."""
    return os.getenv("FILTER_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# JobSpy freshness post-filter
# ---------------------------------------------------------------------------


def _jobspy_fresh(date_str: str | None, hours: int) -> bool:
    """True if a JobSpy row is within the freshness window.

    - Parseable date: delegates to filters.within_hours (do not duplicate).
    - Empty / unparseable date: always returns True (keep).  Rationale: JobSpy
      passes hours_old to LinkedIn which already filtered freshness server-side;
      a missing date is an extraction gap, not evidence of staleness.  Dropping
      these rows was silently discarding fresh LinkedIn results.

    NOTE: do NOT change filters.within_hours — its lenient empty→True behaviour
    is intentional for RSS/ATS boards where missing dates are normal and benign.
    """
    s = (date_str or "").strip()
    if s:
        try:
            dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            return filters.within_hours(s, hours)   # parseable → real window check
        except Exception:
            pass
    return True   # empty / unparseable → keep (JobSpy hours_old already applied server-side)


# ---------------------------------------------------------------------------
# Scraper settings (not user request, not platform tuning — source plumbing)
# ---------------------------------------------------------------------------
JOBSPY_SITES = ["linkedin", "indeed", "google"]  # zip_recruiter=403, glassdoor сыпет ошибки
RESULTS_WANTED = 15        # на каждый источник/термин/страну (меньше = быстрее)
LINKEDIN_FETCH_DESC = True # тянуть полные описания LinkedIn (медленнее, больше лимитов)
PROXIES = None             # ["user:pass@host:port", ...] — нужно для объёма на LinkedIn
USE_REMOTE_BOARDS = True   # RemoteOK + We Work Remotely + Remotive (worldwide)
USE_ATS = True             # Greenhouse / Lever / Ashby

# Indeed принимает страну как домен; здесь мэппинг нестандартных имён.
INDEED_COUNTRY = {"United Kingdom": "uk", "Czech Republic": "czech republic"}

# Допустимые строки стран JobSpy (для Indeed). Если локация НЕ отсюда — считаем её
# регионом (напр. "European Economic Area") и ищем только в LinkedIn+Google.
VALID_JOBSPY_COUNTRIES = {
    "argentina", "australia", "austria", "bahrain", "bangladesh", "belgium", "bulgaria", "brazil",
    "canada", "chile", "china", "colombia", "costa rica", "croatia", "cyprus", "czech republic", "czechia",
    "denmark", "ecuador", "egypt", "estonia", "finland", "france", "germany", "greece", "hong kong", "hungary",
    "india", "indonesia", "ireland", "israel", "italy", "japan", "kuwait", "latvia", "lithuania", "luxembourg",
    "malaysia", "malta", "mexico", "morocco", "netherlands", "new zealand", "nigeria", "norway", "oman",
    "pakistan", "panama", "peru", "philippines", "poland", "portugal", "qatar", "romania", "saudi arabia",
    "singapore", "slovakia", "slovenia", "south africa", "south korea", "spain", "sweden", "switzerland",
    "taiwan", "thailand", "türkiye", "turkey", "ukraine", "united arab emirates", "uk", "united kingdom",
    "usa", "us", "united states", "uruguay", "venezuela", "vietnam", "worldwide",
}

# ---------------------------------------------------------------------------
# Query-combination cap (rate-limit / cost guard)
# ---------------------------------------------------------------------------

# Maximum number of (keyword, location) scrape_jobs calls issued per run.
# Protects against rate-limit exhaustion and LLM-scoring cost explosions when
# the user configures many roles × many locations.
MAX_QUERY_COMBINATIONS = 18

# ---------------------------------------------------------------------------
# Location normalization constants + helper
# ---------------------------------------------------------------------------

# Top EU job markets using EXACT canonical names from VALID_JOBSPY_COUNTRIES.
# Deliberately a curated subset (~8 markets) so that EU-expansion + a couple of
# role keywords stays under the MAX_QUERY_COMBINATIONS cap introduced in C2.
# Do NOT derive from filters.EU_COUNTRY_NAMES — it contains "czech" (not a valid
# JobSpy country; should be "czech republic"/"czechia") and uk/united-kingdom dupes.
EU_EXPANSION_COUNTRIES = [
    "germany", "netherlands", "ireland", "poland", "spain", "france", "sweden", "italy",
]

# Input strings (lower-cased, stripped) that should be expanded to EU_EXPANSION_COUNTRIES.
REGION_ALIASES: set[str] = {"european union", "eu", "emea", "europe"}

# Regex that strips work-format suffixes ANCHORED TO THE END of the string only.
# Matches: " (Remote)", " (Hybrid)", " (On-site)", " (Onsite)", " (On site)"
#       or " - Remote", " — Remote", " - Hybrid", " — On-site", etc.
# Internal occurrences (e.g. "Remote Foods Inc (Remote)" → keep "Remote Foods Inc")
# are handled automatically because we anchor with `$`.
_SUFFIX_RE = re.compile(
    r"(?:"
    r"\s*\(\s*(?:remote|hybrid|on[\-\s]?site|onsite)\s*\)"   # trailing (Remote|Hybrid|On-site|…)
    r"|"
    r"\s*[-—]\s*(?:remote|hybrid|on[\-\s]?site|onsite)"  # trailing – Remote / — Hybrid / …
    r")\s*$",
    re.IGNORECASE,
)


def _normalize_locations(locations: list[str]) -> list[str]:
    """Normalize and optionally expand a list of raw user-supplied location strings.

    Steps (applied per entry):
    1. Strip work-format suffixes anchored to the END of the string (case-insensitive).
       e.g. "Poland (Remote)" -> "Poland";  "Remote Foods Inc (Remote)" -> "Remote Foods Inc".
    2. If the cleaned lower-cased string is a REGION_ALIAS -> expand to EU_EXPANSION_COUNTRIES.
    3. Else if not in VALID_JOBSPY_COUNTRIES -> keep as-is (best-effort passthrough to
       LinkedIn+Google) but emit a clear warning.
    4. Deduplicate the resulting list preserving order (EU expansion may overlap an
       explicitly-added country).

    Returns the normalized/expanded list.
    """
    result: list[str] = []
    seen: set[str] = set()

    for raw in locations:
        # Step 1: strip trailing work-format suffix.
        cleaned = _SUFFIX_RE.sub("", raw).strip()
        lower = cleaned.lower()

        # Step 2: region alias -> expand.
        if lower in REGION_ALIASES:
            print(f"  [locations] '{raw}' → {EU_EXPANSION_COUNTRIES}")
            for country in EU_EXPANSION_COUNTRIES:
                if country not in seen:
                    seen.add(country)
                    result.append(country)
        else:
            # Step 3: validate against known JobSpy countries; warn on unknown.
            if lower not in VALID_JOBSPY_COUNTRIES:
                print(
                    f"  [locations] unrecognized '{cleaned}' → treated as region "
                    f"(LinkedIn+Google only), results may be sparse"
                )
            # Step 4: dedupe.
            key = lower  # use lower-cased form as dedup key, but append original cleaned string
            if key not in seen:
                seen.add(key)
                result.append(cleaned)

    return result


# (url, trust): customer-support — доверяем категории (там бывают нестандартные тайтлы);
# остальным применяем фильтр роли по названию.
WWR_FEEDS = [
    ("https://weworkremotely.com/categories/remote-customer-support-jobs.rss", True),
    ("https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss", False),
    ("https://weworkremotely.com/categories/remote-product-jobs.rss", False),
    ("https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss", False),
]
REMOTEOK_API = "https://remoteok.com/api"
REMOTIVE_API = "https://remotive.com/api/remote-jobs"
HEADERS = {"User-Agent": "job-finder-personal/1.0 (job search helper)"}
TIMEOUT = 25

# --- ATS-источники: прямые вакансии работодателя (Greenhouse / Lever / Ashby) ---
ATS_COMPANIES = [
    ("greenhouse", "gitlab"),
    ("greenhouse", "remote"),
    ("greenhouse", "doist"),
    ("lever", "zapier"),
    ("ashby", "deel"),
    ("ashby", "ashby"),
]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _mk_job(*, source, title, company, location, region, url, date_posted, description) -> Job:
    return Job(
        dedup_key=filters.compute_dedup_key(company, title, url),
        source=source, url=url, company=company, title=title, location=location,
        region=region, description=description, date_posted=date_posted, scraped_at=_now(),
    )


# ---------------------------------------------------------------------------
# Источники
# ---------------------------------------------------------------------------
def collect_jobspy(params: SearchParams) -> list:
    is_remote = params.work_format == "remote"
    jobs = []
    debug = _filter_debug()

    # Normalize + expand locations before the loop (strip suffixes, expand EU/EMEA aliases).
    locations = _normalize_locations(params.locations)
    keywords = params.keywords

    # Always-on combination summary (not behind FILTER_DEBUG).
    total_combos = len(keywords) * len(locations)
    issued = min(total_combos, MAX_QUERY_COMBINATIONS)
    skipped = total_combos - issued
    print(
        f"  [scrape] {len(keywords)} keywords × {len(locations)} locations"
        f" = {total_combos} combos; issuing {issued} (cap {MAX_QUERY_COMBINATIONS})"
        + (f"; skipped {skipped}" if skipped > 0 else "")
    )

    # Always-on stale-drop counter (visible in normal runs, not just FILTER_DEBUG).
    _stale_dropped: int = 0

    # Run-level grand totals (accumulated across all country/term combos).
    if debug:
        _total_raw: dict[str, int] = {}
        _total_kept: int = 0
        _total_dropped: dict[str, int] = {
            "empty_title": 0, "blocked": 0, "not_role": 0, "not_remote": 0, "not_fresh": 0,
        }
        _total_samples: dict[str, list[str]] = {
            "empty_title": [], "blocked": [], "not_role": [], "not_remote": [], "not_fresh": [],
        }

    # Global counter of issued (keyword, location) pairs.
    _combos_issued: int = 0
    _cap_reached = False

    for country in locations:
        if _cap_reached:
            break
        is_region = country.strip().lower() not in VALID_JOBSPY_COUNTRIES
        if is_region:
            sites = [x for x in JOBSPY_SITES if x != "indeed"] or ["linkedin"]
            ci = "usa"   # валидная заглушка; Indeed исключён и не используется
        else:
            sites = JOBSPY_SITES
            ci = INDEED_COUNTRY.get(country, country.strip().lower())
        for term in keywords:
            if _combos_issued >= MAX_QUERY_COMBINATIONS:
                _cap_reached = True
                break
            _combos_issued += 1
            try:
                df = scrape_jobs(
                    site_name=sites,
                    search_term=term,
                    google_search_term=f"{term} remote {country}",
                    location=country,
                    is_remote=is_remote,
                    results_wanted=RESULTS_WANTED,
                    hours_old=params.period_hours,
                    country_indeed=ci,
                    description_format="markdown",
                    linkedin_fetch_description=LINKEDIN_FETCH_DESC,
                    proxies=PROXIES,
                    verbose=0,
                )
            except Exception as e:
                print(f"  [{country}/'{term}'] пропущен: {e}")
                continue
            if df is None or df.empty:
                continue
            cnt = 0

            # Per-(country,term) debug counters — only allocated when FILTER_DEBUG is on.
            if debug:
                _raw_by_site: dict[str, int] = {}
                _dropped: dict[str, int] = {
                    "empty_title": 0, "blocked": 0, "not_role": 0, "not_remote": 0, "not_fresh": 0,
                }
                _samples: dict[str, list[str]] = {
                    "empty_title": [], "blocked": [], "not_role": [], "not_remote": [], "not_fresh": [],
                }

            for _, row in df.iterrows():
                title = filters.s(row.get("title"))
                desc = filters.s(row.get("description"))

                # --- FILTER_DEBUG: tally raw rows and compute first-tripped gate ---
                # This block is counting-only; the real decision below is NOT changed.
                if debug:
                    site_raw = filters.s(row.get("site")).strip().lower() or "unknown"
                    _raw_by_site[site_raw] = _raw_by_site.get(site_raw, 0) + 1
                    _total_raw[site_raw] = _total_raw.get(site_raw, 0) + 1

                # --- The real 1st-pass filter decision (role_keywords + block_seniority from the user's SearchParams) ---
                if (not title or filters.blocked(title, block_seniority=params.exclude_senior)
                        or (not params.loose and not filters.matches_role(title, params.keywords))
                        or not filters.remote_ok(title, desc, filters.parse_remote_flag(row.get("is_remote")))):
                    # --- FILTER_DEBUG: attribute the reason for this drop ---
                    if debug:
                        if not title:
                            gate = "empty_title"
                        elif filters.blocked(title, block_seniority=params.exclude_senior):
                            gate = "blocked"
                        elif not params.loose and not filters.matches_role(title, params.keywords):
                            gate = "not_role"
                        else:
                            gate = "not_remote"
                        _dropped[gate] += 1
                        _total_dropped[gate] += 1
                        # Collect up to 5 example titles per gate (first-seen, truncated).
                        if gate != "empty_title" and len(_samples[gate]) < 5:
                            _samples[gate].append(title[:80])
                        if gate != "empty_title" and len(_total_samples[gate]) < 5:
                            _total_samples[gate].append(title[:80])
                    continue

                # --- Freshness post-filter (JobSpy-specific gate) ---
                # rows.get("date_posted") may be empty (~16% of JobSpy rows —
                # fresh LinkedIn results without a machine-readable date tag).
                # _jobspy_fresh KEEPS empty/unparseable dates and drops only a
                # date that is present AND older than the window.
                row_date = filters.s(row.get("date_posted"))
                if not _jobspy_fresh(row_date, params.period_hours):
                    _stale_dropped += 1
                    if debug:
                        _dropped["not_fresh"] += 1
                        _total_dropped["not_fresh"] += 1
                        if len(_samples["not_fresh"]) < 5:
                            _samples["not_fresh"].append(title[:80])
                        if len(_total_samples["not_fresh"]) < 5:
                            _total_samples["not_fresh"].append(title[:80])
                    continue

                jobs.append(_mk_job(
                    source=filters.s(row.get("site")), title=title,
                    company=filters.s(row.get("company")),
                    location=filters.s(row.get("location")) or country,
                    region=filters.classify_region(row.get("location"), title, desc, country),
                    url=filters.s(row.get("job_url")),
                    date_posted=row_date, description=desc,
                ))
                cnt += 1

            note = " (регион -> LinkedIn+Google, Indeed пропущен)" if is_region else ""
            # Existing line — keep exactly as-is.
            print(f"  [{country}/'{term}'] подходящих: {cnt}{note}")

            if debug:
                _total_kept += cnt
                print(
                    f"  [FILTER_DEBUG {country}/'{term}'] "
                    f"raw_by_site={_raw_by_site} "
                    f"kept={cnt} "
                    f"dropped={_dropped}"
                )
                for gate_name, examples in _samples.items():
                    if examples:
                        print(f"    sample {gate_name}: {examples}")

    if debug:
        print(
            f"  [FILTER_DEBUG jobspy TOTAL] "
            f"raw_by_site={_total_raw} "
            f"kept={_total_kept} "
            f"dropped={_total_dropped}"
        )
        for gate_name, examples in _total_samples.items():
            if examples:
                print(f"    sample {gate_name}: {examples}")

    # Always-on freshness counter (visible in every run, not only FILTER_DEBUG).
    print(
        f"  [freshness] JobSpy stale dropped "
        f"(date present & older than window): {_stale_dropped}"
    )

    return jobs


def fetch_remoteok(hours: int, role_keywords: list[str] | None = None, block_seniority: bool = True) -> list:
    jobs = []
    try:
        r = requests.get(REMOTEOK_API, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [RemoteOK] пропущен: {e}")
        return jobs
    for item in data:
        if not isinstance(item, dict) or "position" not in item:
            continue
        title = html.unescape(item.get("position", ""))
        desc = filters.strip_html(item.get("description", ""))
        if (filters.blocked(title, block_seniority=block_seniority)
                or not filters.matches_role(title, role_keywords)
                or not filters.remote_ok(title, desc, True)):
            continue
        if not filters.within_hours(item.get("date", ""), hours):
            continue
        loc = item.get("location", "") or "Remote"
        jobs.append(_mk_job(
            source="RemoteOK", title=title, company=html.unescape(item.get("company", "")),
            location=loc, region=filters.classify_region(loc, title, desc),
            url=item.get("url", ""), date_posted=(item.get("date", "") or "")[:10], description=desc,
        ))
    print(f"  [RemoteOK] подходящих: {len(jobs)}")
    return jobs


def fetch_wwr(hours: int, role_keywords: list[str] | None = None, block_seniority: bool = True) -> list:
    jobs = []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    for feed_url, trust in WWR_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"  [WWR] {feed_url} пропущен: {e}")
            continue
        cnt = 0
        for entry in feed.entries:
            title = html.unescape(entry.get("title", ""))
            summary = filters.strip_html(entry.get("summary", ""))
            if filters.blocked(title, block_seniority=block_seniority):
                continue
            if not trust and not filters.matches_role(title, role_keywords):   # категории шире support — фильтр по роли
                continue
            if not filters.remote_ok(title, summary, True):
                continue
            pp = entry.get("published_parsed")
            if pp:
                pub = dt.datetime(*pp[:6], tzinfo=dt.timezone.utc)
                if pub < cutoff:
                    continue
            jobs.append(_mk_job(
                source="WeWorkRemotely", title=title,
                company=title.split(":")[0].strip() if ":" in title else "",
                location=entry.get("region", "") or "Remote",
                region=filters.classify_region(entry.get("region", ""), title, summary),
                url=entry.get("link", ""), date_posted=(entry.get("published", "") or "")[:16],
                description=summary,
            ))
            cnt += 1
        print(f"  [WWR] {feed_url.split('/')[-1]}: {cnt}")
    return jobs


def fetch_remotive(hours: int, role_keywords: list[str] | None = None, block_seniority: bool = True) -> list:
    jobs = []
    try:
        r = requests.get(REMOTIVE_API, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json().get("jobs", [])
    except Exception as e:
        print(f"  [Remotive] пропущен: {e}")
        return jobs
    for it in data:
        title = html.unescape(it.get("title", ""))
        desc = filters.strip_html(it.get("description", ""))
        if (filters.blocked(title, block_seniority=block_seniority)
                or not filters.matches_role(title, role_keywords)
                or not filters.remote_ok(title, desc, True)):
            continue
        if not filters.within_hours(it.get("publication_date", ""), hours):
            continue
        loc = it.get("candidate_required_location", "") or "Remote"
        jobs.append(_mk_job(
            source="Remotive", title=title, company=html.unescape(it.get("company_name", "")),
            location=loc, region=filters.classify_region(loc, title, desc),
            url=it.get("url", ""), date_posted=(it.get("publication_date", "") or "")[:10], description=desc,
        ))
    print(f"  [Remotive] подходящих: {len(jobs)}")
    return jobs


def _ats_loc_onsite(loc: str) -> bool:
    ll = (loc or "").lower()
    return (any(k in ll for k in ("hybrid", "on-site", "on site", "onsite", "in-office", "in office"))
            and "remote" not in ll)


def _ats_greenhouse(slug: str, hours: int, role_keywords: list[str] | None = None, block_seniority: bool = True) -> list:
    out = []
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        title = j.get("title", "")
        loc = (j.get("location") or {}).get("name", "")
        desc = filters.strip_html(j.get("content", ""))
        if filters.blocked(title, block_seniority=block_seniority) or not filters.matches_role(title, role_keywords):
            continue
        if not filters.within_hours(j.get("updated_at", ""), hours):
            continue
        if _ats_loc_onsite(loc) or not filters.remote_ok(title, loc + ". " + desc, None):
            continue
        out.append(_mk_job(
            source="Greenhouse", title=title, company=slug, location=loc or "Remote",
            region=filters.classify_region(loc, title, desc),
            url=j.get("absolute_url", ""), date_posted=(j.get("updated_at", "") or "")[:10], description=desc,
        ))
    return out


def _ats_lever(slug: str, hours: int, role_keywords: list[str] | None = None, block_seniority: bool = True) -> list:
    out = []
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    for j in r.json():
        title = j.get("text", "")
        cats = j.get("categories") or {}
        loc = cats.get("location", "") or ""
        desc = j.get("descriptionPlain", "") or filters.strip_html(j.get("description", ""))
        if filters.blocked(title, block_seniority=block_seniority) or not filters.matches_role(title, role_keywords):
            continue
        created = j.get("createdAt")
        if created:
            try:
                if dt.datetime.fromtimestamp(created / 1000, dt.timezone.utc) < cutoff:
                    continue
            except Exception:
                pass
        if _ats_loc_onsite(loc) or not filters.remote_ok(title, loc + ". " + desc, None):
            continue
        out.append(_mk_job(
            source="Lever", title=title, company=slug, location=loc or "Remote",
            region=filters.classify_region(loc, title, desc),
            url=j.get("hostedUrl", ""), date_posted="", description=desc,
        ))
    return out


def _ats_ashby(slug: str, hours: int, role_keywords: list[str] | None = None, block_seniority: bool = True) -> list:
    out = []
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        title = j.get("title", "")
        loc = j.get("location", "") or ""
        desc = j.get("descriptionPlain", "") or filters.strip_html(j.get("description", ""))
        flag = j.get("isRemote")
        flag = bool(flag) if flag is not None else None
        if filters.blocked(title, block_seniority=block_seniority) or not filters.matches_role(title, role_keywords):
            continue
        if not filters.within_hours(str(j.get("publishedAt", "") or j.get("publishedDate", "")), hours):
            continue
        if _ats_loc_onsite(loc) or not filters.remote_ok(title, loc + ". " + desc, flag):
            continue
        out.append(_mk_job(
            source="Ashby", title=title, company=slug, location=loc or "Remote",
            region=filters.classify_region(loc, title, desc),
            url=j.get("jobUrl", "") or j.get("applyUrl", ""),
            date_posted=str(j.get("publishedAt", "") or "")[:10], description=desc,
        ))
    return out


def fetch_ats(hours: int, role_keywords: list[str] | None = None, block_seniority: bool = True) -> list:
    handlers = {"greenhouse": _ats_greenhouse, "lever": _ats_lever, "ashby": _ats_ashby}
    jobs = []
    for platform, slug in ATS_COMPANIES:
        h = handlers.get(platform)
        if not h:
            print(f"  [ATS] неизвестная платформа: {platform}")
            continue
        try:
            got = h(slug, hours, role_keywords=role_keywords, block_seniority=block_seniority)
            jobs += got
            print(f"  [{platform}/{slug}] подходящих: {len(got)}")
        except Exception as e:
            print(f"  [{platform}/{slug}] пропущен: {e}")
    return jobs


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def scrape(params: SearchParams, config: PlatformConfig | None = None) -> list:
    """Collect jobs across all configured sources and dedup within the run.
    State-pure: does not consult seen/processed logs. Returns list[Job]."""
    role_keywords = params.keywords
    block_seniority = params.exclude_senior
    collected = collect_jobspy(params)
    if not params.targeted and USE_REMOTE_BOARDS:
        collected += fetch_remoteok(params.period_hours, role_keywords=role_keywords, block_seniority=block_seniority)
        collected += fetch_wwr(params.period_hours, role_keywords=role_keywords, block_seniority=block_seniority)
        collected += fetch_remotive(params.period_hours, role_keywords=role_keywords, block_seniority=block_seniority)
    if not params.targeted and USE_ATS:
        collected += fetch_ats(params.period_hours, role_keywords=role_keywords, block_seniority=block_seniority)
    return filters.dedupe(collected)
