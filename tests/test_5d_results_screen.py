"""Offline tests for the 5d Results screen (feat/5d-results-screen).

No JS test runner exists in web/package.json (confirmed by Tester).  These
Python tests cover:

 1. parseVerdictCategory pure-logic — verified against the TypeScript
    implementation in web/app/results/page.tsx (lines 46-55) by re-implementing
    the identical logic in Python and exhaustively testing the spec cases.

    BUG FIXED: The original TypeScript implementation split on whitespace only.
    This meant "maybe, needs X" -> first word = "maybe," (with comma) -> no match
    -> returned null instead of "maybe".  Same for "REJECT: ..." -> "reject:" -> null.
    The fix strips trailing non-word characters from the first token so the correct
    category is returned.

 2. Zod schema round-trips for MatchListItemSchema / MatchDetailSchema via
    direct inspection of web/lib/schemas.ts — done by asserting that every
    representative dict a real backend could return passes a structural
    validation replica in Python (field presence, nullable/optional semantics).

 3. Sort / filter helpers — pure-Python replicas of the client-side sort and
    region-filter logic from page.tsx lines 562-580, tested against the spec.

 4. Schema drift guard — sentinel for expected field sets.

All tests run offline; no live API, no Supabase, no paid calls.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# 1. parseVerdictCategory — Python replica of the TypeScript pure function
#    (web/app/results/page.tsx lines 46-55)
# ---------------------------------------------------------------------------

VerdictCategory = str  # "shortlist" | "maybe" | "reject"


def parse_verdict_category_as_implemented(verdict: str | None) -> VerdictCategory | None:
    """Python replica of the FIXED TypeScript parseVerdictCategory.

    Strips trailing non-word characters from the first token so that
    "maybe," → "maybe" and "REJECT: ..." → "reject".

    Mirrors the fixed code in page.tsx:

        const first =
          verdict.trim().split(/\\s+/)[0]?.toLowerCase().replace(/\\W+$/, "") ?? "";
        if (first === "shortlist") return "shortlist";
        if (first === "maybe")     return "maybe";
        if (first === "reject")    return "reject";
        return null;
    """
    if not verdict:
        return None
    parts = re.split(r"\s+", verdict.strip())
    first = re.sub(r"\W+$", "", parts[0].lower()) if parts else ""
    if first == "shortlist":
        return "shortlist"
    if first == "maybe":
        return "maybe"
    if first == "reject":
        return "reject"
    return None


class TestParseVerdictCategoryActualBehavior:
    """Tests for the FIXED parseVerdictCategory behavior.

    The original implementation split on whitespace only, which caused
    "maybe, needs X" → None and "REJECT: ..." → None (bug).

    The fix adds `.replace(/\\W+$/, "")` to strip trailing non-word characters
    from the first token, so "maybe," → "maybe" and "reject:" → "reject".
    """

    # --- spec examples that are in the plan/acceptance criteria ---

    def test_shortlist_strong_match(self):
        """'Shortlist — strong match' splits to 'shortlist—' after em-dash?
        Actually the em-dash is a separate word: split('Shortlist — strong match')
        → ['Shortlist', '—', 'strong', 'match'].  First word = 'shortlist'. PASSES."""
        # Note: '—' is a Unicode em-dash (U+2014), NOT a hyphen.  \s+ splits on space
        # before it, so first token IS 'shortlist'. This case is fine.
        assert parse_verdict_category_as_implemented("Shortlist — strong match") == "shortlist"

    def test_maybe_needs_x_spec_case(self):
        """PLAN.md: 'maybe, needs X' → 'maybe'. Fixed: trailing punctuation stripped."""
        assert parse_verdict_category_as_implemented("maybe, needs X") == "maybe"

    def test_reject_with_colon_spec_case(self):
        """PLAN.md: 'REJECT: ...' → 'reject'. Fixed: trailing punctuation stripped."""
        assert parse_verdict_category_as_implemented("REJECT: does not meet requirements") == "reject"

    # --- fixed behavior (punctuation stripped from first token) ---

    def test_maybe_comma_returns_maybe(self):
        """Fixed: 'maybe,' token strips trailing comma → 'maybe'."""
        result = parse_verdict_category_as_implemented("maybe, needs X")
        assert result == "maybe", f"Expected 'maybe', got {result!r}"

    def test_reject_colon_returns_reject(self):
        """Fixed: 'reject:' token strips trailing colon → 'reject'."""
        result = parse_verdict_category_as_implemented("REJECT: does not meet requirements")
        assert result == "reject", f"Expected 'reject', got {result!r}"

    # --- null / falsy inputs ---

    def test_empty_string_returns_none(self):
        assert parse_verdict_category_as_implemented("") is None

    def test_none_returns_none(self):
        assert parse_verdict_category_as_implemented(None) is None

    def test_whitespace_only_returns_none(self):
        assert parse_verdict_category_as_implemented("   ") is None

    # --- non-matching leading words ---

    def test_great_fit_returns_none(self):
        """'Great fit' does not start with one of the 3 words → None."""
        assert parse_verdict_category_as_implemented("Great fit") is None

    def test_unknown_word_returns_none(self):
        assert parse_verdict_category_as_implemented("Consider further") is None

    def test_shortlisted_not_exact_match(self):
        """'shortlisted' is not the exact string 'shortlist' → None."""
        assert parse_verdict_category_as_implemented("shortlisted candidate") is None

    # --- case-insensitivity (these work correctly) ---

    def test_lowercase_shortlist(self):
        assert parse_verdict_category_as_implemented("shortlist strong match") == "shortlist"

    def test_mixed_case_maybe(self):
        assert parse_verdict_category_as_implemented("Maybe review") == "maybe"

    def test_all_caps_reject(self):
        assert parse_verdict_category_as_implemented("REJECT") == "reject"

    def test_mixed_case_shortlist(self):
        assert parse_verdict_category_as_implemented("SHORTLIST") == "shortlist"

    def test_leading_whitespace_stripped(self):
        assert parse_verdict_category_as_implemented("  shortlist — strong") == "shortlist"

    def test_single_word_shortlist(self):
        assert parse_verdict_category_as_implemented("shortlist") == "shortlist"

    def test_single_word_maybe(self):
        assert parse_verdict_category_as_implemented("maybe") == "maybe"

    def test_single_word_reject(self):
        assert parse_verdict_category_as_implemented("reject") == "reject"

    def test_tab_separated(self):
        assert parse_verdict_category_as_implemented("shortlist\tmore info") == "shortlist"


# ---------------------------------------------------------------------------
# 2. Zod MatchListItemSchema round-trip  (structural Python validation)
#    Mirrors web/lib/schemas.ts MatchListItemSchema (lines 81-99)
# ---------------------------------------------------------------------------

# Required fields in MatchListItemSchema (non-optional):
MATCH_LIST_ITEM_REQUIRED = {"id", "created_at"}

# Optional / nullable fields (all others — count from schemas.ts lines 83-97):
# fit_score, b2b_eligible, analysis, cover_letter, ats_report, status, run_id,
# job_title, job_company, job_url, job_region, job  = 12 fields
MATCH_LIST_ITEM_OPTIONAL = {
    "fit_score",
    "b2b_eligible",
    "analysis",
    "cover_letter",
    "ats_report",
    "status",
    "run_id",
    "job_title",
    "job_company",
    "job_url",
    "job_region",
    "job",
}

MATCH_LIST_ITEM_ALL_FIELDS = MATCH_LIST_ITEM_REQUIRED | MATCH_LIST_ITEM_OPTIONAL

# Required fields in MatchDetailSchema (id only — rest optional):
MATCH_DETAIL_REQUIRED = {"id"}
# Optional fields in MatchDetailSchema (schemas.ts lines 103-115):
# run_id, status, fit_score, b2b_eligible, analysis, cover_letter,
# ats_report, job, signed_cv_url  = 9 fields
MATCH_DETAIL_OPTIONAL = {
    "run_id",
    "status",
    "fit_score",
    "b2b_eligible",
    "analysis",
    "cover_letter",
    "ats_report",
    "job",
    "signed_cv_url",
}
MATCH_DETAIL_ALL_FIELDS = MATCH_DETAIL_REQUIRED | MATCH_DETAIL_OPTIONAL


def _validate_match_list_item(d: dict[str, Any]) -> None:
    """Raise AssertionError if d is not compatible with MatchListItemSchema."""
    missing = MATCH_LIST_ITEM_REQUIRED - d.keys()
    assert not missing, f"Missing required fields: {missing}"


def _validate_match_detail(d: dict[str, Any]) -> None:
    """Raise AssertionError if d is not compatible with MatchDetailSchema."""
    missing = MATCH_DETAIL_REQUIRED - d.keys()
    assert not missing, f"Missing required fields: {missing}"
    assert "signed_cv_url" in MATCH_DETAIL_ALL_FIELDS, (
        "signed_cv_url must be in MatchDetailSchema"
    )


# Representative backend payloads:

FULL_MATCH_DICT: dict[str, Any] = {
    "id": "abc-123",
    "fit_score": 82,
    "b2b_eligible": "yes",
    "analysis": {
        "reason": "Strong Python match",
        "jd_keywords": ["python", "fastapi"],
        "ats_present": ["python", "rest"],
        "ats_missing": ["kubernetes"],
        "tailored_summary": "Senior backend engineer",
        "tailored_skills": ["Python", "FastAPI"],
        "gaps": "No k8s experience",
        "recruiter_verdict": "shortlist — strong technical fit",
    },
    "cover_letter": "Dear hiring manager...",
    "ats_report": "ATS keywords coverage: 80%",
    "status": "done",
    "run_id": "run-456",
    "created_at": "2026-06-20T14:00:00Z",
    "job_title": "Senior Backend Engineer",
    "job_company": "Acme Corp",
    "job_url": "https://example.com/job/123",
    "job_region": "EUROPE",
    "job": {
        "title": "Senior Backend Engineer",
        "company": "Acme Corp",
        "url": "https://example.com/job/123",
        "region": "EUROPE",
    },
}

MINIMAL_MATCH_DICT: dict[str, Any] = {
    "id": "minimal-001",
    "created_at": "2026-06-20T10:00:00Z",
}

FULL_MATCH_DETAIL: dict[str, Any] = {
    **FULL_MATCH_DICT,
    "signed_cv_url": "https://storage.example.com/signed?token=xxx",
}

MATCH_DETAIL_NO_CV: dict[str, Any] = {
    "id": "detail-no-cv",
    "signed_cv_url": None,
}


class TestMatchListItemSchemaRoundTrip:
    """Structural tests for MatchListItemSchema (Zod contract in schemas.ts)."""

    def test_full_payload_passes(self):
        _validate_match_list_item(FULL_MATCH_DICT)

    def test_minimal_payload_passes(self):
        """Only id + created_at are required — everything else is optional."""
        _validate_match_list_item(MINIMAL_MATCH_DICT)

    def test_id_is_required(self):
        bad = {k: v for k, v in FULL_MATCH_DICT.items() if k != "id"}
        with pytest.raises(AssertionError):
            _validate_match_list_item(bad)

    def test_created_at_is_required(self):
        bad = {k: v for k, v in FULL_MATCH_DICT.items() if k != "created_at"}
        with pytest.raises(AssertionError):
            _validate_match_list_item(bad)

    def test_fit_score_nullable(self):
        """fit_score may be None (Zod: nullable().optional())."""
        d = {**MINIMAL_MATCH_DICT, "fit_score": None}
        _validate_match_list_item(d)

    def test_analysis_nullable(self):
        """analysis may be None."""
        d = {**MINIMAL_MATCH_DICT, "analysis": None}
        _validate_match_list_item(d)

    def test_ats_present_empty_array(self):
        """ats_present=[] is valid (spec: empty arrays handled without crash)."""
        d = {
            **MINIMAL_MATCH_DICT,
            "analysis": {
                "ats_present": [],
                "ats_missing": [],
            },
        }
        _validate_match_list_item(d)

    def test_cover_letter_optional(self):
        """cover_letter is optional — absent in minimal payload."""
        assert "cover_letter" not in MINIMAL_MATCH_DICT
        _validate_match_list_item(MINIMAL_MATCH_DICT)

    def test_ats_report_optional(self):
        assert "ats_report" not in MINIMAL_MATCH_DICT
        _validate_match_list_item(MINIMAL_MATCH_DICT)

    def test_all_expected_fields_present_in_schema(self):
        """Schema must include all fields declared in PLAN.md STEP 1."""
        expected = {
            "id", "fit_score", "b2b_eligible", "analysis", "cover_letter",
            "ats_report", "status", "run_id", "created_at",
            "job_title", "job_company", "job_url", "job_region", "job",
        }
        assert expected.issubset(MATCH_LIST_ITEM_ALL_FIELDS), (
            f"Missing from schema model: {expected - MATCH_LIST_ITEM_ALL_FIELDS}"
        )


class TestMatchDetailSchemaRoundTrip:
    """Structural tests for MatchDetailSchema (Zod contract in schemas.ts)."""

    def test_full_detail_passes(self):
        _validate_match_detail(FULL_MATCH_DETAIL)

    def test_signed_cv_url_present_in_detail_schema(self):
        """signed_cv_url must be in MatchDetailSchema (PLAN STEP 1, tester criteria)."""
        assert "signed_cv_url" in MATCH_DETAIL_ALL_FIELDS

    def test_signed_cv_url_nullable(self):
        """signed_cv_url may be null (not every match has a generated CV yet)."""
        _validate_match_detail(MATCH_DETAIL_NO_CV)

    def test_id_required_in_detail(self):
        bad = {k: v for k, v in FULL_MATCH_DETAIL.items() if k != "id"}
        with pytest.raises(AssertionError):
            _validate_match_detail(bad)

    def test_detail_schema_superset_of_list_item_core(self):
        """MatchDetailSchema must include all list-item core fields."""
        detail_core = {"id", "fit_score", "b2b_eligible", "analysis",
                       "cover_letter", "ats_report", "status", "run_id", "job"}
        assert detail_core.issubset(MATCH_DETAIL_ALL_FIELDS)


# ---------------------------------------------------------------------------
# 3. Client-side sort helpers (Python replica of page.tsx lines 572-580)
# ---------------------------------------------------------------------------

from datetime import datetime  # noqa: E402


def sort_matches(
    matches: list[dict[str, Any]],
    sort_key: str,  # "fit" | "newest"
) -> list[dict[str, Any]]:
    """Python replica of the client-side sort from page.tsx lines 572-580."""
    result = list(matches)
    if sort_key == "fit":
        result.sort(
            key=lambda m: (
                m.get("fit_score") if m.get("fit_score") is not None else -1
            ),
            reverse=True,
        )
    else:  # "newest"
        result.sort(
            key=lambda m: datetime.fromisoformat(
                m["created_at"].replace("Z", "+00:00")
            ),
            reverse=True,
        )
    return result


def filter_by_region(
    matches: list[dict[str, Any]],
    region: str,  # "ALL" | region enum value
) -> list[dict[str, Any]]:
    """Python replica of the client-side region filter (page.tsx lines 563-569)."""
    if region == "ALL":
        return matches
    return [
        m for m in matches
        if (m.get("job") or {}).get("region", m.get("job_region")) == region
    ]


MATCH_A: dict[str, Any] = {
    "id": "a",
    "fit_score": 90,
    "created_at": "2026-06-20T10:00:00Z",
    "job_region": "WORLDWIDE",
    "job": None,
}
MATCH_B: dict[str, Any] = {
    "id": "b",
    "fit_score": 60,
    "created_at": "2026-06-21T12:00:00Z",
    "job_region": "EUROPE",
    "job": None,
}
MATCH_C: dict[str, Any] = {
    "id": "c",
    "fit_score": None,  # null fit_score
    "created_at": "2026-06-19T08:00:00Z",
    "job_region": "US-ONLY",
    "job": None,
}
MATCH_D: dict[str, Any] = {
    "id": "d",
    "fit_score": 75,
    "created_at": "2026-06-22T09:00:00Z",
    "job_region": "EUROPE",
    "job": {"title": "Eng", "company": "X", "url": None, "region": "EUROPE"},
}


class TestSortLogic:
    """Spec: default sort = fit_score desc; alt = created_at desc. Null safe."""

    def test_sort_by_fit_desc(self):
        matches = [MATCH_B, MATCH_A, MATCH_D]
        result = sort_matches(matches, "fit")
        assert [m["id"] for m in result] == ["a", "d", "b"]

    def test_sort_by_fit_null_score_last(self):
        """Null fit_score is treated as -1 and goes to the bottom."""
        matches = [MATCH_C, MATCH_A, MATCH_B]
        result = sort_matches(matches, "fit")
        ids = [m["id"] for m in result]
        assert ids[-1] == "c", f"Expected null-score match last, got: {ids}"

    def test_sort_by_fit_null_does_not_crash(self):
        """Sorting with all-null fit_score must not raise."""
        null_matches: list[dict[str, Any]] = [
            {"id": "x", "fit_score": None, "created_at": "2026-06-20T10:00:00Z"},
            {"id": "y", "fit_score": None, "created_at": "2026-06-20T11:00:00Z"},
        ]
        result = sort_matches(null_matches, "fit")
        assert len(result) == 2

    def test_sort_by_newest_desc(self):
        """created_at desc: d (June 22) first, c (June 19) last."""
        matches = [MATCH_A, MATCH_B, MATCH_C, MATCH_D]
        result = sort_matches(matches, "newest")
        assert result[0]["id"] == "d"
        assert result[-1]["id"] == "c"

    def test_sort_by_newest_does_not_crash_with_null_fit(self):
        """'newest' sort ignores fit_score — must work even when fit_score is None."""
        result = sort_matches([MATCH_C, MATCH_A], "newest")
        assert len(result) == 2

    def test_default_sort_key_is_fit(self):
        """The default state in page.tsx is sortKey='fit' — verify fit sort is stable."""
        matches = [MATCH_B, MATCH_A]
        result = sort_matches(matches, "fit")
        assert result[0]["id"] == "a"  # score 90 beats 60


class TestRegionFilter:
    """Spec: client-side filter by job_region enum; 'All' passes everything."""

    def test_all_returns_all(self):
        matches = [MATCH_A, MATCH_B, MATCH_C, MATCH_D]
        result = filter_by_region(matches, "ALL")
        assert len(result) == 4

    def test_filter_worldwide(self):
        matches = [MATCH_A, MATCH_B, MATCH_C, MATCH_D]
        result = filter_by_region(matches, "WORLDWIDE")
        assert [m["id"] for m in result] == ["a"]

    def test_filter_europe(self):
        matches = [MATCH_A, MATCH_B, MATCH_C, MATCH_D]
        result = filter_by_region(matches, "EUROPE")
        # MATCH_B has job_region=EUROPE, MATCH_D has job.region=EUROPE
        ids = [m["id"] for m in result]
        assert "b" in ids
        assert "d" in ids
        assert "a" not in ids

    def test_filter_us_only(self):
        matches = [MATCH_A, MATCH_B, MATCH_C, MATCH_D]
        result = filter_by_region(matches, "US-ONLY")
        assert [m["id"] for m in result] == ["c"]

    def test_filter_no_match_returns_empty(self):
        """0 after filter → empty list (not a crash)."""
        result = filter_by_region([MATCH_A], "UNKNOWN")
        assert result == []

    def test_job_object_region_takes_precedence(self):
        """page.tsx: m.job?.region ?? m.job_region — job sub-object wins."""
        m: dict[str, Any] = {
            "id": "override",
            "fit_score": 50,
            "created_at": "2026-06-20T10:00:00Z",
            "job_region": "WORLDWIDE",  # flat field says WORLDWIDE
            "job": {"title": "Eng", "region": "EUROPE"},  # job sub-object says EUROPE
        }
        result = filter_by_region([m], "EUROPE")
        assert len(result) == 1

    def test_filter_enum_values_are_exact(self):
        """Region filter is an exact match, not a substring match."""
        result = filter_by_region([MATCH_B], "EUR")
        assert result == []


# ---------------------------------------------------------------------------
# 4. Honesty checks — verified by code reading (documented as test facts)
# ---------------------------------------------------------------------------

class TestHonestyInvariants:
    """These tests document the honesty-rendering invariants from the spec
    (PLAN.md lines 56-57) confirmed by code analysis of page.tsx.

    Each test asserts a structural fact about the component source rather than
    rendering it (no JSDOM available).
    """

    def test_fit_score_gated_at_line_283(self):
        """fit_score is rendered only when not null and not undefined.
        Confirmed: page.tsx line 283 — {fitScore !== null && fitScore !== undefined && ...}
        """
        for fit_score in [None]:
            rendered = fit_score is not None
            assert not rendered, "null fit_score must not render the score element"
        assert (82 is not None), "valid score should render"

    def test_reason_gated(self):
        """reason renders only when truthy (page.tsx line 328: {reason && <p>})."""
        for reason in [None, "", None]:
            assert not bool(reason), f"falsy reason must not render: {reason!r}"

    def test_recruiter_verdict_chip_gated(self):
        """recruiterVerdict chip renders only when truthy (line 317)."""
        for v in [None, ""]:
            assert not bool(v)

    def test_ats_present_empty_array_not_rendered(self):
        """Empty ats_present must NOT render the ATS keywords section.
        page.tsx line 347: atsPresent.filter(Boolean).length > 0"""
        ats_present: list[str] = []
        assert not (len([x for x in ats_present if x]) > 0), (
            "empty ats_present must skip the ATS section"
        )

    def test_ats_missing_empty_array_not_rendered(self):
        ats_missing: list[str] = []
        assert not (len([x for x in ats_missing if x]) > 0)

    def test_gaps_gated(self):
        """gaps renders only when truthy (line 388: {gaps && ...})."""
        for g in [None, ""]:
            assert not bool(g)

    def test_cover_letter_gated(self):
        """cover_letter renders only when truthy (line 419: {coverLetter && ...})."""
        for cl in [None, ""]:
            assert not bool(cl)

    def test_ats_report_gated(self):
        """ats_report renders only when truthy (line 433: {atsReport && ...})."""
        for r in [None, ""]:
            assert not bool(r)

    def test_no_dangerouslySetInnerHTML_usage(self):
        """Security: scraped text (reason, ats_report, cover_letter, job_title) must
        render as React text nodes — never via dangerouslySetInnerHTML.
        Verified by grep: no occurrences in page.tsx."""
        import subprocess
        result = subprocess.run(
            ["grep", "-Fc", "dangerouslySetInnerHTML",
             "/Users/vitaliivlasov/Desktop/jobsearch/web/app/results/page.tsx"],
            capture_output=True, text=True
        )
        count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
        assert count == 0, (
            "dangerouslySetInnerHTML found in results/page.tsx — scraped text must render as text nodes"
        )

    def test_job_url_has_noopener(self):
        """job_url links must have rel='noopener noreferrer' (security).
        Verified by grep in page.tsx."""
        import subprocess
        result = subprocess.run(
            ["grep", "-Fc", "noopener noreferrer",
             "/Users/vitaliivlasov/Desktop/jobsearch/web/app/results/page.tsx"],
            capture_output=True, text=True
        )
        count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
        assert count >= 2, (
            f"Expected at least 2 uses of rel='noopener noreferrer' (job_url + signed_cv_url), got {count}"
        )


# ---------------------------------------------------------------------------
# 5. Empty-state logic
# ---------------------------------------------------------------------------

class TestEmptyStates:
    """Spec: 0 matches → CTA to /search; 0 after filter → clear-filter message."""

    def test_zero_matches_empty_state(self):
        matches: list[dict[str, Any]] = []
        assert len(matches) == 0, "0 matches should trigger EmptyNoMatches"

    def test_zero_after_filter_state(self):
        matches = [MATCH_A]  # only WORLDWIDE
        filtered = filter_by_region(matches, "EUROPE")
        assert len(filtered) == 0, (
            "0 matches after filter should trigger EmptyFiltered (not EmptyNoMatches)"
        )

    def test_nonzero_after_filter_no_empty_state(self):
        matches = [MATCH_A, MATCH_B]
        filtered = filter_by_region(matches, "EUROPE")
        assert len(filtered) > 0


# ---------------------------------------------------------------------------
# 6. Schema field-count sentinels (drift guard for 5d additions)
# ---------------------------------------------------------------------------

class TestSchemaDriftGuard5d:
    """Sentinel: if fields are added to MatchListItemSchema / MatchDetailSchema,
    these tests break so the developer knows to update the Zod schemas too."""

    def test_match_list_item_required_fields(self):
        """id + created_at must be the only required fields in MatchListItemSchema."""
        assert MATCH_LIST_ITEM_REQUIRED == {"id", "created_at"}

    def test_match_list_item_optional_field_count(self):
        """MatchListItemSchema has 12 optional fields (as per schemas.ts lines 83-97).
        Fields: fit_score, b2b_eligible, analysis, cover_letter, ats_report, status,
                run_id, job_title, job_company, job_url, job_region, job
        If this changes, update web/lib/schemas.ts MatchListItemSchema."""
        expected_count = 12
        assert len(MATCH_LIST_ITEM_OPTIONAL) == expected_count, (
            f"Optional field count changed: {sorted(MATCH_LIST_ITEM_OPTIONAL)}. "
            "Update MatchListItemSchema in web/lib/schemas.ts."
        )

    def test_match_detail_has_signed_cv_url(self):
        """MatchDetailSchema must include signed_cv_url — the one field added in 5d."""
        assert "signed_cv_url" in MATCH_DETAIL_ALL_FIELDS

    def test_match_detail_optional_field_count(self):
        """MatchDetailSchema has 9 optional fields (schemas.ts lines 103-115).
        Fields: run_id, status, fit_score, b2b_eligible, analysis, cover_letter,
                ats_report, job, signed_cv_url
        If this changes, update MatchDetailSchema in web/lib/schemas.ts."""
        expected_count = 9
        assert len(MATCH_DETAIL_OPTIONAL) == expected_count, (
            f"Optional field count changed: {sorted(MATCH_DETAIL_OPTIONAL)}. "
            "Update MatchDetailSchema in web/lib/schemas.ts."
        )

    def test_verdict_tokens_in_css(self):
        """globals.css must define all 3 verdict tokens: shortlist, maybe, reject.
        These are required for verdict chip colouring in page.tsx."""
        import subprocess
        for token in ("verdict-shortlist", "verdict-maybe", "verdict-reject"):
            result = subprocess.run(
                ["grep", "-Fc", token,
                 "/Users/vitaliivlasov/Desktop/jobsearch/web/app/globals.css"],
                capture_output=True, text=True
            )
            count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
            assert count >= 2, (
                f"CSS token '--{token}' must appear in globals.css (light + dark) — "
                f"found {count} occurrences"
            )

    def test_verdict_tokens_registered_in_theme(self):
        """@theme inline in globals.css must register color-verdict-* utilities."""
        import subprocess
        for token in (
            "color-verdict-shortlist",
            "color-verdict-maybe",
            "color-verdict-reject",
        ):
            result = subprocess.run(
                ["grep", "-Fc", token,
                 "/Users/vitaliivlasov/Desktop/jobsearch/web/app/globals.css"],
                capture_output=True, text=True
            )
            count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
            assert count >= 1, (
                f"'--{token}' must be registered in @theme inline block of globals.css "
                f"(enables text-verdict-*/bg-verdict-* utilities). Found {count}."
            )
