"""Unit tests for _normalize_locations in jobsearch.sources.

Pure function tests — no network, no file I/O.
"""

from jobsearch.sources import (
    EU_EXPANSION_COUNTRIES,
    REGION_ALIASES,
    _normalize_locations,
)


# ---------------------------------------------------------------------------
# Suffix stripping
# ---------------------------------------------------------------------------

def test_strip_parenthetical_remote_suffix():
    """Trailing '(Remote)' is stripped."""
    assert _normalize_locations(["Poland (Remote)"]) == ["Poland"]


def test_strip_parenthetical_hybrid_suffix():
    """Trailing '(Hybrid)' is stripped."""
    assert _normalize_locations(["Germany (Hybrid)"]) == ["Germany"]


def test_strip_parenthetical_onsite_suffix():
    """Trailing '(On-site)' and '(Onsite)' are stripped."""
    assert _normalize_locations(["Ireland (On-site)"]) == ["Ireland"]
    assert _normalize_locations(["France (Onsite)"]) == ["France"]


def test_strip_dash_remote_suffix():
    """Trailing '- Remote' (or em-dash) is stripped."""
    assert _normalize_locations(["Spain - Remote"]) == ["Spain"]
    assert _normalize_locations(["Sweden — Remote"]) == ["Sweden"]


def test_strip_dash_hybrid_suffix():
    """Trailing '- Hybrid' (dash) is stripped."""
    assert _normalize_locations(["Italy - Hybrid"]) == ["Italy"]


def test_counterexample_internal_remote_preserved():
    """CRITICAL: 'Remote Foods Inc (Remote)' → 'Remote Foods Inc'.
    The internal 'Remote' in the company name must be preserved;
    only the TRAILING '(Remote)' suffix is stripped."""
    result = _normalize_locations(["Remote Foods Inc (Remote)"])
    assert result == ["Remote Foods Inc"]
    # The company name part 'Remote Foods Inc' must be present.
    assert "Remote Foods" in result[0]


def test_no_suffix_passthrough():
    """Location with no suffix is returned as-is (after validation)."""
    result = _normalize_locations(["Poland"])
    assert result == ["Poland"]


# ---------------------------------------------------------------------------
# Region alias expansion
# ---------------------------------------------------------------------------

def test_eu_expansion():
    """'European Union' expands to EU_EXPANSION_COUNTRIES."""
    result = _normalize_locations(["European Union"])
    assert result == EU_EXPANSION_COUNTRIES


def test_eu_lowercase_expansion():
    """'eu' (case-insensitive) expands to EU_EXPANSION_COUNTRIES."""
    result = _normalize_locations(["EU"])
    assert result == EU_EXPANSION_COUNTRIES


def test_emea_expansion():
    """'EMEA' expands to EU_EXPANSION_COUNTRIES."""
    result = _normalize_locations(["EMEA"])
    assert result == EU_EXPANSION_COUNTRIES


def test_europe_expansion():
    """'Europe' expands to EU_EXPANSION_COUNTRIES."""
    result = _normalize_locations(["Europe"])
    assert result == EU_EXPANSION_COUNTRIES


def test_expansion_countries_are_valid_jobspy_countries():
    """Every country in EU_EXPANSION_COUNTRIES must be in VALID_JOBSPY_COUNTRIES."""
    from jobsearch.sources import VALID_JOBSPY_COUNTRIES
    for country in EU_EXPANSION_COUNTRIES:
        assert country in VALID_JOBSPY_COUNTRIES, (
            f"'{country}' in EU_EXPANSION_COUNTRIES is NOT in VALID_JOBSPY_COUNTRIES"
        )


# ---------------------------------------------------------------------------
# Unrecognized location passthrough (with warning — no crash)
# ---------------------------------------------------------------------------

def test_unrecognized_location_passthrough(capsys):
    """An unrecognized location is kept as-is but a warning is printed."""
    result = _normalize_locations(["Narnia"])
    assert result == ["Narnia"]
    captured = capsys.readouterr()
    assert "unrecognized" in captured.out
    assert "Narnia" in captured.out


def test_unrecognized_warns_but_no_exception():
    """No exception is raised for an unknown location."""
    result = _normalize_locations(["SomeUnknownRegion42"])
    assert result == ["SomeUnknownRegion42"]


# ---------------------------------------------------------------------------
# Already-valid country passthrough
# ---------------------------------------------------------------------------

def test_already_valid_country_passthrough():
    """A recognized country passes through unchanged."""
    result = _normalize_locations(["germany"])
    assert result == ["germany"]


def test_multiple_valid_countries():
    """Multiple valid countries are returned in input order."""
    result = _normalize_locations(["ireland", "poland"])
    assert result == ["ireland", "poland"]


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def test_dedupe_explicit_country_and_eu_expansion():
    """If 'germany' is listed explicitly AND EU is also listed, 'germany'
    should appear only once in the output."""
    result = _normalize_locations(["germany", "EU"])
    # 'germany' from explicit entry + EU expansion — no dupe
    assert result.count("germany") == 1
    # All other EU countries should still appear
    for country in EU_EXPANSION_COUNTRIES:
        assert country in result


def test_dedupe_suffix_stripped_dupe():
    """'Poland' and 'Poland (Remote)' both normalize to 'Poland'; only one entry."""
    result = _normalize_locations(["Poland", "Poland (Remote)"])
    assert result.count("Poland") == 1


def test_dedupe_preserves_order():
    """Order of first occurrence is preserved after dedup."""
    result = _normalize_locations(["ireland", "germany", "ireland"])
    assert result.index("ireland") < result.index("germany")
    assert result.count("ireland") == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_list():
    """Empty input returns empty list."""
    assert _normalize_locations([]) == []


def test_region_aliases_set_contains_expected_values():
    """REGION_ALIASES contains the four expected strings."""
    assert "european union" in REGION_ALIASES
    assert "eu" in REGION_ALIASES
    assert "emea" in REGION_ALIASES
    assert "europe" in REGION_ALIASES


def test_eu_expansion_with_suffix():
    """'European Union (Remote)' → strips suffix then expands."""
    result = _normalize_locations(["European Union (Remote)"])
    assert result == EU_EXPANSION_COUNTRIES
