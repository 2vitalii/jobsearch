"""Unit tests for the MAX_QUERY_COMBINATIONS cap in jobsearch.sources.

These tests exercise the cap logic without making real network calls by
monkey-patching scrape_jobs to return a controlled DataFrame.
"""

from __future__ import annotations

import pandas as pd
import pytest

from jobsearch.sources import MAX_QUERY_COMBINATIONS, collect_jobspy
from jobsearch.models import SearchParams


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_params(keywords: list[str], locations: list[str]) -> SearchParams:
    return SearchParams(
        keywords=keywords,
        locations=locations,
        period_hours=168,
        work_format="remote",
    )


def _empty_df() -> pd.DataFrame:
    """Return an empty DataFrame (simulates a scrape with 0 results)."""
    return pd.DataFrame(
        columns=[
            "site", "title", "company", "location", "job_url",
            "description", "date_posted", "is_remote",
        ]
    )


# ---------------------------------------------------------------------------
# Cap enforcement: number of scrape_jobs calls
# ---------------------------------------------------------------------------

def test_cap_limits_scrape_jobs_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """With more combos than the cap, scrape_jobs is called at most MAX_QUERY_COMBINATIONS times."""
    call_count = 0

    def fake_scrape_jobs(**kwargs: object) -> pd.DataFrame:  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        return _empty_df()

    monkeypatch.setattr("jobsearch.sources.scrape_jobs", fake_scrape_jobs)

    # 5 keywords × 5 locations = 25 combos > 18 cap
    params = _make_params(
        keywords=["role1", "role2", "role3", "role4", "role5"],
        locations=["germany", "netherlands", "ireland", "poland", "spain"],
    )
    collect_jobspy(params)

    assert call_count == MAX_QUERY_COMBINATIONS, (
        f"Expected exactly {MAX_QUERY_COMBINATIONS} calls, got {call_count}"
    )


def test_under_cap_all_combos_issued(monkeypatch: pytest.MonkeyPatch) -> None:
    """When total combos <= cap, every (keyword, location) pair is scraped."""
    call_count = 0

    def fake_scrape_jobs(**kwargs: object) -> pd.DataFrame:  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        return _empty_df()

    monkeypatch.setattr("jobsearch.sources.scrape_jobs", fake_scrape_jobs)

    # 2 keywords × 3 locations = 6 combos — well under cap
    params = _make_params(
        keywords=["engineer", "specialist"],
        locations=["germany", "ireland", "poland"],
    )
    collect_jobspy(params)

    assert call_count == 6


def test_cap_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hitting the cap never raises an exception — it just stops issuing calls."""
    monkeypatch.setattr("jobsearch.sources.scrape_jobs", lambda **_: _empty_df())

    # 10 × 10 = 100 combos — far above cap
    params = _make_params(
        keywords=[f"role{i}" for i in range(10)],
        locations=["germany", "netherlands", "ireland", "poland", "spain",
                   "france", "sweden", "italy", "austria", "belgium"],
    )
    result = collect_jobspy(params)  # must not raise
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Always-on log output
# ---------------------------------------------------------------------------

def test_combo_summary_logged(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """The [scrape] summary line is always printed (not behind FILTER_DEBUG)."""
    monkeypatch.setattr("jobsearch.sources.scrape_jobs", lambda **_: _empty_df())

    params = _make_params(
        keywords=["support engineer", "project coordinator", "solutions engineer"],
        locations=["ireland"],
    )
    collect_jobspy(params)

    out = capsys.readouterr().out
    assert "[scrape]" in out
    assert "combos" in out
    assert "issuing" in out


def test_skipped_shown_when_over_cap(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """When total combos > cap, the log includes '; skipped N'."""
    monkeypatch.setattr("jobsearch.sources.scrape_jobs", lambda **_: _empty_df())

    # 5 × 5 = 25 > 18 cap
    params = _make_params(
        keywords=["r1", "r2", "r3", "r4", "r5"],
        locations=["germany", "netherlands", "ireland", "poland", "spain"],
    )
    collect_jobspy(params)

    out = capsys.readouterr().out
    assert "skipped" in out


def test_skipped_not_shown_under_cap(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """When total combos <= cap, 'skipped' does NOT appear in the log."""
    monkeypatch.setattr("jobsearch.sources.scrape_jobs", lambda **_: _empty_df())

    params = _make_params(
        keywords=["engineer"],
        locations=["ireland"],
    )
    collect_jobspy(params)

    out = capsys.readouterr().out
    assert "[scrape]" in out
    assert "skipped" not in out


# ---------------------------------------------------------------------------
# Dedup: same vacancy found via two different keywords collapses to one record
# ---------------------------------------------------------------------------

def test_cross_keyword_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A vacancy returned under two different keyword searches collapses to 1 result
    after filters.dedupe() is applied in scrape()."""
    from jobsearch.sources import scrape
    import datetime as dt

    now_str = dt.datetime.now(dt.timezone.utc).isoformat()

    # Simulate a DataFrame row that would pass all filters.
    def fake_scrape_jobs(**kwargs: object) -> pd.DataFrame:  # noqa: ARG001
        return pd.DataFrame([{
            "site": "linkedin",
            "title": "Support Engineer",
            "company": "Acme Corp",
            "location": "Remote",
            "job_url": "https://example.com/job/123",
            "description": "Remote technical support role",
            "date_posted": now_str[:10],
            "is_remote": True,
        }])

    monkeypatch.setattr("jobsearch.sources.scrape_jobs", fake_scrape_jobs)
    # Disable remote boards and ATS so only collect_jobspy is exercised.
    monkeypatch.setattr("jobsearch.sources.USE_REMOTE_BOARDS", False)
    monkeypatch.setattr("jobsearch.sources.USE_ATS", False)

    params = SearchParams(
        keywords=["support engineer", "technical support"],
        locations=["ireland"],
        period_hours=8760,  # wide window — no strict-drop on missing dates
        work_format="remote",
        loose=True,  # skip role-keyword filter so our fake title passes
    )

    result = scrape(params)
    # Even though the same vacancy row was returned for two keyword searches,
    # dedup should collapse them to exactly one entry.
    assert len(result) == 1, (
        f"Expected 1 deduped result, got {len(result)}: {[j.title for j in result]}"
    )
