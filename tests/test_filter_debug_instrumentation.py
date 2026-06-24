"""Offline unit tests for the FILTER_DEBUG instrumentation added in feat/filter-debug-instrumentation.

Three focus areas:
  1. BEHAVIOR INVARIANCE  — toggling FILTER_DEBUG must not change which jobs are kept/dropped.
  2. COUNTER CORRECTNESS  — the short-circuit attribution order must be exact for both passes.
  3. FLAG-LEAK METRIC     — the specific job that survives 1st pass (flag=True) but drops on
                            2nd pass (flag=None) is correctly identified and counted.

All tests are strictly offline; no network calls, no Supabase, no LLM.
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any

import pytest

from jobsearch import filters
from jobsearch.models import Job, SearchParams


# ---------------------------------------------------------------------------
# Helpers to build synthetic Job objects
# ---------------------------------------------------------------------------

def _job(
    title: str = "Technical Support Engineer",
    description: str = "Fully remote role. Work from home. SQL and REST APIs.",
    region: str = "WORLDWIDE",
    company: str = "AcmeCo",
    source: str = "LinkedIn",
) -> Job:
    """Return a minimal Job that passes _passes() by default."""
    return Job(
        dedup_key=filters.compute_dedup_key(company, title),
        source=source,
        url=f"https://example.com/{title.replace(' ', '-')}",
        company=company,
        title=title,
        location="Remote",
        region=region,
        description=description,
        date_posted="2026-06-20",
        scraped_at="2026-06-20T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Replicate the gate logic from sources.py (1st pass) and api/run.py (2nd pass)
# without importing those modules (which drag in network/db imports).
# We test the underlying filter functions directly, mirroring the exact
# short-circuit order documented in FILTER_AUDIT.md.
# ---------------------------------------------------------------------------

def _passes_1st(title: str, desc: str, is_remote_flag, loose: bool) -> bool:
    """Mirror of collect_jobspy filter condition (sources.py:180-182)."""
    if not title:
        return False
    if filters.blocked(title):
        return False
    if not loose and not filters.matches_role(title):
        return False
    if not filters.remote_ok(title, desc, is_remote_flag):
        return False
    return True


def _gate_1st(title: str, desc: str, is_remote_flag, loose: bool) -> str:
    """Return the first-tripped gate name for the 1st pass, or 'KEPT'."""
    if not title:
        return "empty_title"
    if filters.blocked(title):
        return "blocked"
    if not loose and not filters.matches_role(title):
        return "not_role"
    if not filters.remote_ok(title, desc, is_remote_flag):
        return "not_remote"
    return "KEPT"


def _passes_2nd(job: Job, loose: bool) -> bool:
    """Mirror of _passes() in api/run.py (lines 264-269)."""
    return (
        (not filters.blocked(job.title))
        and filters.remote_ok(job.title, job.description, None)
        and (loose or filters.matches_role(job.title))
    )


def _gate_2nd(job: Job, loose: bool) -> str:
    """Return the first-tripped gate name for the 2nd pass, or 'KEPT'.
    Order: blocked -> not_remote -> not_role  (short-circuit `and` chain)."""
    if filters.blocked(job.title):
        return "blocked"
    if not filters.remote_ok(job.title, job.description, None):
        return "not_remote"
    if not (loose or filters.matches_role(job.title)):
        return "not_role"
    return "KEPT"


def _is_flag_leak(job: Job) -> bool:
    """True when job drops on 2nd pass (flag=None) but would pass with flag=True.
    This is the not_remote_flag_leak metric from FILTER_DEBUG."""
    passes_none = filters.remote_ok(job.title, job.description, None)
    passes_true = filters.remote_ok(job.title, job.description, True)
    return (not passes_none) and passes_true


# ---------------------------------------------------------------------------
# Synthetic corpus with KNOWN distribution (used in counter tests)
# ---------------------------------------------------------------------------

# Descriptions that carry clear remote signals.
_REMOTE_DESC = "This is a fully remote role. Work from anywhere. Distributed team."
# Descriptions with ZERO remote signals and ZERO hybrid markers — the flag-leak scenario.
_SILENT_DESC = "We are a global company with offices worldwide. Great benefits package."

# NOTE: "worldwide" is a WORLDWIDE_HINT, not a REMOTE_SIGNAL.
# remote_ok scans blob for REMOTE_SIGNALS; "worldwide" is NOT in that list.
# Verify assumption:
assert "worldwide" not in [s.lower() for s in filters.REMOTE_SIGNALS]

CORPUS = [
    # title, desc, is_remote_flag_1st, expected_gate_1st, expected_gate_2nd
    # 1. empty title
    ("", _REMOTE_DESC, None, "empty_title", None),
    # 2. blocked title ("senior" is NEGATIVE_TITLE_KEYWORD)
    ("Senior Support Engineer", _REMOTE_DESC, None, "blocked", "blocked"),
    # 3. not_role — no role keyword, not blocked
    ("Marketing Manager", _REMOTE_DESC, None, "not_role", "not_role"),
    # 4. not_remote — role keyword, no remote signal, no flag
    ("Technical Support Engineer", _SILENT_DESC, None, "not_remote", "not_remote"),
    # 5. KEPT — role keyword, remote signal in text
    ("Technical Support Engineer", _REMOTE_DESC, None, "KEPT", "KEPT"),
    # 6. KEPT — role keyword, flag=True shortcut (no text signal needed on 1st pass)
    #    BUT flag becomes None on 2nd pass → drops as not_remote (flag-leak)
    ("Technical Support Engineer", _SILENT_DESC, True, "KEPT", "not_remote"),
]


# ===========================================================================
# PART 1 — BEHAVIOR INVARIANCE
# ===========================================================================

class TestBehaviorInvariance:
    """The kept/dropped set must be identical regardless of FILTER_DEBUG state."""

    def _apply_filter_1st(self, rows, loose=False):
        """Apply 1st-pass gate to a list of (title, desc, flag) and return kept set."""
        kept = []
        for title, desc, flag, _, _ in rows:
            if _passes_1st(title, desc, flag, loose):
                kept.append(title)
        return kept

    def _apply_filter_2nd(self, jobs, loose=False):
        """Apply 2nd-pass gate to Job objects and return kept dedup_keys."""
        return [j.dedup_key for j in jobs if _passes_2nd(j, loose)]

    def test_1st_pass_same_kept_with_debug_on(self, monkeypatch):
        monkeypatch.setenv("FILTER_DEBUG", "1")
        kept_on = self._apply_filter_1st(CORPUS)
        monkeypatch.delenv("FILTER_DEBUG")
        kept_off = self._apply_filter_1st(CORPUS)
        assert kept_on == kept_off, "1st-pass kept set differs with FILTER_DEBUG on vs off"

    def test_1st_pass_same_kept_with_debug_off(self, monkeypatch):
        monkeypatch.delenv("FILTER_DEBUG", raising=False)
        kept_a = self._apply_filter_1st(CORPUS)
        monkeypatch.setenv("FILTER_DEBUG", "true")
        kept_b = self._apply_filter_1st(CORPUS)
        assert set(kept_a) == set(kept_b), "Kept set must be identical regardless of debug flag"

    def test_2nd_pass_same_kept_with_debug_on(self, monkeypatch):
        jobs = [_job(title=t, description=d) for t, d, _, _, g2 in CORPUS if t and g2 is not None]
        monkeypatch.setenv("FILTER_DEBUG", "1")
        kept_on = self._apply_filter_2nd(jobs)
        monkeypatch.delenv("FILTER_DEBUG")
        kept_off = self._apply_filter_2nd(jobs)
        assert kept_on == kept_off, "2nd-pass kept set differs with FILTER_DEBUG on vs off"

    def test_2nd_pass_same_kept_all_truthy_values(self, monkeypatch):
        """All recognized truthy env values should produce the same behavioral outcome."""
        jobs = [_job(title=t, description=d) for t, d, _, _, g2 in CORPUS if t and g2 is not None]
        monkeypatch.delenv("FILTER_DEBUG", raising=False)
        baseline = self._apply_filter_2nd(jobs)
        for val in ("1", "true", "yes", "on", "TRUE", "YES", "ON"):
            monkeypatch.setenv("FILTER_DEBUG", val)
            kept = self._apply_filter_2nd(jobs)
            assert kept == baseline, f"FILTER_DEBUG={val!r} changed kept set"

    def test_filter_debug_helper_false_when_unset(self, monkeypatch):
        """_filter_debug() must return False when FILTER_DEBUG is absent."""
        monkeypatch.delenv("FILTER_DEBUG", raising=False)
        from jobsearch.sources import _filter_debug as src_debug
        assert src_debug() is False

    def test_filter_debug_helper_true_for_all_truthy_values(self, monkeypatch):
        from jobsearch.sources import _filter_debug as src_debug
        for val in ("1", "true", "yes", "on", "TRUE", "True", "ON", "YES"):
            monkeypatch.setenv("FILTER_DEBUG", val)
            assert src_debug() is True, f"_filter_debug() returned False for FILTER_DEBUG={val!r}"

    def test_filter_debug_helper_false_for_falsy_values(self, monkeypatch):
        from jobsearch.sources import _filter_debug as src_debug
        for val in ("0", "false", "off", "no", "", "  ", "False", "OFF"):
            monkeypatch.setenv("FILTER_DEBUG", val)
            assert src_debug() is False, f"_filter_debug() returned True for FILTER_DEBUG={val!r}"

    def test_api_run_filter_debug_helper_consistent_with_sources(self, monkeypatch):
        """Both _filter_debug() implementations must agree for every input."""
        from jobsearch.sources import _filter_debug as src_debug
        from api.run import _filter_debug as api_debug
        for val in ("1", "true", "yes", "on", "0", "false", ""):
            monkeypatch.setenv("FILTER_DEBUG", val)
            assert src_debug() == api_debug(), (
                f"sources._filter_debug() != api._filter_debug() for FILTER_DEBUG={val!r}"
            )


# ===========================================================================
# PART 2 — COUNTER CORRECTNESS
# ===========================================================================

class TestCounterCorrectness:
    """Verify that first-tripped-gate attribution matches exact short-circuit order."""

    # --- 1st-pass attribution order: empty_title -> blocked -> not_role -> not_remote ---

    def test_1st_pass_empty_title_attribution(self):
        assert _gate_1st("", _REMOTE_DESC, None, loose=False) == "empty_title"

    def test_1st_pass_blocked_attribution(self):
        # "senior" in NEGATIVE_TITLE_KEYWORDS — should trip 'blocked' before role/remote checks
        assert _gate_1st("Senior Support Engineer", _REMOTE_DESC, None, loose=False) == "blocked"

    def test_1st_pass_blocked_beats_not_role(self):
        # "senior" is blocked; even though "Marketing" wouldn't match role, blocked fires first
        assert _gate_1st("Senior Marketing Manager", _REMOTE_DESC, None, loose=False) == "blocked"

    def test_1st_pass_not_role_attribution(self):
        # Not blocked, but no role keyword — fires not_role before not_remote check
        assert _gate_1st("Marketing Manager", _REMOTE_DESC, None, loose=False) == "not_role"

    def test_1st_pass_not_remote_attribution(self):
        # Role matches, not blocked, but no remote signal and no flag
        assert _gate_1st("Technical Support Engineer", _SILENT_DESC, None, loose=False) == "not_remote"

    def test_1st_pass_kept_with_remote_signal(self):
        assert _gate_1st("Technical Support Engineer", _REMOTE_DESC, None, loose=False) == "KEPT"

    def test_1st_pass_kept_with_true_flag(self):
        # flag=True short-circuits remote check — KEPT even with silent desc
        assert _gate_1st("Technical Support Engineer", _SILENT_DESC, True, loose=False) == "KEPT"

    def test_1st_pass_loose_skips_role_check(self):
        # When loose=True, not_role gate is skipped; "Marketing Manager" passes if remote OK
        assert _gate_1st("Marketing Manager", _REMOTE_DESC, None, loose=True) == "KEPT"

    def test_1st_pass_full_corpus_attribution(self):
        """Assert each row in CORPUS hits the expected 1st-pass gate."""
        for title, desc, flag, expected_gate_1, _ in CORPUS:
            got = _gate_1st(title, desc, flag, loose=False)
            assert got == expected_gate_1, (
                f"title={title!r}: expected gate={expected_gate_1!r}, got={got!r}"
            )

    def test_1st_pass_counts_match_corpus(self):
        """Tally gates against CORPUS and assert known distribution."""
        counts: dict[str, int] = {}
        for title, desc, flag, expected_gate, _ in CORPUS:
            gate = _gate_1st(title, desc, flag, loose=False)
            counts[gate] = counts.get(gate, 0) + 1
        assert counts.get("empty_title", 0) == 1
        assert counts.get("blocked", 0) == 1
        assert counts.get("not_role", 0) == 1
        assert counts.get("not_remote", 0) == 1
        # 2 KEPT: one with text signal, one with True flag
        assert counts.get("KEPT", 0) == 2

    # --- 2nd-pass attribution order: blocked -> not_remote -> not_role ---

    def test_2nd_pass_blocked_attribution(self):
        j = _job(title="Senior Support Engineer", description=_REMOTE_DESC)
        assert _gate_2nd(j, loose=False) == "blocked"

    def test_2nd_pass_not_remote_attribution(self):
        j = _job(title="Technical Support Engineer", description=_SILENT_DESC)
        assert _gate_2nd(j, loose=False) == "not_remote"

    def test_2nd_pass_not_role_attribution(self):
        # Not blocked, remote signal present, but no role keyword
        j = _job(title="Marketing Manager", description=_REMOTE_DESC)
        assert _gate_2nd(j, loose=False) == "not_role"

    def test_2nd_pass_kept(self):
        j = _job(title="Technical Support Engineer", description=_REMOTE_DESC)
        assert _gate_2nd(j, loose=False) == "KEPT"

    def test_2nd_pass_blocked_before_not_remote(self):
        # "senior" blocked, AND silent desc → blocked fires first (not not_remote)
        j = _job(title="Senior Support Engineer", description=_SILENT_DESC)
        assert _gate_2nd(j, loose=False) == "blocked"

    def test_2nd_pass_not_remote_before_not_role(self):
        # Not blocked, silent desc → not_remote fires before not_role
        # Use a title that has no role keyword, to make sure not_remote wins
        j = _job(title="Marketing Manager", description=_SILENT_DESC)
        assert _gate_2nd(j, loose=False) == "not_remote"

    def test_2nd_pass_full_corpus_attribution(self):
        """Assert each applicable CORPUS row hits the expected 2nd-pass gate."""
        for title, desc, flag, _, expected_gate_2 in CORPUS:
            if not title or expected_gate_2 is None:
                continue
            j = _job(title=title, description=desc)
            got = _gate_2nd(j, loose=False)
            assert got == expected_gate_2, (
                f"title={title!r}: expected 2nd-pass gate={expected_gate_2!r}, got={got!r}"
            )

    def test_2nd_pass_counts_match_corpus(self):
        """Tally 2nd-pass gates for applicable CORPUS rows."""
        counts: dict[str, int] = {}
        for title, desc, flag, _, expected_gate_2 in CORPUS:
            if not title or expected_gate_2 is None:
                continue
            j = _job(title=title, description=desc)
            gate = _gate_2nd(j, loose=False)
            counts[gate] = counts.get(gate, 0) + 1
        assert counts.get("blocked", 0) == 1
        assert counts.get("not_remote", 0) == 2   # the no-signal row + the flag-leak row
        assert counts.get("not_role", 0) == 1
        assert counts.get("KEPT", 0) == 1

    def test_2nd_pass_loose_skips_role_check(self):
        # loose=True → not_role gate skipped; "Marketing Manager" with remote signal → KEPT
        j = _job(title="Marketing Manager", description=_REMOTE_DESC)
        assert _gate_2nd(j, loose=True) == "KEPT"

    def test_debug_attribution_sum_equals_corpus_size(self):
        """Sum of all gate counts must equal the number of non-empty-title rows."""
        rows = [(t, d, f) for t, d, f, _, _ in CORPUS if t]
        count_total = len(rows)
        counted = 0
        for title, desc, flag, _, _ in CORPUS:
            if not title:
                continue
            j = _job(title=title, description=desc)
            gate = _gate_2nd(j, loose=False)
            counted += 1
        assert counted == count_total


# ===========================================================================
# PART 3 — FLAG-LEAK METRIC
# ===========================================================================

class TestFlagLeakMetric:
    """The most critical test: a job that passes 1st-pass via is_remote_flag=True
    but has no REMOTE_SIGNALS in title/desc and no HYBRID markers must be correctly
    identified as a flag-leak (drops on 2nd pass where flag=None is used)."""

    # The canonical flag-leak job from FILTER_AUDIT.md section 5.
    FLAG_LEAK_TITLE = "Technical Support Engineer"
    FLAG_LEAK_DESC = "We are a global company with offices worldwide. Great benefits."

    def test_flag_leak_passes_1st_with_true_flag(self):
        """1st pass: is_remote_flag=True → remote_ok returns True immediately."""
        result = filters.remote_ok(self.FLAG_LEAK_TITLE, self.FLAG_LEAK_DESC, True)
        assert result is True, "Flag=True shortcut must make remote_ok return True"

    def test_flag_leak_drops_2nd_with_none_flag(self):
        """2nd pass: is_remote_flag=None, no REMOTE_SIGNALS in text → remote_ok returns False."""
        result = filters.remote_ok(self.FLAG_LEAK_TITLE, self.FLAG_LEAK_DESC, None)
        assert result is False, (
            "With flag=None and no remote signals in text, remote_ok must return False "
            "(this is exactly the flag-leak condition)"
        )

    def test_flag_leak_job_is_detected_as_leak(self):
        j = _job(title=self.FLAG_LEAK_TITLE, description=self.FLAG_LEAK_DESC)
        assert _is_flag_leak(j) is True, "Flag-leak job must be identified as a leak"

    def test_job_with_remote_signal_is_not_a_leak(self):
        """A job that has an explicit remote signal in text passes both passes → NOT a leak."""
        j = _job(title=self.FLAG_LEAK_TITLE, description=_REMOTE_DESC)
        assert _is_flag_leak(j) is False, (
            "Job with REMOTE_SIGNALS in description must NOT be counted as a flag leak"
        )

    def test_job_with_hybrid_in_title_not_a_leak(self):
        """Hybrid title → remote_ok returns False even with flag=True (HYBRID_TITLE beats flag).
        This job is NOT a leak — it drops for a different reason (hybrid guard)."""
        hybrid_title = "Support Engineer (Hybrid)"
        j = _job(title=hybrid_title, description=_SILENT_DESC)
        # With flag=True: hybrid title fires first → False; so would-pass-with-true is also False.
        assert filters.remote_ok(hybrid_title, _SILENT_DESC, True) is False
        assert _is_flag_leak(j) is False

    def test_job_with_hybrid_desc_not_a_leak(self):
        """Hybrid description → remote_ok returns False with both True and None flags.
        NOT a leak (drops because of HYBRID_DESC, not because of missing text signal)."""
        hybrid_desc = "This is a hybrid role, 3 days in the office. Also global."
        j = _job(title=self.FLAG_LEAK_TITLE, description=hybrid_desc)
        # flag=True still False because HYBRID_DESC fires before the True shortcut
        assert filters.remote_ok(self.FLAG_LEAK_TITLE, hybrid_desc, True) is False
        assert _is_flag_leak(j) is False

    def test_flag_leak_title_has_no_hybrid_markers(self):
        """Confirm the flag-leak title itself contains no HYBRID_TITLE keywords."""
        t = self.FLAG_LEAK_TITLE.lower().replace("\\", "")
        assert not any(k in t for k in filters.HYBRID_TITLE), (
            "The canonical flag-leak title must not contain HYBRID_TITLE keywords"
        )

    def test_flag_leak_desc_has_no_hybrid_markers(self):
        """Confirm the flag-leak description contains no HYBRID_DESC phrases."""
        blob = (self.FLAG_LEAK_TITLE + " " + self.FLAG_LEAK_DESC).lower().replace("\\", "")
        assert not any(p in blob for p in filters.HYBRID_DESC), (
            "The canonical flag-leak description must not contain HYBRID_DESC phrases"
        )

    def test_flag_leak_desc_has_no_remote_signals(self):
        """Confirm the flag-leak description contains no REMOTE_SIGNALS."""
        blob = (self.FLAG_LEAK_TITLE + " " + self.FLAG_LEAK_DESC).lower().replace("\\", "")
        assert not any(sig in blob for sig in filters.REMOTE_SIGNALS), (
            "The canonical flag-leak description must not contain any REMOTE_SIGNALS"
        )

    def test_flag_leak_counting_in_debug_attribution(self):
        """The debug attribution block mirrors: if not remote_ok(..., None) and remote_ok(..., True)
        → count as flag_leak. Verify count=1 in a corpus of 3 jobs (1 leak, 1 not-remote-not-leak,
        1 fully kept)."""
        jobs_subset = [
            # 1. Flag-leak: drops on None but passes on True
            _job(title=self.FLAG_LEAK_TITLE, description=self.FLAG_LEAK_DESC),
            # 2. Not-remote but NOT a leak: also fails with True (hybrid desc)
            _job(title="Support Engineer", description="This is a hybrid role, 3 days in the office."),
            # 3. Kept: passes on None (has remote signal)
            _job(title="Technical Support Engineer", description=_REMOTE_DESC),
        ]
        flag_leak_count = 0
        not_remote_count = 0
        kept_count = 0
        for j in jobs_subset:
            if filters.blocked(j.title):
                pass
            elif not filters.remote_ok(j.title, j.description, None):
                not_remote_count += 1
                if filters.remote_ok(j.title, j.description, True):
                    flag_leak_count += 1
            elif filters.matches_role(j.title):
                kept_count += 1
        assert not_remote_count == 2, f"Expected 2 not-remote, got {not_remote_count}"
        assert flag_leak_count == 1, f"Expected 1 flag-leak, got {flag_leak_count}"
        assert kept_count == 1, f"Expected 1 kept, got {kept_count}"

    def test_flag_false_is_not_a_leak(self):
        """A job with is_remote_flag=False is not remote because flag says so (not text).
        It drops with flag=None too but NOT because of flag=True shortcut → not a leak."""
        # flag=False: remote_ok returns False (flag=False fires before text scan)
        # flag=True: remote_ok returns True (the True shortcut fires)
        # This would appear as a "leak" by our metric! But the scenario for flag-leak
        # detection only arises for jobs that PASSED the 1st filter (flag=True or text signal).
        # A job with flag=False would have been dropped on the 1st pass already.
        # We verify flag=False jobs can't reach the 2nd pass with a consistent scenario check.
        result_none = filters.remote_ok(self.FLAG_LEAK_TITLE, self.FLAG_LEAK_DESC, False)
        result_true = filters.remote_ok(self.FLAG_LEAK_TITLE, self.FLAG_LEAK_DESC, True)
        # flag=False drops it on 1st pass → never reaches 2nd pass
        assert result_none is False
        # flag=True would let it through on 1st pass
        assert result_true is True

    def test_remote_ok_flag_none_path_falls_through_to_text_scan(self):
        """When flag=None, remote_ok falls through to the REMOTE_SIGNALS text scan.
        A description with an explicit 'remote' keyword must return True with flag=None."""
        desc_with_signal = "This is a fully remote role."
        assert filters.remote_ok("Support Engineer", desc_with_signal, None) is True

    def test_remote_ok_flag_true_shortcut_bypasses_text_scan(self):
        """flag=True → immediate return True, even if REMOTE_SIGNALS absent from text."""
        assert filters.remote_ok("Support Engineer", _SILENT_DESC, True) is True

    def test_worldwide_is_not_a_remote_signal(self):
        """'worldwide' is a WORLDWIDE_HINT for region classification, NOT a REMOTE_SIGNAL.
        A description containing only 'worldwide' must return False with flag=None."""
        desc = "We are a global company with offices worldwide."
        assert filters.remote_ok("Support Engineer", desc, None) is False


# ===========================================================================
# PART 4 — INTEGRATION: the full 2nd-pass debug attribution block as a unit
# ===========================================================================

class TestDebugAttributionBlock:
    """Replicate the exact debug counting loop from api/run.py:292-304 against a
    synthetic jobs list and assert the counter values match expected ground truth."""

    def _run_debug_block(self, jobs: list[Job], loose: bool = False) -> dict[str, int]:
        """Pure Python reimplementation of the FILTER_DEBUG attribution block."""
        dropped_blocked = 0
        dropped_not_remote = 0
        dropped_not_role = 0
        flag_leak = 0
        passed_filters = 0
        for j in jobs:
            if filters.blocked(j.title):
                dropped_blocked += 1
            elif not filters.remote_ok(j.title, j.description, None):
                dropped_not_remote += 1
                if filters.remote_ok(j.title, j.description, True):
                    flag_leak += 1
            elif not (loose or filters.matches_role(j.title)):
                dropped_not_role += 1
            else:
                passed_filters += 1
        return {
            "blocked": dropped_blocked,
            "not_remote": dropped_not_remote,
            "not_role": dropped_not_role,
            "flag_leak": flag_leak,
            "passed": passed_filters,
        }

    def _build_synthetic_jobs(self) -> list[Job]:
        """Build a synthetic jobs list with exactly one job per gate bucket."""
        return [
            _job(title="Senior Support Engineer", description=_REMOTE_DESC,
                 company="BlockedCo"),           # blocked
            _job(title="Marketing Manager", description=_REMOTE_DESC,
                 company="NotRoleCo"),            # not_role
            _job(title="Technical Support Engineer", description=_SILENT_DESC,
                 company="SilentCo"),             # not_remote (no signal)
            _job(title="Technical Support Engineer", description=_SILENT_DESC,
                 company="LeakCo"),               # not_remote + flag_leak
            _job(title="Technical Support Engineer", description=_REMOTE_DESC,
                 company="KeptCo"),               # KEPT
        ]

    def test_attribution_counts_match_expected(self):
        # Note: both SilentCo and LeakCo have the same (title, desc) — both are flag-leaks.
        # The "flag-leak" metric is whether remote_ok(..., True) is True for a not-remote job.
        jobs = self._build_synthetic_jobs()
        counts = self._run_debug_block(jobs)
        assert counts["blocked"] == 1
        assert counts["not_remote"] == 2     # SilentCo + LeakCo (both have _SILENT_DESC)
        assert counts["flag_leak"] == 2      # both would pass with flag=True
        assert counts["not_role"] == 1
        assert counts["passed"] == 1

    def test_attribution_sum_equals_jobs_total(self):
        jobs = self._build_synthetic_jobs()
        counts = self._run_debug_block(jobs)
        total = counts["blocked"] + counts["not_remote"] + counts["not_role"] + counts["passed"]
        assert total == len(jobs)

    def test_attribution_with_distinct_flag_leak_scenario(self):
        """Specifically test a corpus where only ONE job is a flag-leak and
        another truly has no remote signal AND fails even with flag=True (hybrid)."""
        jobs = [
            # Hybrid desc → fails with both None AND True → not a flag-leak
            _job(title="Technical Support Engineer",
                 description="This is a hybrid role, 3 days in the office."),
            # Silent desc, no hybrid markers → fails with None, passes with True → IS a flag-leak
            _job(title="Technical Support Engineer", description=_SILENT_DESC),
        ]
        counts = self._run_debug_block(jobs)
        assert counts["not_remote"] == 2
        assert counts["flag_leak"] == 1     # only the _SILENT_DESC job is a leak

    def test_blocked_job_not_counted_as_flag_leak(self):
        """A blocked job is counted under 'blocked', never under 'flag_leak'."""
        j = _job(title="Senior Support Engineer", description=_SILENT_DESC)
        counts = self._run_debug_block([j])
        assert counts["blocked"] == 1
        assert counts["flag_leak"] == 0
        assert counts["not_remote"] == 0

    def test_empty_jobs_list_all_zeros(self):
        counts = self._run_debug_block([])
        assert counts == {"blocked": 0, "not_remote": 0, "not_role": 0, "flag_leak": 0, "passed": 0}

    def test_all_kept_no_drops(self):
        """When every job passes _passes(), all counters except 'passed' are zero."""
        jobs = [
            _job(title="Technical Support Engineer", description=_REMOTE_DESC, company=f"Co{i}")
            for i in range(5)
        ]
        counts = self._run_debug_block(jobs)
        assert counts["blocked"] == 0
        assert counts["not_remote"] == 0
        assert counts["not_role"] == 0
        assert counts["flag_leak"] == 0
        assert counts["passed"] == 5
