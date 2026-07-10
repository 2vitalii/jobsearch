"""Offline unit tests for the _jobspy_fresh helper in jobsearch/sources.py.

Four key cases from the audit spec:
  1. Empty date + narrow window (<= STRICT_FRESH_WINDOW_H) → False (strict-drop)
  2. Empty date + wide window (> STRICT_FRESH_WINDOW_H) → True  (keep)
  3. Real old date + narrow window (24h) → False (via within_hours)
  4. Real fresh date + narrow window (24h) → True  (via within_hours)

No network, no Supabase, no LLM.
"""

from __future__ import annotations

import datetime as dt

import pytest

from jobsearch.sources import STRICT_FRESH_WINDOW_H, _jobspy_fresh


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_hours_ago(hours: float) -> str:
    """Return an ISO date string representing 'hours' ago from now."""
    d = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    return d.isoformat()


# ---------------------------------------------------------------------------
# Empty / unparseable date
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("date_str", ["", None, "   ", "not-a-date", "2 weeks ago"])
def test_empty_date_narrow_window_drops(date_str):
    """Empty / unparseable date in a narrow (<= STRICT_FRESH_WINDOW_H) window → False."""
    assert _jobspy_fresh(date_str, STRICT_FRESH_WINDOW_H) is False


@pytest.mark.parametrize("date_str", ["", None, "   ", "not-a-date"])
def test_empty_date_wide_window_keeps(date_str):
    """Empty / unparseable date in a wide (> STRICT_FRESH_WINDOW_H) window → True."""
    assert _jobspy_fresh(date_str, STRICT_FRESH_WINDOW_H + 1) is True


def test_empty_date_exactly_at_threshold_drops():
    """At exactly STRICT_FRESH_WINDOW_H (not strictly greater) → False."""
    assert _jobspy_fresh("", STRICT_FRESH_WINDOW_H) is False


def test_empty_date_one_over_threshold_keeps():
    """One hour over STRICT_FRESH_WINDOW_H → True."""
    assert _jobspy_fresh("", STRICT_FRESH_WINDOW_H + 1) is True


# ---------------------------------------------------------------------------
# Parseable date — delegates to within_hours
# ---------------------------------------------------------------------------

def test_old_date_narrow_window_drops():
    """A date 2 weeks ago fails a 24h window."""
    old = _iso_hours_ago(24 * 14)  # 2 weeks ago
    assert _jobspy_fresh(old, 24) is False


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
    fresh = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    iso_z = fresh.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _jobspy_fresh(iso_z, 24) is True


def test_accepts_date_only_string():
    """Date-only string (YYYY-MM-DD) — today's date passes a wide window."""
    today = dt.date.today().isoformat()
    assert _jobspy_fresh(today, 24 * 30) is True
