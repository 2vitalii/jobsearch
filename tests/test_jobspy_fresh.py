"""Offline unit tests for the _jobspy_fresh helper in jobsearch/sources.py.

New behavior (post fix/sources empty-date):
  - Empty / None / unparseable date → always True (keep), regardless of window.
    Rationale: JobSpy passes hours_old to LinkedIn server-side; a missing date
    is an extraction gap, not staleness evidence.
  - Parseable date older than the window → False (dropped, unchanged).
  - Parseable date within the window → True (kept, unchanged).

No network, no Supabase, no LLM.
"""

from __future__ import annotations

import datetime as dt
from datetime import timezone

import pytest

from jobsearch.sources import _jobspy_fresh


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_hours_ago(hours: float) -> str:
    """Return an ISO date string representing 'hours' ago from now."""
    d = dt.datetime.now(timezone.utc) - dt.timedelta(hours=hours)
    return d.isoformat()


# ---------------------------------------------------------------------------
# Empty / None / whitespace → always keep (True), any window
# ---------------------------------------------------------------------------

def test_empty_string_narrow_window_keeps():
    """Empty string passes a narrow 24h window (NEW: keep, not drop)."""
    assert _jobspy_fresh("", 24) is True


def test_whitespace_string_narrow_window_keeps():
    """Whitespace-only string passes a narrow 24h window."""
    assert _jobspy_fresh("   ", 24) is True


def test_none_narrow_window_keeps():
    """None passes a narrow 24h window."""
    assert _jobspy_fresh(None, 24) is True


def test_empty_date_wide_window_keeps():
    """Empty date in a wide window (720h) → True (unchanged from old behavior)."""
    assert _jobspy_fresh("", 720) is True


def test_none_wide_window_keeps():
    """None in a wide window → True."""
    assert _jobspy_fresh(None, 720) is True


# ---------------------------------------------------------------------------
# Unparseable non-empty strings → keep (True)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("date_str", ["2 hours ago", "not-a-date", "yesterday", "2 weeks ago"])
def test_unparseable_string_keeps(date_str):
    """Unparseable non-empty strings are kept (JobSpy hours_old already filtered)."""
    assert _jobspy_fresh(date_str, 24) is True


# ---------------------------------------------------------------------------
# Parseable date — delegates to within_hours (unchanged behavior)
# ---------------------------------------------------------------------------

def test_old_date_narrow_window_drops():
    """A date-only string 2025-02-12 fails a 24h window — stays False."""
    assert _jobspy_fresh("2025-02-12", 24) is False


def test_fresh_date_narrow_window_keeps():
    """A date 1 hour ago passes a 24h window."""
    fresh = _iso_hours_ago(1)
    assert _jobspy_fresh(fresh, 24) is True


def test_old_date_wide_window_keeps():
    """A 2-week-old date passes a 30-day (720h) window."""
    old = _iso_hours_ago(24 * 14)  # 2 weeks ago
    assert _jobspy_fresh(old, 24 * 30) is True


def test_boundary_date_just_inside_window():
    """A date 23h 50m ago passes a 24h window."""
    almost_24h = _iso_hours_ago(23.83)
    assert _jobspy_fresh(almost_24h, 24) is True


def test_boundary_date_just_outside_window():
    """A date 25h ago fails a 24h window."""
    over_24h = _iso_hours_ago(25)
    assert _jobspy_fresh(over_24h, 24) is False


# ---------------------------------------------------------------------------
# ISO variants accepted
# ---------------------------------------------------------------------------

def test_accepts_iso_with_z_suffix():
    """ISO string with Z suffix (UTC) is parsed correctly."""
    fresh = dt.datetime.now(timezone.utc) - dt.timedelta(hours=1)
    iso_z = fresh.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _jobspy_fresh(iso_z, 24) is True


def test_accepts_date_only_string():
    """Date-only string (YYYY-MM-DD) — today's date passes a wide window."""
    today = dt.date.today().isoformat()
    assert _jobspy_fresh(today, 24 * 30) is True
