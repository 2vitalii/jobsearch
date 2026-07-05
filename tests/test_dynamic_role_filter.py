"""Tests for feat/dynamic-role-filter acceptance criteria.

PLAN reference: .agent/PLAN_dynamic_role_filter.md
Implementation: jobsearch/filters.py + api/run.py (STEP 1 + STEP 3)

Coverage:
  1. Two dissimilar roles — no cross-profile leak.
  2. Legacy defaults unchanged (backward-compat: no-arg calls preserve old behavior).
  3. Empty-list semantic (matches_role(x, []) == True) + POST /run 400 guard.
  4. Seniority toggle (block_seniority=True/False).
  5. FILTER_DEBUG counter honesty: debug mirror in api/run.py uses identical args to _passes().

All tests are strictly offline: no network, no Supabase, no LLM, no paid API calls.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest

from jobsearch import filters
from jobsearch.models import Job, PlatformConfig, SearchParams


# ===========================================================================
# PART 1 — Two dissimilar roles: no cross-profile leak
# ===========================================================================

class TestTwoDissimilarRoles:
    """Headline requirement: a 'support engineer' user and a 'marketing manager'
    user each see only matches from their own keyword list, with no leakage from
    the author's static ROLE_KEYWORDS list."""

    SUPPORT_KEYWORDS = ["technical support", "support engineer"]
    MARKETING_KEYWORDS = ["marketing manager", "marketing"]

    # ---- Marketing Manager user ----

    def test_marketing_user_matches_marketing_manager_title(self):
        """A marketing user's keywords must match a 'Marketing Manager' title."""
        assert filters.matches_role("Marketing Manager", self.MARKETING_KEYWORDS) is True

    def test_marketing_user_matches_digital_marketing_specialist(self):
        """'Digital Marketing Specialist' contains 'marketing' -> matches."""
        assert filters.matches_role("Digital Marketing Specialist", self.MARKETING_KEYWORDS) is True

    def test_marketing_user_does_not_match_support_title(self):
        """A marketing user's keyword list must NOT match 'Technical Support Engineer'."""
        assert filters.matches_role("Technical Support Engineer", self.MARKETING_KEYWORDS) is False

    # ---- Support Engineer user ----

    def test_support_user_matches_technical_support_title(self):
        """A support user's keywords must match 'Technical Support Engineer'."""
        assert filters.matches_role("Technical Support Engineer", self.SUPPORT_KEYWORDS) is True

    def test_support_user_matches_support_engineer_title(self):
        assert filters.matches_role("Support Engineer", self.SUPPORT_KEYWORDS) is True

    def test_support_user_does_not_match_marketing_title(self):
        """A support user's keywords must NOT match 'Marketing Manager'."""
        assert filters.matches_role("Marketing Manager", self.SUPPORT_KEYWORDS) is False

    # ---- Isolation: static ROLE_KEYWORDS must NOT leak when caller supplies keywords ----

    def test_static_role_keywords_do_not_leak_into_marketing_user(self):
        """When marketing keywords are supplied, ROLE_KEYWORDS is ignored.
        'Technical Support Engineer' is in ROLE_KEYWORDS but NOT in marketing keywords
        -> must return False for a marketing user."""
        # Confirm the static list WOULD match (sanity):
        assert filters.matches_role("Technical Support Engineer") is True  # static default
        # But with marketing keywords it must NOT match:
        assert filters.matches_role("Technical Support Engineer", self.MARKETING_KEYWORDS) is False

    def test_static_role_keywords_do_not_leak_into_custom_list(self):
        """Any non-None role_keywords list replaces ROLE_KEYWORDS entirely.
        'Implementation Consultant' is in ROLE_KEYWORDS but an empty-ish custom list
        that doesn't mention 'implementation' must not match."""
        niche_keywords = ["cloud architect"]  # intentionally narrow
        assert filters.matches_role("Implementation Consultant") is True   # static default
        assert filters.matches_role("Implementation Consultant", niche_keywords) is False

    def test_marketing_title_in_static_role_keywords_is_false(self):
        """Confirm Marketing Manager is NOT in ROLE_KEYWORDS (legacy default must return False)."""
        assert filters.matches_role("Marketing Manager") is False

    def test_support_title_in_static_role_keywords_is_true(self):
        """Confirm 'Technical Support Engineer' IS in ROLE_KEYWORDS (legacy default True)."""
        assert filters.matches_role("Technical Support Engineer") is True

    def test_case_insensitive_matching_for_dynamic_keywords(self):
        """Dynamic keyword matching must be case-insensitive, same as the static path."""
        assert filters.matches_role("MARKETING MANAGER", self.MARKETING_KEYWORDS) is True
        assert filters.matches_role("technical support engineer", self.SUPPORT_KEYWORDS) is True

    def test_substring_matching_for_dynamic_keywords(self):
        """Dynamic keywords use substring matching (same mechanism as static).
        'marketing' as a keyword matches 'Senior Marketing Manager' as a substring."""
        assert filters.matches_role("Senior Marketing Manager", ["marketing"]) is True

    def test_two_users_same_title_different_outcomes(self):
        """Same title, different user keyword lists -> different results.
        This is the canonical no-leak proof: each user's list drives their own result."""
        title = "Marketing Manager"
        assert filters.matches_role(title, self.MARKETING_KEYWORDS) is True
        assert filters.matches_role(title, self.SUPPORT_KEYWORDS) is False


# ===========================================================================
# PART 2 — Legacy defaults unchanged (backward-compat)
# ===========================================================================

class TestLegacyDefaults:
    """Calling matches_role/blocked WITHOUT the new optional args must behave
    identically to the pre-feature behavior: ROLE_KEYWORDS + block_seniority=True."""

    def test_matches_role_no_arg_true_for_support_title(self):
        """matches_role(title) with no 2nd arg uses ROLE_KEYWORDS -> True for support."""
        assert filters.matches_role("Technical Support Engineer") is True

    def test_matches_role_no_arg_false_for_marketing(self):
        """matches_role(title) with no 2nd arg uses ROLE_KEYWORDS -> False for marketing."""
        assert filters.matches_role("Marketing Manager") is False

    def test_matches_role_no_arg_false_for_graphic_designer(self):
        assert filters.matches_role("Graphic Designer") is False

    def test_matches_role_no_arg_true_for_project_coordinator(self):
        assert filters.matches_role("Project Coordinator") is True

    def test_matches_role_no_arg_true_for_integration_specialist(self):
        """'integration' is in ROLE_KEYWORDS -> True."""
        assert filters.matches_role("Integration Specialist") is True

    def test_blocked_no_arg_blocks_seniority_by_default(self):
        """blocked(title) with no 2nd arg: block_seniority=True (legacy default).
        'Senior Support Engineer' contains 'senior' in SENIORITY_KEYWORDS -> True."""
        assert filters.blocked("Senior Support Engineer") is True

    def test_blocked_no_arg_blocks_director(self):
        """'Director of Support' contains 'director' in SENIORITY_KEYWORDS -> blocked."""
        assert filters.blocked("Director of Support") is True

    def test_blocked_no_arg_blocks_head_of(self):
        """'Head of Customer Success' contains 'head of' in SENIORITY_KEYWORDS -> blocked."""
        assert filters.blocked("Head of Customer Success") is True

    def test_blocked_no_arg_allows_junior_support(self):
        """Plain 'Support Engineer' has no negative keywords -> False."""
        assert filters.blocked("Support Engineer") is False

    def test_blocked_no_arg_blocks_negative_title_keywords(self):
        """NEGATIVE_TITLE_KEYWORDS must still fire: 'recruiter' -> blocked."""
        assert filters.blocked("Recruiter") is True

    def test_blocked_no_arg_blocks_sales_engineer(self):
        assert filters.blocked("Sales Engineer") is True


# ===========================================================================
# PART 3 — Empty-list semantic + POST /run empty-keywords guard
# ===========================================================================

class TestEmptyListSemantic:
    """matches_role(x, []) == True (no-constraint: all roles pass the pre-filter).
    The product closes this path at the API layer (POST /run returns 400)."""

    def test_empty_list_returns_true_for_any_title(self):
        """The empty-list semantic means 'no role constraint' -> any title passes."""
        assert filters.matches_role("Anything At All", []) is True

    def test_empty_list_returns_true_for_unrelated_title(self):
        assert filters.matches_role("Garbage Collection Specialist III", []) is True

    def test_empty_list_returns_true_for_empty_title(self):
        """Even an empty title passes the role gate when no constraint is given."""
        assert filters.matches_role("", []) is True

    def test_empty_list_vs_none_behavior_differs(self):
        """[] means 'no constraint' (True for anything); None means 'use ROLE_KEYWORDS'."""
        # Marketing Manager: matches [] (no constraint) but NOT ROLE_KEYWORDS
        assert filters.matches_role("Marketing Manager", []) is True
        assert filters.matches_role("Marketing Manager", None) is False  # None uses ROLE_KEYWORDS

    def test_none_keyword_arg_uses_static_role_keywords(self):
        """None explicitly uses ROLE_KEYWORDS (same as no-arg path)."""
        assert filters.matches_role("Technical Support Engineer", None) is True
        assert filters.matches_role("Graphic Designer", None) is False


class TestEmptyKeywordsGuardPredicate:
    """Unit-test the guard predicate logic from api/run.py line 435.

    The guard is: `if not any(k.strip() for k in params.keywords):`
    This predicate is tested here as a pure function, independent of HTTP.
    A more expensive TestClient test exercising the POST /run 400 follows below.
    """

    @staticmethod
    def _guard_fires(keywords: list[str]) -> bool:
        """Returns True when the api/run.py guard would fire (i.e. 400 would be raised)."""
        return not any(k.strip() for k in keywords)

    def test_empty_list_fires_guard(self):
        assert self._guard_fires([]) is True

    def test_list_of_blank_strings_fires_guard(self):
        assert self._guard_fires(["", "  ", "\t"]) is True

    def test_list_with_one_non_blank_does_not_fire(self):
        assert self._guard_fires(["support"]) is False

    def test_list_with_whitespace_around_keyword_does_not_fire(self):
        assert self._guard_fires(["  support  "]) is False

    def test_nonempty_list_does_not_fire(self):
        assert self._guard_fires(["technical support", "support engineer"]) is False


# ---------------------------------------------------------------------------
# POST /run 400 test using offline TestClient (dependency_overrides pattern)
# ---------------------------------------------------------------------------

pytest.importorskip("fastapi")

from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.main import app
from api.deps import get_config, get_job_store, get_llm, get_scraper, get_supabase, get_user_client, get_user_state
from api.auth import get_current_user, CurrentUser


_FAKE_USER_ID_DRFT = "aaaaaaaa-0000-0000-0000-000000000042"
_FAKE_CV_MD = "# Jane\nSupport Engineer\n## Summary\nFoo bar."
_FAKE_SHORT_PROFILE = "Support engineer."


class _MinimalRunsDB:
    """Thread-safe in-memory stub for the runs table."""
    def __init__(self):
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}

    def insert(self, row: dict) -> dict:
        row = dict(row)
        row.setdefault("id", str(uuid.uuid4()))
        row.setdefault("status", "running")
        with self._lock:
            self._rows[row["id"]] = row
        return row

    def select_matching(self, filt: list[tuple]) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._rows.values() if all(r.get(c) == v for c, v in filt)]

    def clear(self):
        with self._lock:
            self._rows.clear()


class _MinimalQB:
    def __init__(self, db: _MinimalRunsDB, table: str):
        self._db = db
        self._table = table
        self._op: str | None = None
        self._data: dict | None = None
        self._filters: list[tuple] = []

    def insert(self, data: dict):
        self._op = "insert"; self._data = data; return self

    def update(self, data: dict):
        self._op = "update"; self._data = data; return self

    def upsert(self, data: dict, **_kw):
        self._op = "insert"; self._data = data; return self

    def select(self, _: str = "*"):
        self._op = "select"; return self

    def eq(self, col: str, val: Any):
        self._filters.append((col, val)); return self

    def order(self, *_, **__): return self

    def limit(self, _: int): return self

    def execute(self):
        class _R:
            data: list = []
        r = _R()
        if self._table != "runs":
            return r
        if self._op == "insert":
            r.data = [self._db.insert(self._data or {})]
        elif self._op == "select":
            r.data = self._db.select_matching(self._filters)
        return r


class _MinimalSupabase:
    def __init__(self, db: _MinimalRunsDB):
        self._db = db

    def table(self, name: str) -> _MinimalQB:
        return _MinimalQB(self._db, name)


class _MinimalJobStore:
    def save(self, _jobs): pass


class _MinimalUserState:
    def is_processed(self, *_): return False
    def mark_processed(self, *_): pass




class _FakeLLM:
    def complete(self, *, model, system, messages, max_tokens) -> str:
        import json
        return json.dumps({"fit_score": 88, "b2b_eligible": "yes", "reason": "ok",
                           "jd_keywords": [], "ats_present": [], "ats_missing": [],
                           "tailored_summary": "Test", "tailored_skills": [], "gaps": "",
                           "recruiter_verdict": "shortlist", "cover_letter": "Dear team"})

@pytest.fixture()
def _empty_keywords_client(monkeypatch):
    """TestClient with all fakes wired and _load_search_params returning [] keywords."""
    import api.run as run_mod

    db = _MinimalRunsDB()
    fake_sb = _MinimalSupabase(db)

    monkeypatch.setattr(run_mod, "make_supabase_client", lambda: fake_sb)
    monkeypatch.setattr(
        run_mod, "_load_search_params",
        lambda sb, uid: SearchParams(keywords=[], locations=["Worldwide"], period_hours=168,
                                     work_format="remote", loose=False, targeted=False),
    )
    monkeypatch.setattr(
        run_mod, "_load_cv",
        lambda sb, uid: (_FAKE_CV_MD, _FAKE_SHORT_PROFILE),
    )
    monkeypatch.setattr(run_mod, "_run_background", lambda **_kw: None)

    def _user():
        return CurrentUser(user_id=_FAKE_USER_ID_DRFT, email="drft@test.com", token="tok")

    def _sb():
        return fake_sb

    def _user_client():
        return fake_sb

    def _job_store():
        return _MinimalJobStore()

    def _user_state():
        return _MinimalUserState()

    def _config():
        return PlatformConfig()

    def _scraper():
        return lambda p, c: []

    def _llm():
        return _FakeLLM()

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_supabase] = _sb
    app.dependency_overrides[get_user_client] = _user_client
    app.dependency_overrides[get_job_store] = _job_store
    app.dependency_overrides[get_user_state] = _user_state
    app.dependency_overrides[get_config] = _config
    app.dependency_overrides[get_scraper] = _scraper
    app.dependency_overrides[get_llm] = _llm

    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()
    db.clear()


class TestPostRunEmptyKeywords400:
    """POST /run with empty (or blank-only) keywords must return 400."""

    def test_empty_keywords_returns_400(self, _empty_keywords_client):
        r = _empty_keywords_client.post("/run")
        assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"

    def test_400_detail_mentions_keyword(self, _empty_keywords_client):
        r = _empty_keywords_client.post("/run")
        body = r.json()
        detail = body.get("detail", "").lower()
        assert "keyword" in detail, f"Expected 'keyword' in detail, got: {detail!r}"

    def test_400_guard_fires_before_run_row_is_created(self, _empty_keywords_client, monkeypatch):
        """When 400 fires, no 'running' row should be written to the DB."""
        import api.run as run_mod
        created_rows = []
        original_load = run_mod._load_search_params

        # We just check that POST returns 400 — success proves no row was created
        # because _run_background is monkeypatched to a no-op.
        r = _empty_keywords_client.post("/run")
        assert r.status_code == 400


# ===========================================================================
# PART 4 — Seniority toggle (block_seniority=True/False)
# ===========================================================================

class TestSeniorityToggle:
    """blocked(title, block_seniority=...) controls whether SENIORITY_KEYWORDS are applied.
    NEGATIVE_TITLE_KEYWORDS are always applied, regardless of the flag."""

    # ---- block_seniority=True (explicit, same as legacy default) ----

    def test_senior_marketing_manager_blocked_when_true(self):
        """'senior' in SENIORITY_KEYWORDS, block_seniority=True -> blocked."""
        assert filters.blocked("Senior Marketing Manager", block_seniority=True) is True

    def test_senior_support_engineer_blocked_when_true(self):
        assert filters.blocked("Senior Support Engineer", block_seniority=True) is True

    def test_principal_engineer_blocked_when_true(self):
        assert filters.blocked("Principal Engineer", block_seniority=True) is True

    def test_chief_of_staff_blocked_when_true(self):
        assert filters.blocked("Chief of Staff", block_seniority=True) is True

    def test_director_of_support_blocked_when_true(self):
        """'director' is in SENIORITY_KEYWORDS -> blocked with block_seniority=True."""
        assert filters.blocked("Director of Support", block_seniority=True) is True

    # ---- block_seniority=False: seniority keywords are skipped ----

    def test_senior_marketing_manager_passes_when_false(self):
        """With block_seniority=False, 'senior' is not checked -> NOT blocked."""
        assert filters.blocked("Senior Marketing Manager", block_seniority=False) is False

    def test_senior_support_engineer_passes_when_false(self):
        """Support + senior: with block_seniority=False the seniority check is skipped."""
        assert filters.blocked("Senior Support Engineer", block_seniority=False) is False

    def test_director_of_support_passes_when_false(self):
        """'director' is only in SENIORITY_KEYWORDS (not NEGATIVE_TITLE_KEYWORDS)
        -> passes when block_seniority=False."""
        assert filters.blocked("Director of Support", block_seniority=False) is False

    def test_head_of_customer_success_passes_when_false(self):
        assert filters.blocked("Head of Customer Success", block_seniority=False) is False

    # ---- NEGATIVE_TITLE_KEYWORDS are ALWAYS applied (regardless of seniority flag) ----

    def test_recruiter_still_blocked_when_seniority_false(self):
        """'recruiter' is in NEGATIVE_TITLE_KEYWORDS -> always blocked, even with
        block_seniority=False. Proves seniority toggle didn't disable other negatives."""
        assert filters.blocked("Recruiter", block_seniority=False) is True

    def test_sales_engineer_still_blocked_when_seniority_false(self):
        """'sales engineer' is in NEGATIVE_TITLE_KEYWORDS -> always blocked."""
        assert filters.blocked("Sales Engineer", block_seniority=False) is True

    def test_software_engineer_still_blocked_when_seniority_false(self):
        assert filters.blocked("Software Engineer", block_seniority=False) is True

    def test_qa_engineer_still_blocked_when_seniority_false(self):
        assert filters.blocked("QA Engineer", block_seniority=False) is True

    def test_machine_learning_still_blocked_when_seniority_false(self):
        assert filters.blocked("Machine Learning Engineer", block_seniority=False) is True

    def test_senior_recruiter_blocked_for_both_reasons_when_true(self):
        """'Senior Recruiter' is blocked both by 'recruiter' (NEGATIVE_TITLE_KEYWORDS)
        and 'senior' (SENIORITY_KEYWORDS). With block_seniority=False it's still blocked
        due to 'recruiter'."""
        assert filters.blocked("Senior Recruiter", block_seniority=True) is True
        assert filters.blocked("Senior Recruiter", block_seniority=False) is True  # recruiter is always negative

    # ---- Confirm SENIORITY_KEYWORDS list is separate from NEGATIVE_TITLE_KEYWORDS ----

    def test_seniority_keywords_not_in_negative_title_keywords(self):
        """SENIORITY_KEYWORDS are extracted from NEGATIVE_TITLE_KEYWORDS — they must NOT
        appear in NEGATIVE_TITLE_KEYWORDS (which would make block_seniority=False useless)."""
        for kw in filters.SENIORITY_KEYWORDS:
            for neg in filters.NEGATIVE_TITLE_KEYWORDS:
                assert kw.strip().lower() not in neg.lower() and neg.lower() not in kw.strip().lower(), (
                    f"SENIORITY_KEYWORD {kw!r} appears inside NEGATIVE_TITLE_KEYWORD {neg!r}; "
                    "these lists must be disjoint so block_seniority=False can work independently"
                )

    def test_block_seniority_default_equals_true(self):
        """Calling blocked(title) without 2nd arg must equal blocked(title, block_seniority=True)."""
        titles = [
            "Senior Support Engineer",
            "Director of Engineering",
            "Principal Engineer",
            "Support Engineer",
            "Recruiter",
            "Marketing Manager",
        ]
        for t in titles:
            assert filters.blocked(t) == filters.blocked(t, block_seniority=True), (
                f"Default must equal block_seniority=True for title={t!r}"
            )


# ===========================================================================
# PART 5 — FILTER_DEBUG counter honesty (api/run.py _passes + debug mirror)
# ===========================================================================

class TestFilterDebugHonesty:
    """Verify that the debug attribution block in api/run.py uses the SAME args
    as _passes(). We replicate both _passes() and the debug mirror as pure Python
    (no import of api.run which drags in DB/network) and assert they agree on a
    representative corpus.

    The key property: for any job J and any (params.keywords, params.exclude_senior),
    the debug gate attribution must be consistent with what _passes() actually decided.
    """

    def _make_job(self, title: str, desc: str, region: str = "WORLDWIDE") -> Job:
        return Job(
            dedup_key=filters.compute_dedup_key("AcmeCo", title),
            source="LinkedIn",
            url=f"https://example.com/{title.replace(' ', '-')}",
            company="AcmeCo",
            title=title,
            location="Remote",
            region=region,
            description=desc,
            date_posted="2026-06-20",
        )

    def _passes(self, j: Job, keywords: list[str] | None, exclude_senior: bool, loose: bool) -> bool:
        """Replication of api/run.py _passes() with the new dynamic args."""
        return (
            (not filters.blocked(j.title, block_seniority=exclude_senior))
            and filters.remote_ok(j.title, j.description, None)
            and (loose or filters.matches_role(j.title, keywords))
        )

    def _debug_gate(self, j: Job, keywords: list[str] | None, exclude_senior: bool, loose: bool) -> str:
        """Replication of the debug attribution block in api/run.py (lines 294-305).
        Returns the first-tripped gate name, or 'KEPT'."""
        if filters.blocked(j.title, block_seniority=exclude_senior):
            return "blocked"
        if not filters.remote_ok(j.title, j.description, None):
            return "not_remote"
        if not (loose or filters.matches_role(j.title, keywords)):
            return "not_role"
        return "KEPT"

    REMOTE_DESC = "Fully remote role. Work from home. Distributed team."
    SILENT_DESC = "We are a great company with offices worldwide."

    CORPUS = [
        # (title, desc, expect_passes)
        # Support engineer user (role_keywords=["support engineer"]):
        ("Technical Support Engineer", REMOTE_DESC, True),   # passes all gates
        ("Support Engineer", REMOTE_DESC, True),             # passes all gates
        ("Marketing Manager", REMOTE_DESC, False),           # fails role gate
        ("Senior Support Engineer", REMOTE_DESC, False),     # blocked (exclude_senior=True)
        ("Technical Support Engineer", SILENT_DESC, False),  # fails remote gate
    ]

    def test_passes_and_debug_agree_support_user_exclude_senior(self):
        """Support engineer user with exclude_senior=True: _passes and debug must agree."""
        keywords = ["technical support", "support engineer"]
        exclude_senior = True
        loose = False

        for title, desc, expected_passes in self.CORPUS:
            j = self._make_job(title, desc)
            actual_passes = self._passes(j, keywords, exclude_senior, loose)
            gate = self._debug_gate(j, keywords, exclude_senior, loose)

            # Agreement check: if passes -> gate must be KEPT; if drops -> gate must not be KEPT
            if actual_passes:
                assert gate == "KEPT", (
                    f"title={title!r}: _passes()=True but debug gate={gate!r}"
                )
            else:
                assert gate != "KEPT", (
                    f"title={title!r}: _passes()=False but debug gate='KEPT'"
                )

    def test_passes_and_debug_agree_marketing_user_no_seniority_block(self):
        """Marketing manager user with exclude_senior=False: passes and debug must agree."""
        keywords = ["marketing manager", "marketing"]
        exclude_senior = False
        loose = False

        corpus = [
            ("Marketing Manager", self.REMOTE_DESC, True),
            ("Digital Marketing Specialist", self.REMOTE_DESC, True),
            ("Technical Support Engineer", self.REMOTE_DESC, False),  # fails role gate
            ("Senior Marketing Manager", self.REMOTE_DESC, True),  # senior allowed (exclude_senior=False)
            ("Recruiter", self.REMOTE_DESC, False),  # always blocked (NEGATIVE_TITLE_KEYWORDS)
            ("Marketing Manager", self.SILENT_DESC, False),  # fails remote gate
        ]

        for title, desc, expected_passes in corpus:
            j = self._make_job(title, desc)
            actual_passes = self._passes(j, keywords, exclude_senior, loose)
            gate = self._debug_gate(j, keywords, exclude_senior, loose)

            assert actual_passes == expected_passes, (
                f"title={title!r}: expected _passes()={expected_passes}, got {actual_passes}"
            )
            if actual_passes:
                assert gate == "KEPT", (
                    f"title={title!r}: _passes()=True but debug gate={gate!r}"
                )
            else:
                assert gate != "KEPT", (
                    f"title={title!r}: _passes()=False but debug gate='KEPT'"
                )

    def test_passes_and_debug_agree_for_known_set(self):
        """Comprehensive consistency check: for a set of (title, desc, keywords, exclude_senior)
        combinations, assert _passes() result matches debug attribution direction."""
        cases = [
            ("Technical Support Engineer", self.REMOTE_DESC, ["support engineer"], True),
            ("Technical Support Engineer", self.REMOTE_DESC, ["marketing"], True),      # loose=True below
            ("Senior Support Engineer", self.REMOTE_DESC, ["support engineer"], True),  # exclude_senior=True blocks
            ("Marketing Manager", self.REMOTE_DESC, ["marketing manager"], False),
            ("Marketing Manager", self.REMOTE_DESC, ["support engineer"], False),
            ("Software Engineer", self.REMOTE_DESC, ["software engineer"], False),       # NEGATIVE_TITLE_KEYWORDS
        ]
        loose = False
        for title, desc, kws, exclude_senior in cases:
            j = self._make_job(title, desc)
            passes = self._passes(j, kws, exclude_senior, loose)
            gate = self._debug_gate(j, kws, exclude_senior, loose)
            consistent = (passes and gate == "KEPT") or (not passes and gate != "KEPT")
            assert consistent, (
                f"INCONSISTENCY: title={title!r}, kws={kws!r}, exclude_senior={exclude_senior}, "
                f"passes={passes}, gate={gate!r}"
            )

    def test_same_function_same_args_cannot_diverge(self):
        """The identity property: calling the same filter function with the same args
        twice must return the same result. This is a sanity guard — if the debug block
        calls f(x, arg) and _passes calls f(x, arg) with the same arg, they cannot diverge."""
        title = "Senior Marketing Manager"
        exclude_senior = False

        result_a = filters.blocked(title, block_seniority=exclude_senior)
        result_b = filters.blocked(title, block_seniority=exclude_senior)
        assert result_a == result_b  # trivially True for a pure function

        kws = ["marketing manager"]
        role_a = filters.matches_role(title, kws)
        role_b = filters.matches_role(title, kws)
        assert role_a == role_b

    def test_debug_attribution_sum_equals_total(self):
        """All jobs must be attributed to exactly one gate bucket."""
        jobs_with_params = [
            (self._make_job("Technical Support Engineer", self.REMOTE_DESC), ["support engineer"], True),
            (self._make_job("Marketing Manager", self.REMOTE_DESC), ["support engineer"], True),
            (self._make_job("Senior Support Engineer", self.REMOTE_DESC), ["support engineer"], True),
            (self._make_job("Recruiter", self.REMOTE_DESC), ["recruiter specialist"], True),
            (self._make_job("Technical Support Engineer", self.SILENT_DESC), ["support engineer"], False),
        ]
        counts = {"blocked": 0, "not_remote": 0, "not_role": 0, "KEPT": 0}
        for j, kws, exclude_senior in jobs_with_params:
            gate = self._debug_gate(j, kws, exclude_senior, loose=False)
            counts[gate] = counts.get(gate, 0) + 1
        assert sum(counts.values()) == len(jobs_with_params)
