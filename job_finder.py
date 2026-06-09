#!/usr/bin/env python3
"""
job_finder.py — сбор вакансий из ВСЕХ доступных источников:
  • JobSpy: LinkedIn, Indeed, Google — по списку стран с remote
  • RemoteOK (API), We Work Remotely (RSS), Remotive (API) — worldwide-remote
  • ATS компаний: Greenhouse / Lever / Ashby — прямые вакансии работодателя
Всё с полными описаниями для ATS-тюнинга в pipeline.py.

Запуск:
  pip install python-jobspy feedparser requests
  python job_finder.py            # за неделю (168ч)
  python job_finder.py 24         # за последние 24 часа
  python job_finder.py 6          # за последние 6 часов (свежак / мало откликов)
"""

import csv
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

# ---------------------------------------------------------------------------
# НАСТРОЙКИ — правь под себя
# ---------------------------------------------------------------------------
JOBSPY_SITES = ["linkedin", "indeed", "google"]  # zip_recruiter=403, glassdoor сыпет ошибки

SEARCH_TERMS = [
    "support engineer",
    "application support",
    "support specialist",
    "integration specialist",
    "junior project manager",
    "project coordinator",
    "technical consultant",
    "implementation consultant",
    "solutions consultant",
    "data support specialist",
]

# Страны для поиска "<роль> remote <страна>". Больше стран = шире охват, но дольше прогон
# и сильнее throttle LinkedIn. Режь, если медленно (Gulf-рынок часто офисный).
COUNTRIES = ["Poland", "Germany", "Netherlands", "Spain", "Portugal", "Ireland",
             "France", "Sweden", "United Kingdom", "Italy", "Belgium", "Czech Republic",
             "United Arab Emirates", "Saudi Arabia", "Qatar"]

RESULTS_WANTED = 15        # на каждый источник/термин/страну (меньше = быстрее)
HOURS_OLD = 168            # окно по умолчанию — неделя
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

EU_COUNTRY_NAMES = {
    "poland", "germany", "netherlands", "spain", "portugal", "ireland", "france",
    "sweden", "italy", "belgium", "austria", "denmark", "finland", "czech", "romania",
    "united kingdom", "uk", "estonia", "lithuania", "latvia", "greece", "hungary",
}

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
# (платформа, slug). slug = идентификатор компании в URL её карьерной страницы:
#   Greenhouse: boards.greenhouse.io/<slug>   Lever: jobs.lever.co/<slug>   Ashby: jobs.ashbyhq.com/<slug>
# Это remote-first компании. СЛАГИ НИЖЕ — СТАРТОВЫЕ, ПРОВЕРЬ по логу ("[платформа/slug] N"):
# неверный slug вернёт 0 или ошибку — просто поправь/удали. Добавляй свои сюда.
ATS_COMPANIES = [
    ("greenhouse", "gitlab"),
    ("greenhouse", "remote"),
    ("greenhouse", "doist"),
    ("lever", "zapier"),
    ("ashby", "deel"),
    ("ashby", "ashby"),
]

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

SEEN_LOG = ".seen_jobs.txt"

# Переопределяются из CLI (--term/--location/--loose). Вручную не трогай.
LOOSE = False       # True -> доверяем ключевому слову, не режем предустановленным фильтром ролей
TARGETED = False    # True -> целевой поиск: только JobSpy по term×location, без worldwide-досок/ATS


# ---------------------------------------------------------------------------
# Фильтры и утилиты
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


def s(val) -> str:
    if val is None:
        return ""
    txt = str(val)
    return "" if txt.lower() == "nan" else txt


def job_key(j: dict) -> str:
    # ключ по компании+названию (нормализованным), чтобы одна вакансия из поиска
    # по разным странам с разными ссылками считалась за одну
    t = re.sub(r"[^a-z0-9]+", "", (j.get("title", "") or "").lower())
    c = re.sub(r"[^a-z0-9]+", "", (j.get("company", "") or "").lower())
    if c or t:
        return f"{c}|{t}"
    return (j.get("url", "") or "").lower()


def load_seen() -> set:
    if os.path.exists(SEEN_LOG):
        with open(SEEN_LOG, encoding="utf-8") as f:
            return {ln.strip() for ln in f if ln.strip()}
    return set()


def append_seen(keys) -> None:
    with open(SEEN_LOG, "a", encoding="utf-8") as f:
        for k in keys:
            f.write(k + "\n")


# ---------------------------------------------------------------------------
# Источники
# ---------------------------------------------------------------------------
def collect_jobspy(hours: int) -> list:
    jobs = []
    for country in COUNTRIES:
        is_region = country.strip().lower() not in VALID_JOBSPY_COUNTRIES
        if is_region:
            sites = [x for x in JOBSPY_SITES if x != "indeed"] or ["linkedin"]
            ci = "usa"   # валидная заглушка; Indeed исключён и не используется
        else:
            sites = JOBSPY_SITES
            ci = INDEED_COUNTRY.get(country, country.strip().lower())
        for term in SEARCH_TERMS:
            try:
                df = scrape_jobs(
                    site_name=sites,
                    search_term=term,
                    google_search_term=f"{term} remote {country}",
                    location=country,
                    is_remote=True,
                    results_wanted=RESULTS_WANTED,
                    hours_old=hours,
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
            for _, row in df.iterrows():
                title = s(row.get("title"))
                desc = s(row.get("description"))
                if (not title or blocked(title)
                        or (not LOOSE and not matches_role(title))
                        or not remote_ok(title, desc, parse_remote_flag(row.get("is_remote")))):
                    continue
                jobs.append({
                    "source": s(row.get("site")),
                    "title": title,
                    "company": s(row.get("company")),
                    "location": s(row.get("location")) or country,
                    "region": classify_region(row.get("location"), title, desc, country),
                    "url": s(row.get("job_url")),
                    "date": s(row.get("date_posted")),
                    "description": desc,
                })
                cnt += 1
            note = " (регион -> LinkedIn+Google, Indeed пропущен)" if is_region else ""
            print(f"  [{country}/'{term}'] подходящих: {cnt}{note}")
    return jobs


def fetch_remoteok(hours: int) -> list:
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
        desc = strip_html(item.get("description", ""))
        if blocked(title) or not matches_role(title) or not remote_ok(title, desc, True):
            continue
        if not within_hours(item.get("date", ""), hours):
            continue
        loc = item.get("location", "") or "Remote"
        jobs.append({
            "source": "RemoteOK",
            "title": title,
            "company": html.unescape(item.get("company", "")),
            "location": loc,
            "region": classify_region(loc, title, desc),
            "url": item.get("url", ""),
            "date": (item.get("date", "") or "")[:10],
            "description": desc,
        })
    print(f"  [RemoteOK] подходящих: {len(jobs)}")
    return jobs


def fetch_wwr(hours: int) -> list:
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
            summary = strip_html(entry.get("summary", ""))
            if blocked(title):
                continue
            if not trust and not matches_role(title):   # категории шире support — фильтр по роли
                continue
            if not remote_ok(title, summary, True):
                continue
            pp = entry.get("published_parsed")
            if pp:
                pub = dt.datetime(*pp[:6], tzinfo=dt.timezone.utc)
                if pub < cutoff:
                    continue
            jobs.append({
                "source": "WeWorkRemotely",
                "title": title,
                "company": title.split(":")[0].strip() if ":" in title else "",
                "location": entry.get("region", "") or "Remote",
                "region": classify_region(entry.get("region", ""), title, summary),
                "url": entry.get("link", ""),
                "date": (entry.get("published", "") or "")[:16],
                "description": summary,
            })
            cnt += 1
        print(f"  [WWR] {feed_url.split('/')[-1]}: {cnt}")
    return jobs


def fetch_remotive(hours: int) -> list:
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
        desc = strip_html(it.get("description", ""))
        if blocked(title) or not matches_role(title) or not remote_ok(title, desc, True):
            continue
        if not within_hours(it.get("publication_date", ""), hours):
            continue
        loc = it.get("candidate_required_location", "") or "Remote"
        jobs.append({
            "source": "Remotive",
            "title": title,
            "company": html.unescape(it.get("company_name", "")),
            "location": loc,
            "region": classify_region(loc, title, desc),
            "url": it.get("url", ""),
            "date": (it.get("publication_date", "") or "")[:10],
            "description": desc,
        })
    print(f"  [Remotive] подходящих: {len(jobs)}")
    return jobs


def _ats_loc_onsite(loc: str) -> bool:
    ll = (loc or "").lower()
    return (any(k in ll for k in ("hybrid", "on-site", "on site", "onsite", "in-office", "in office"))
            and "remote" not in ll)


def _ats_greenhouse(slug: str, hours: int) -> list:
    out = []
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        title = j.get("title", "")
        loc = (j.get("location") or {}).get("name", "")
        desc = strip_html(j.get("content", ""))
        if blocked(title) or not matches_role(title):
            continue
        if not within_hours(j.get("updated_at", ""), hours):
            continue
        if _ats_loc_onsite(loc) or not remote_ok(title, loc + ". " + desc, None):
            continue
        out.append({"source": "Greenhouse", "title": title, "company": slug,
                    "location": loc or "Remote", "region": classify_region(loc, title, desc),
                    "url": j.get("absolute_url", ""), "date": (j.get("updated_at", "") or "")[:10],
                    "description": desc})
    return out


def _ats_lever(slug: str, hours: int) -> list:
    out = []
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    for j in r.json():
        title = j.get("text", "")
        cats = j.get("categories") or {}
        loc = cats.get("location", "") or ""
        desc = j.get("descriptionPlain", "") or strip_html(j.get("description", ""))
        if blocked(title) or not matches_role(title):
            continue
        created = j.get("createdAt")
        if created:
            try:
                if dt.datetime.fromtimestamp(created / 1000, dt.timezone.utc) < cutoff:
                    continue
            except Exception:
                pass
        if _ats_loc_onsite(loc) or not remote_ok(title, loc + ". " + desc, None):
            continue
        out.append({"source": "Lever", "title": title, "company": slug,
                    "location": loc or "Remote", "region": classify_region(loc, title, desc),
                    "url": j.get("hostedUrl", ""), "date": "", "description": desc})
    return out


def _ats_ashby(slug: str, hours: int) -> list:
    out = []
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        title = j.get("title", "")
        loc = j.get("location", "") or ""
        desc = j.get("descriptionPlain", "") or strip_html(j.get("description", ""))
        flag = j.get("isRemote")
        flag = bool(flag) if flag is not None else None
        if blocked(title) or not matches_role(title):
            continue
        if not within_hours(str(j.get("publishedAt", "") or j.get("publishedDate", "")), hours):
            continue
        if _ats_loc_onsite(loc) or not remote_ok(title, loc + ". " + desc, flag):
            continue
        out.append({"source": "Ashby", "title": title, "company": slug,
                    "location": loc or "Remote", "region": classify_region(loc, title, desc),
                    "url": j.get("jobUrl", "") or j.get("applyUrl", ""),
                    "date": str(j.get("publishedAt", "") or "")[:10], "description": desc})
    return out


def fetch_ats(hours: int) -> list:
    handlers = {"greenhouse": _ats_greenhouse, "lever": _ats_lever, "ashby": _ats_ashby}
    jobs = []
    for platform, slug in ATS_COMPANIES:
        h = handlers.get(platform)
        if not h:
            print(f"  [ATS] неизвестная платформа: {platform}")
            continue
        try:
            got = h(slug, hours)
            jobs += got
            print(f"  [{platform}/{slug}] подходящих: {len(got)}")
        except Exception as e:
            print(f"  [{platform}/{slug}] пропущен: {e}")
    return jobs


def dedupe(jobs: list) -> list:
    seen, out = set(), []
    for j in jobs:
        k = job_key(j)
        if k in seen:
            continue
        seen.add(k)
        out.append(j)
    return out


def main():
    global SEARCH_TERMS, COUNTRIES, LOOSE, TARGETED
    import argparse
    p = argparse.ArgumentParser(description="Сбор вакансий. Без флагов — обычный широкий прогон.")
    p.add_argument("hours", nargs="?", type=int, default=None, help="окно в часах (по умолч. неделя)")
    p.add_argument("--term", action="append", help="ключевое слово/роль (можно несколько раз или через запятую)")
    p.add_argument("--location", action="append", help="локация (можно несколько раз или через запятую)")
    p.add_argument("--loose", action="store_true", help="доверять ключевому слову, не резать фильтром ролей")
    a = p.parse_args()

    hours = a.hours if a.hours else HOURS_OLD

    def _split(items):
        out = []
        for it in items or []:
            out += [x.strip() for x in it.split(",") if x.strip()]
        return out

    terms = _split(a.term)
    locs = _split(a.location)
    if terms:
        SEARCH_TERMS = terms
    if locs:
        COUNTRIES = locs
    LOOSE = a.loose
    TARGETED = bool(terms or locs)   # целевой поиск -> только JobSpy по term×location

    if TARGETED:
        print(f"Целевой поиск за {hours} ч | термины: {SEARCH_TERMS} | локации: {COUNTRIES}"
              + (" | свободный фильтр" if LOOSE else ""))
    else:
        print(f"Собираю вакансии за последние {hours} ч "
              f"(LinkedIn/Indeed/Google по странам + RemoteOK + WWR + Remotive + ATS)...")

    collected = collect_jobspy(hours)
    if not TARGETED and USE_REMOTE_BOARDS:
        collected += fetch_remoteok(hours) + fetch_wwr(hours) + fetch_remotive(hours)
    if not TARGETED and USE_ATS:
        collected += fetch_ats(hours)
    all_jobs = dedupe(collected)

    seen = load_seen()
    before = len(all_jobs)
    all_jobs = [j for j in all_jobs if job_key(j) not in seen]
    skipped = before - len(all_jobs)

    order = {"WORLDWIDE": 0, "EUROPE": 1, "UNKNOWN": 2, "US-ONLY": 3}
    all_jobs.sort(key=lambda j: order.get(j["region"], 9))

    fname = f"jobs_{dt.date.today().isoformat()}.csv"
    cols = ["region", "title", "company", "location", "source", "date", "url",
            "description", "status", "tailored_cv", "applied_date", "notes"]
    with open(fname, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for j in all_jobs:
            j.setdefault("status", "NEW")
            j.setdefault("tailored_cv", "")
            j.setdefault("applied_date", "")
            j.setdefault("notes", "")
            w.writerow({c: j.get(c, "") for c in cols})

    append_seen(job_key(j) for j in all_jobs)

    ww = sum(1 for j in all_jobs if j["region"] == "WORLDWIDE")
    eu = sum(1 for j in all_jobs if j["region"] == "EUROPE")
    by_src = {}
    for j in all_jobs:
        by_src[j["source"]] = by_src.get(j["source"], 0) + 1
    print(f"\nГотово. Новых: {len(all_jobs)} | WORLDWIDE: {ww} | EUROPE: {eu}")
    print(f"По источникам: {by_src}")
    print(f"Пропущено как уже виденные: {skipped}")
    print(f"Файл-трекер (с описаниями): {fname}")


if __name__ == "__main__":
    main()
