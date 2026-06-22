"""Unit tests for the pure title/description filters in jobsearch.filters.

These run without network or files — they only exercise pure functions.
"""

from jobsearch import filters


# --- blocked() — negative title keywords / seniority -------------------------
def test_blocked_rejects_seniority_and_offrole():
    assert filters.blocked("Senior Support Engineer") is True      # "senior"
    assert filters.blocked("Sales Engineer") is True               # "sales engineer"
    assert filters.blocked("Head of Customer Success") is True     # "head of"
    assert filters.blocked("Data Engineer") is True                # "data engineer"


def test_blocked_allows_target_roles():
    assert filters.blocked("Support Engineer") is False
    assert filters.blocked("Project Coordinator") is False
    assert filters.blocked("") is False


# --- matches_role() — role keyword whitelist ---------------------------------
def test_matches_role_positive():
    assert filters.matches_role("Technical Support Engineer") is True
    assert filters.matches_role("Project Coordinator") is True
    assert filters.matches_role("Implementation Consultant") is True


def test_matches_role_negative():
    assert filters.matches_role("Marketing Manager") is False
    assert filters.matches_role("Graphic Designer") is False
    assert filters.matches_role("") is False


# --- remote_ok() — strictly-remote gate --------------------------------------
def test_remote_ok_requires_positive_signal():
    # No remote signal and no flag -> treated as NOT remote.
    assert filters.remote_ok("Support Engineer", "We are a great company", None) is False
    # Explicit remote phrasing in description.
    assert filters.remote_ok("Support Engineer", "This is a fully remote role", None) is True


def test_remote_ok_respects_is_remote_flag():
    assert filters.remote_ok("Support Engineer", "", True) is True
    assert filters.remote_ok("Support Engineer", "remote", False) is False


def test_remote_ok_rejects_hybrid_title_and_desc():
    assert filters.remote_ok("Support Engineer (Hybrid)", "remote", None) is False
    # Hybrid signalled in the body wins even with the is_remote flag set True.
    assert filters.remote_ok(
        "Support Engineer", "This is a hybrid role, 3 days in the office", True
    ) is False


def test_remote_ok_jobspy_escaped_onsite_regression():
    """JobSpy returns markdown with escaped hyphens ('on\\-site'); remote_ok must
    normalise the backslash before matching, otherwise on-site jobs leak through."""
    assert filters.remote_ok("Support Engineer on\\-site", "work from home remote", None) is False
    # Unescaped form must also be rejected (sanity).
    assert filters.remote_ok("Support Engineer on-site", "remote", None) is False


# --- classify_region() — region bucketing ------------------------------------
def test_classify_region_buckets():
    assert filters.classify_region("Berlin, Germany", "Support", "") == "EUROPE"
    assert filters.classify_region("", "Support", "work from anywhere, worldwide") == "WORLDWIDE"
    assert filters.classify_region("", "Support", "US only, must reside in the US") == "US-ONLY"
    assert filters.classify_region("Mars Colony", "Support", "") == "UNKNOWN"


def test_classify_region_hint_country_fallback():
    # Empty blob but EU hint country -> EUROPE.
    assert filters.classify_region("", "Support", "", hint_country="Poland") == "EUROPE"


def test_classify_region_us_only_precedence():
    # US-ONLY is checked before WORLDWIDE/EUROPE.
    assert filters.classify_region(
        "Remote, Germany", "Support", "US only, authorized to work in the United States"
    ) == "US-ONLY"


# ---------------------------------------------------------------------------
# Edge-case additions (STEP 1)
# ---------------------------------------------------------------------------

# --- matches_role() edge cases -----------------------------------------------

def test_matches_role_none_and_empty():
    # None is coerced to "" via `(text or "").lower()` — no match possible.
    assert filters.matches_role(None) is False
    # Already covered by the negative suite, but explicit for the None path.
    assert filters.matches_role("") is False


def test_matches_role_case_insensitive():
    # All-uppercase keyword must still match.
    assert filters.matches_role("INTEGRATION SPECIALIST") is True


def test_matches_role_substring_of_keyword():
    # "integration" is a standalone entry in ROLE_KEYWORDS (not a multi-word phrase).
    # The `in` check is purely substring-based, so a word that *contains* a
    # ROLE_KEYWORD as a literal substring will also match.
    # "Reintegration" contains "integration" as a substring -> True.
    assert filters.matches_role("Reintegration Specialist") is True


# --- blocked() edge cases ----------------------------------------------------

def test_blocked_none_and_empty():
    # None is coerced to "" via `(title or "").lower()` — no negative keyword present.
    assert filters.blocked(None) is False
    # Already guarded by existing test, restated for the None path explicitly.
    assert filters.blocked("") is False


def test_blocked_case_insensitive():
    # Uppercase variant of a negative keyword must still trigger blocked().
    assert filters.blocked("SENIOR IMPLEMENTATION CONSULTANT") is True


def test_blocked_staff_trailing_space_semantics():
    # NEGATIVE_TITLE_KEYWORDS contains "staff " (with a trailing space).
    # "staffing" does NOT contain the two-char sequence "staff " (the char after 'f' is 'i',
    # not a space), so "staffing coordinator" must NOT be blocked.
    assert filters.blocked("staffing coordinator") is False
    # "Staff Support Engineer" lowercases to "staff support engineer", which *does*
    # contain the substring "staff " (staff followed by space) -> blocked.
    assert filters.blocked("Staff Support Engineer") is True


def test_blocked_role_and_seniority_overlap():
    # "support engineer" is a valid role keyword, but "senior" is a NEGATIVE_TITLE_KEYWORD.
    # blocked() checks only NEGATIVE_TITLE_KEYWORDS, so the presence of a valid role keyword
    # does NOT override a seniority block — the title is still blocked.
    assert filters.blocked("Senior Support Engineer") is True


# --- classify_region() edge cases --------------------------------------------

def test_classify_region_all_none():
    # All None values produce an empty blob -> no hint matches -> UNKNOWN.
    assert filters.classify_region(None, None, None) == "UNKNOWN"


def test_classify_region_all_empty():
    assert filters.classify_region("", "", "") == "UNKNOWN"


def test_classify_region_worldwide_beats_europe():
    # WORLDWIDE_HINTS are checked before EUROPE_HINTS / EU_COUNTRY_NAMES.
    # A blob containing both "worldwide" and an EU country name returns "WORLDWIDE".
    assert filters.classify_region("", "", "worldwide opportunity, germany office") == "WORLDWIDE"


def test_classify_region_europe_via_europe_hints():
    # "emea" is in EUROPE_HINTS -> EUROPE.
    assert filters.classify_region("", "", "open to emea region") == "EUROPE"


def test_classify_region_europe_via_eu_country_names():
    # "uk" is in EU_COUNTRY_NAMES -> EUROPE.
    assert filters.classify_region("", "", "based in uk") == "EUROPE"


def test_classify_region_hint_country_non_eu_fallback():
    # hint_country is truthy but NOT in EU_COUNTRY_NAMES -> UNKNOWN.
    assert filters.classify_region("", "", "", hint_country="Brazil") == "UNKNOWN"


def test_classify_region_hint_country_empty_string():
    # hint_country="" is falsy -> the EU-hint branch is skipped -> UNKNOWN.
    assert filters.classify_region("", "", "", hint_country="") == "UNKNOWN"


def test_classify_region_non_string_coercion():
    # Non-string values in positional slots are coerced via str(x).lower().
    # An integer produces a numeric string with no hint matches -> UNKNOWN.
    # This must not raise.
    assert filters.classify_region(42, 0, 0) == "UNKNOWN"


# --- remote_ok() edge cases --------------------------------------------------

def test_remote_ok_all_none():
    # All None: title/"" desc/"" blob has no remote signal, no flag -> False.
    assert filters.remote_ok(None, None, None) is False


def test_remote_ok_relocation_required_blocks():
    # "relocation required" in desc with no "no relocation" guard -> False.
    assert filters.remote_ok(
        "Support Engineer",
        "relocation required to join our team",
        None,
    ) is False


def test_remote_ok_no_relocation_required_not_blocked():
    # "no relocation required" in desc: the relocation guard checks
    # `"relocation required" in d and "no relocation" not in d`.
    # "no relocation" IS in d -> guard is skipped.
    # With a remote signal present the function should return True.
    assert filters.remote_ok(
        "Support Engineer",
        "no relocation required, work from home",
        None,
    ) is True


def test_remote_ok_flag_false_beats_remote_signal():
    # is_remote_flag=False is checked AFTER hybrid guards but BEFORE the signal scan.
    # A "fully remote" phrase in desc cannot override an explicit False flag.
    assert filters.remote_ok("Support Engineer", "fully remote position", False) is False


def test_remote_ok_hybrid_desc_beats_true_flag():
    # HYBRID_DESC is checked before the `is_remote_flag is True` shortcut.
    # A clearly hybrid description must return False even when the flag says True.
    assert filters.remote_ok(
        "Support Engineer",
        "this is a hybrid role, 3 days in the office",
        True,
    ) is False


def test_remote_ok_hybrid_title_gate_is_title_only():
    # "onsite" is in HYBRID_TITLE but is NOT a HYBRID_DESC phrase.
    # When "onsite" appears only in the description (not the title) the HYBRID_TITLE
    # guard (which inspects only `t`) is NOT triggered.
    # With an explicit remote signal the job is treated as remote.
    assert filters.remote_ok(
        "Support Engineer",
        "onsite option available, fully remote preferred",
        None,
    ) is True


def test_remote_ok_backslash_normalisation_in_desc():
    # JobSpy sometimes escapes hyphens in descriptions too (e.g. r'on\-site').
    # remote_ok normalises both title and desc via .replace("\\", "").
    # r'days on\-site' -> after normalise -> 'days on-site' which is in HYBRID_DESC
    # -> must return False (same as the unescaped form).
    escaped_desc = r"we offer 3 days on\-site per week"
    assert filters.remote_ok("Support Engineer", escaped_desc, None) is False
    # Sanity: unescaped form gives the same result.
    assert filters.remote_ok("Support Engineer", "we offer 3 days on-site per week", None) is False
