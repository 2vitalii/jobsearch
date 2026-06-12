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
