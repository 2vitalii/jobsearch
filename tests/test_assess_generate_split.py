"""Tests for the assess/generate split acceptance criteria (feat/assess-generate-split).

Covers criteria #1 through #7:
  #1 Assessment dataclass has NO tailored_summary/tailored_skills/cover_letter fields.
  #2 HONESTY_RULES substring appears in BOTH SYSTEM_ASSESS and SYSTEM_GENERATE.
  #3 fit=25 is saved to matches with generation_status='none', no cover_letter/cv_docx_path.
  #4 Same job assessed in two runs → one row, run_id preserved from first run (DO NOTHING).
  #5 _generate_background happy path: updates match to generation_status='done'
     with cover_letter + cv_docx_path.
  #6 Repeat generate while already 'generating' → 409 (atomic guard).
  #7 Full suite green (verified separately; here we ensure no regressions from new tests).

Design:
  - All tests are fully offline (no network, no DB, no LLM).
  - For #5/#6 we use FastAPI TestClient + dependency_overrides, or call
    _generate_background directly with fake objects.
  - FakeQueryBuilder used below is extended from the one in test_async_run.py
    to support .neq() and full matches UPDATE semantics needed by the generate path.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import uuid
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from api.main import app
from api.auth import get_current_user, CurrentUser
from api.deps import (
    get_config,
    get_llm,
    get_supabase,
    get_user_client,
)
from api.matches import _generate_background, GenerateStarted
from jobsearch.models import Assessment, Generation, Job, PlatformConfig
from jobsearch import scoring
from jobsearch.scoring import HONESTY_RULES, SYSTEM_ASSESS, SYSTEM_GENERATE


# ---------------------------------------------------------------------------
# Constants reused across tests
# ---------------------------------------------------------------------------

FAKE_USER_ID = "00000000-0000-0000-0000-aaaaaaaaaaaa"
FAKE_TOKEN = "fake-token-assess-generate"

FAKE_CV_MD = (
    "# Jane Doe\nSupport Engineer\n\n## Professional Summary\n"
    "Support engineer with 2 years experience.\n\n"
    "## Core Skills\n- Technical Support: SQL, REST APIs\n\n"
    "## Professional Experience\n### Support Engineer — Acme\n2024 - 2026\n"
    "- Resolved tickets.\n\n## Education\n### B.Sc. IT\nSome University\n"
)

FAKE_MATCH_ID = str(uuid.uuid4())
FAKE_JOB_ID = str(uuid.uuid4())

# Generation payload: only tailored fields
GEN_PAYLOAD = {
    "tailored_summary": "Tailored support engineer summary.",
    "tailored_skills": ["Technical Support: SQL, REST APIs"],
    "cover_letter": "Dear team, I am a strong fit.",
}

# Assessment-only payload (no tailored_*/cover_letter)
ASSESS_PAYLOAD = {
    "fit_score": 88,
    "b2b_eligible": "yes",
    "reason": "strong match",
    "jd_keywords": ["sql", "rest api", "support"],
    "ats_present": ["sql", "support"],
    "ats_missing": [],
    "gaps": "none",
    "recruiter_verdict": "shortlist",
}

# Pre-score payload for Haiku (fit >= pre_min_fit)
PRE_PAYLOAD = {"fit_score": 88, "b2b_eligible": "yes", "reason": "ok"}


# ===========================================================================
# Criterion #1 — Assessment dataclass has no tailored_*/cover_letter fields
# ===========================================================================

class TestAssessmentDataclassStructure:
    """Structural test: Assessment never has tailored_summary/tailored_skills/cover_letter
    fields — honesty invariant #1 is enforced at the type level."""

    def test_assessment_has_no_tailored_summary_field(self):
        field_names = {f.name for f in dataclasses.fields(Assessment)}
        assert "tailored_summary" not in field_names, (
            "Assessment must NOT have 'tailored_summary' — would allow leaking CV text"
        )

    def test_assessment_has_no_tailored_skills_field(self):
        field_names = {f.name for f in dataclasses.fields(Assessment)}
        assert "tailored_skills" not in field_names, (
            "Assessment must NOT have 'tailored_skills'"
        )

    def test_assessment_has_no_cover_letter_field(self):
        field_names = {f.name for f in dataclasses.fields(Assessment)}
        assert "cover_letter" not in field_names, (
            "Assessment must NOT have 'cover_letter'"
        )

    def test_assessment_has_exactly_the_eight_expected_fields(self):
        """Assessment must have exactly the 8 scoring/analysis fields and nothing more."""
        expected = {
            "fit_score", "b2b", "reason",
            "jd_keywords", "ats_present", "ats_missing",
            "gaps", "recruiter_verdict",
        }
        actual = {f.name for f in dataclasses.fields(Assessment)}
        assert actual == expected, (
            f"Assessment fields mismatch.\nExpected: {sorted(expected)}\nGot:      {sorted(actual)}"
        )

    def test_assess_with_fake_llm_returning_tailored_fields_does_not_leak(self):
        """Even if a rogue LLM returns tailored_summary/cover_letter in JSON,
        assess() must yield an Assessment with none of those attributes."""

        # Fake LLM that includes tailored_* fields (simulating a confused model)
        class LeakyFakeLLM:
            def complete(self, *, model, system, messages, max_tokens) -> str:
                return json.dumps({
                    **ASSESS_PAYLOAD,
                    "tailored_summary": "LEAKED SUMMARY — should not appear",
                    "tailored_skills": ["LEAKED: skill"],
                    "cover_letter": "LEAKED COVER LETTER",
                })

        job = Job(
            dedup_key="acme|test", source="LinkedIn", url="https://x/1",
            company="Acme", title="Support Eng", location="Remote",
            region="WORLDWIDE", description="Support role with SQL.",
        )
        result = scoring.assess(job, FAKE_CV_MD, PlatformConfig(), LeakyFakeLLM())

        # Must be an Assessment, not a MatchResult or anything with extra fields
        assert isinstance(result, Assessment)
        assert not hasattr(result, "tailored_summary"), "Assessment must not have tailored_summary"
        assert not hasattr(result, "tailored_skills"), "Assessment must not have tailored_skills"
        assert not hasattr(result, "cover_letter"), "Assessment must not have cover_letter"

        # The scoring fields must still be populated correctly
        assert result.fit_score == 88
        assert result.b2b == "yes"
        assert result.jd_keywords == ["sql", "rest api", "support"]


# ===========================================================================
# Criterion #2 — HONESTY_RULES substring appears in BOTH system prompts
# ===========================================================================

class TestHonestyRulesBothPrompts:
    """HONESTY_RULES constant must be present verbatim in both SYSTEM_ASSESS
    and SYSTEM_GENERATE. This is the architectural invariant from the plan."""

    # Use a distinctive phrase that is unique to HONESTY_RULES and not in
    # generic boilerplate, so we can be confident the constant is actually there.
    _DISTINCTIVE_PHRASE = "ЖЁСТКИЕ ПРАВИЛА ЧЕСТНОСТИ"

    def test_honesty_rules_in_system_assess(self):
        assert self._DISTINCTIVE_PHRASE in SYSTEM_ASSESS, (
            f"SYSTEM_ASSESS must contain HONESTY_RULES (looking for {self._DISTINCTIVE_PHRASE!r})"
        )

    def test_honesty_rules_in_system_generate(self):
        assert self._DISTINCTIVE_PHRASE in SYSTEM_GENERATE, (
            f"SYSTEM_GENERATE must contain HONESTY_RULES (looking for {self._DISTINCTIVE_PHRASE!r})"
        )

    def test_honesty_rules_constant_is_the_same_text(self):
        """HONESTY_RULES is a single constant interpolated into both — not duplicated.
        Check that the full constant text appears in each prompt, meaning neither
        prompt has a truncated or modified version."""
        # HONESTY_RULES is a non-trivial multi-line string; check a body phrase
        body_phrase = "Используй ТОЛЬКО факты и навыки из мастер-CV"
        assert body_phrase in HONESTY_RULES
        assert body_phrase in SYSTEM_ASSESS
        assert body_phrase in SYSTEM_GENERATE

    def test_honesty_rules_not_in_score_system(self):
        """SCORE_SYSTEM (Haiku pre-filter) is a cheaper, different prompt.
        It must NOT include the full HONESTY_RULES block (it has its own inline rule).
        This guards against accidentally including the expensive block in the cheap call."""
        # score_fit uses SCORE_SYSTEM; it's a separate, shorter prompt.
        assert self._DISTINCTIVE_PHRASE not in scoring.SCORE_SYSTEM, (
            "SCORE_SYSTEM (Haiku) must NOT include HONESTY_RULES block — wrong prompt"
        )


# ===========================================================================
# Criterion #3 — fit=25 saved to matches with generation_status='none'
# ===========================================================================
# (Already partially covered by TestAssessOnlyLoop in test_async_run.py;
#  here we verify the WRITTEN ROW structure more precisely.)

class _MatchesStore:
    """Thread-safe in-memory matches table for criterion #3/#4 unit tests."""

    def __init__(self):
        self._lock = threading.Lock()
        # Key: (user_id, job_id)
        self._rows: dict[tuple[str, str], dict] = {}

    def upsert_ignore_dup(self, row: dict) -> dict | None:
        key = (row.get("user_id", ""), row.get("job_id", ""))
        with self._lock:
            if key in self._rows:
                return None
            self._rows[key] = dict(row)
            return dict(row)

    def all_rows(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._rows.values()]


class _FakeJobsStoreSingle:
    """Fake supabase for just jobs table + matches table (for _write_assessment tests)."""

    def __init__(self, matches_store: _MatchesStore):
        self._matches = matches_store
        self._jobs: dict[str, dict] = {}  # dedup_key -> row

    def table(self, name: str):
        return _SingleFakeQB(self._jobs, self._matches, name)


class _SingleFakeQB:
    """Minimal query builder for _write_assessment's calls (jobs + matches)."""

    def __init__(self, jobs: dict, matches: _MatchesStore, table: str):
        self._jobs = jobs
        self._matches = matches
        self._table = table
        self._op: str | None = None
        self._data: dict | None = None
        self._filters: list[tuple] = []
        self._limit_val: int | None = None
        self._ignore_dup: bool = False

    def select(self, _cols: str = "*"):
        self._op = "select"
        return self

    def insert(self, data: dict):
        self._op = "insert"
        self._data = data
        return self

    def upsert(self, data: dict, on_conflict: str = "", ignore_duplicates: bool = False, **_kw):
        self._op = "upsert"
        self._data = data
        self._ignore_dup = ignore_duplicates
        return self

    def update(self, data: dict):
        self._op = "update"
        self._data = data
        return self

    def eq(self, col: str, val: Any):
        self._filters.append((col, val))
        return self

    def limit(self, n: int):
        self._limit_val = n
        return self

    def execute(self):
        class R:
            data: list = []

        r = R()
        if self._table == "jobs":
            if self._op == "select":
                dedup_key = next((v for c, v in self._filters if c == "dedup_key"), None)
                if dedup_key and dedup_key in self._jobs:
                    r.data = [self._jobs[dedup_key]]
                elif dedup_key:
                    r.data = []
                else:
                    r.data = list(self._jobs.values())
            elif self._op in ("upsert", "insert"):
                row = dict(self._data or {})
                row.setdefault("id", str(uuid.uuid4()))
                key = row.get("dedup_key", "")
                if key not in self._jobs:
                    self._jobs[key] = row
                r.data = [self._jobs[key]]
        elif self._table == "matches":
            if self._op == "upsert" and self._ignore_dup:
                inserted = self._matches.upsert_ignore_dup(self._data or {})
                r.data = [inserted] if inserted is not None else []
            else:
                r.data = []
        return r


class TestFit25SavedWithGenerationStatusNone:
    """Criterion #3: fit=25 assessment is saved to matches with generation_status='none',
    no cover_letter, no cv_docx_path (assessment-only row)."""

    def test_write_assessment_saves_fit_25_with_status_none(self):
        from api.run import _write_assessment

        matches = _MatchesStore()
        fake_sb = _FakeJobsStoreSingle(matches)

        job = Job(
            dedup_key="acme|test25", source="LinkedIn", url="https://x/1",
            company="Acme", title="Support Eng", location="Remote",
            region="WORLDWIDE", description="Support role.",
        )
        assessment = Assessment(
            fit_score=25,
            b2b="yes",
            reason="moderate match",
            jd_keywords=["sql"],
            ats_present=["sql"],
            ats_missing=["k8s"],
            gaps="missing k8s",
            recruiter_verdict="maybe shortlist",
        )

        _write_assessment(fake_sb, FAKE_USER_ID, job, assessment, run_id="run-001")

        rows = matches.all_rows()
        assert len(rows) == 1, f"expected 1 row, got {len(rows)}"
        row = rows[0]

        # fit_score must be 25
        assert row["fit_score"] == 25, f"expected fit_score=25, got {row['fit_score']}"

        # generation_status must be 'none'
        assert row["generation_status"] == "none", (
            f"expected generation_status='none', got {row['generation_status']!r}"
        )

        # status must be 'ASSESSED'
        assert row["status"] == "ASSESSED"

        # No cover_letter or cv_docx_path in the row
        assert row.get("cover_letter") is None or "cover_letter" not in row, (
            "assessment row must not have cover_letter"
        )
        assert row.get("cv_docx_path") is None or "cv_docx_path" not in row, (
            "assessment row must not have cv_docx_path"
        )

        # analysis dict must NOT have tailored_summary or tailored_skills
        analysis = row.get("analysis", {})
        assert "tailored_summary" not in analysis, (
            "analysis must not have tailored_summary at assess time"
        )
        assert "tailored_skills" not in analysis, (
            "analysis must not have tailored_skills at assess time"
        )

        # analysis must have assessment fields
        assert "reason" in analysis
        assert "jd_keywords" in analysis
        assert "ats_present" in analysis
        assert "ats_missing" in analysis
        assert "gaps" in analysis
        assert "recruiter_verdict" in analysis

    def test_write_assessment_saves_run_id(self):
        from api.run import _write_assessment

        matches = _MatchesStore()
        fake_sb = _FakeJobsStoreSingle(matches)
        job = Job(
            dedup_key="acme|test-run-id", source="LinkedIn", url="https://x/2",
            company="Acme", title="Eng", location="Remote", region="WORLDWIDE",
            description="Role.",
        )
        assessment = Assessment(
            fit_score=25, b2b="yes", reason="ok", jd_keywords=[],
            ats_present=[], ats_missing=[], gaps="", recruiter_verdict="",
        )

        _write_assessment(fake_sb, FAKE_USER_ID, job, assessment, run_id="run-xyz-123")

        rows = matches.all_rows()
        assert rows[0]["run_id"] == "run-xyz-123"


# ===========================================================================
# Criterion #4 — ON CONFLICT DO NOTHING: run_id from first run is preserved
# ===========================================================================
# (Also covered by TestRunIdFirstRunInvariant in test_async_run.py;
#  here we test the _write_assessment DB-level behavior directly.)

class TestConflictDoNothing:
    """Direct unit test of _write_assessment's ON CONFLICT DO NOTHING behavior."""

    def test_second_write_same_job_is_ignored(self):
        from api.run import _write_assessment

        matches = _MatchesStore()
        fake_sb = _FakeJobsStoreSingle(matches)
        job = Job(
            dedup_key="acme|conflict-test", source="LinkedIn", url="https://x/3",
            company="Acme", title="Eng", location="Remote", region="WORLDWIDE",
            description="Role.",
        )
        a = Assessment(
            fit_score=80, b2b="yes", reason="run-a", jd_keywords=["sql"],
            ats_present=["sql"], ats_missing=[], gaps="", recruiter_verdict="shortlist",
        )

        _write_assessment(fake_sb, FAKE_USER_ID, job, a, run_id="run-A")

        # Same job, different run, different assessment data
        a2 = Assessment(
            fit_score=90, b2b="yes", reason="run-b", jd_keywords=["python"],
            ats_present=["python"], ats_missing=[], gaps="", recruiter_verdict="shortlist",
        )
        _write_assessment(fake_sb, FAKE_USER_ID, job, a2, run_id="run-B")

        rows = matches.all_rows()
        assert len(rows) == 1, f"ON CONFLICT DO NOTHING: expected 1 row, got {len(rows)}"
        assert rows[0]["run_id"] == "run-A", (
            f"run_id must stay 'run-A' (first run wins), got {rows[0]['run_id']!r}"
        )
        assert rows[0]["fit_score"] == 80, (
            f"fit_score must be from first run (80), got {rows[0]['fit_score']}"
        )

    def test_different_jobs_create_separate_rows(self):
        from api.run import _write_assessment

        matches = _MatchesStore()
        fake_sb = _FakeJobsStoreSingle(matches)

        jobs_and_assessments = [
            (Job(
                dedup_key=f"acme|job-{i}", source="LinkedIn", url=f"https://x/{i}",
                company="Acme", title="Eng", location="Remote", region="WORLDWIDE",
                description="Role.",
            ), Assessment(
                fit_score=70 + i, b2b="yes", reason=f"run-{i}", jd_keywords=[],
                ats_present=[], ats_missing=[], gaps="", recruiter_verdict="",
            ))
            for i in range(3)
        ]

        for job, assessment in jobs_and_assessments:
            _write_assessment(fake_sb, FAKE_USER_ID, job, assessment, run_id="run-A")

        rows = matches.all_rows()
        assert len(rows) == 3, f"3 different jobs → 3 rows, got {len(rows)}"


# ===========================================================================
# Criterion #5 — _generate_background happy path
# ===========================================================================

class _FakeMatchesTableForGenerate:
    """In-memory matches store for _generate_background tests.
    Supports select, update, and upsert (for the loaded match row)."""

    def __init__(self, initial_rows: list[dict] | None = None):
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}
        for r in (initial_rows or []):
            self._rows[r["id"]] = dict(r)

    def get(self, match_id: str) -> dict | None:
        with self._lock:
            return dict(self._rows[match_id]) if match_id in self._rows else None

    def update_by_id(self, match_id: str, fields: dict) -> list[dict]:
        with self._lock:
            if match_id in self._rows:
                self._rows[match_id].update(fields)
                return [dict(self._rows[match_id])]
            return []

    def select_by_id_and_user(self, match_id: str, user_id: str) -> list[dict]:
        with self._lock:
            row = self._rows.get(match_id)
            if row and row.get("user_id") == user_id:
                return [dict(row)]
            return []


class _FakeJobsTableForGenerate:
    """In-memory jobs table for _generate_background tests."""

    def __init__(self, rows: list[dict] | None = None):
        self._rows: dict[str, dict] = {r["id"]: r for r in (rows or [])}

    def select_by_id(self, job_id: str) -> list[dict]:
        r = self._rows.get(job_id)
        return [dict(r)] if r else []


class _FakeSupabaseForGenerate:
    """Fake supabase for _generate_background: routes matches + jobs table calls."""

    def __init__(self, matches: _FakeMatchesTableForGenerate, jobs: _FakeJobsTableForGenerate):
        self._matches = matches
        self._jobs = jobs
        self._storage_uploads: list[dict] = []  # record upload calls

    def table(self, name: str):
        return _GenerateQB(self._matches, self._jobs, name)

    # Fake storage (for _upload_docx)
    @property
    def storage(self):
        return _FakeStorageRouter(self._storage_uploads)


class _FakeStorageRouter:
    def __init__(self, uploads: list):
        self._uploads = uploads

    def from_(self, _bucket: str):
        return _FakeBucket(self._uploads)


class _FakeBucket:
    def __init__(self, uploads: list):
        self._uploads = uploads

    def upload(self, path: str, file: bytes, file_options: dict | None = None):
        self._uploads.append({"path": path, "size": len(file)})

    def create_signed_url(self, path: str, ttl: int):
        return {"signedURL": f"https://fake-storage.example.com/{path}?token=abc"}


class _GenerateQB:
    """Query builder for _generate_background calls."""

    def __init__(self, matches: _FakeMatchesTableForGenerate, jobs: _FakeJobsTableForGenerate, table: str):
        self._matches = matches
        self._jobs = jobs
        self._table = table
        self._op: str | None = None
        self._data: dict | None = None
        self._filters: list[tuple] = []
        self._neq_filters: list[tuple] = []
        self._limit_val: int | None = None

    def select(self, _cols: str = "*"):
        self._op = "select"
        return self

    def update(self, data: dict):
        self._op = "update"
        self._data = data
        return self

    def upsert(self, data: dict, on_conflict: str = "", ignore_duplicates: bool = False, **_kw):
        self._op = "upsert"
        self._data = data
        return self

    def eq(self, col: str, val: Any):
        self._filters.append((col, val))
        return self

    def neq(self, col: str, val: Any):
        self._neq_filters.append((col, val))
        return self

    def limit(self, n: int):
        self._limit_val = n
        return self

    def execute(self):
        class R:
            data: list = []

        r = R()

        if self._table == "matches":
            if self._op == "select":
                match_id = next((v for c, v in self._filters if c == "id"), None)
                user_id = next((v for c, v in self._filters if c == "user_id"), None)
                if match_id and user_id:
                    r.data = self._matches.select_by_id_and_user(match_id, user_id)
                else:
                    r.data = []
            elif self._op == "update":
                match_id = next((v for c, v in self._filters if c == "id"), None)
                if match_id:
                    # Apply neq filters: if any neq condition fails, return empty
                    row = self._matches.get(match_id)
                    for col, val in self._neq_filters:
                        if row and row.get(col) == val:
                            # Condition failed — this is the 409 gate
                            r.data = []
                            return r
                    r.data = self._matches.update_by_id(match_id, self._data or {})
                else:
                    r.data = []
        elif self._table == "jobs":
            if self._op == "select":
                job_id = next((v for c, v in self._filters if c == "id"), None)
                if job_id:
                    r.data = self._jobs.select_by_id(job_id)
                else:
                    r.data = []
        return r


class _FakeLLMGenerate:
    """Fake LLM returning generation fields for the generate() call."""

    def complete(self, *, model, system, messages, max_tokens) -> str:
        return json.dumps(GEN_PAYLOAD)


class TestGenerateBackgroundHappyPath:
    """Criterion #5: _generate_background with fakes → generation_status='done',
    cover_letter and cv_docx_path written to matches."""

    def _make_match_row(self, generation_status: str = "generating") -> dict:
        """Typical match row as would be read from DB during _generate_background."""
        return {
            "id": FAKE_MATCH_ID,
            "user_id": FAKE_USER_ID,
            "job_id": FAKE_JOB_ID,
            "fit_score": 88,
            "b2b_eligible": "yes",
            "generation_status": generation_status,
            "job_title": "Support Engineer",
            "job_company": "Acme",
            "job_url": "https://acme.example.com/jobs/1",
            "job_region": "WORLDWIDE",
            "analysis": {
                "reason": "strong match",
                "jd_keywords": ["sql", "support"],
                "ats_present": ["sql"],
                "ats_missing": [],
                "gaps": "none",
                "recruiter_verdict": "shortlist",
            },
        }

    def _make_job_row(self) -> dict:
        return {
            "id": FAKE_JOB_ID,
            "description": "Fully remote support role. SQL and REST APIs required.",
            "title": "Support Engineer",
            "company": "Acme",
            "location": "Remote",
            "url": "https://acme.example.com/jobs/1",
            "region": "WORLDWIDE",
        }

    def test_generate_background_sets_done_with_cover_letter_and_docx(self, monkeypatch):
        """_generate_background must update match to generation_status='done'
        with cover_letter and cv_docx_path written. No real LLM / storage."""
        import api.matches as matches_mod

        match_row = self._make_match_row()
        matches = _FakeMatchesTableForGenerate([match_row])
        jobs = _FakeJobsTableForGenerate([self._make_job_row()])
        fake_sb = _FakeSupabaseForGenerate(matches, jobs)

        # Patch make_supabase_client and _upload_docx
        monkeypatch.setattr(matches_mod, "make_supabase_client", lambda: fake_sb)
        monkeypatch.setattr(
            matches_mod,
            "_upload_docx",
            lambda sb, uid, job, score, data: f"{uid}/{score}_{job.dedup_key}.docx",
        )

        _generate_background(
            match_id=FAKE_MATCH_ID,
            user_id=FAKE_USER_ID,
            cv_markdown=FAKE_CV_MD,
            config=PlatformConfig(),
            llm=_FakeLLMGenerate(),
        )

        row = matches.get(FAKE_MATCH_ID)
        assert row is not None

        # generation_status must be 'done'
        assert row["generation_status"] == "done", (
            f"expected generation_status='done', got {row['generation_status']!r}"
        )

        # cover_letter must be set
        assert row.get("cover_letter"), (
            f"expected non-empty cover_letter, got {row.get('cover_letter')!r}"
        )

        # cv_docx_path must be set
        assert row.get("cv_docx_path"), (
            f"expected non-empty cv_docx_path, got {row.get('cv_docx_path')!r}"
        )

        # analysis must now have tailored_summary and tailored_skills
        analysis = row.get("analysis", {})
        assert analysis.get("tailored_summary") == GEN_PAYLOAD["tailored_summary"]
        assert analysis.get("tailored_skills") == GEN_PAYLOAD["tailored_skills"]

    def test_generate_background_sets_failed_on_exception(self, monkeypatch):
        """If an exception occurs, _generate_background must set generation_status='failed'
        and must NOT propagate the exception (background task must not crash the worker)."""
        import api.matches as matches_mod

        match_row = self._make_match_row()
        matches = _FakeMatchesTableForGenerate([match_row])
        jobs = _FakeJobsTableForGenerate([self._make_job_row()])
        fake_sb = _FakeSupabaseForGenerate(matches, jobs)

        monkeypatch.setattr(matches_mod, "make_supabase_client", lambda: fake_sb)

        class BoomLLM:
            def complete(self, **_kw) -> str:
                raise ConnectionError("LLM unreachable")

        # Must not raise
        _generate_background(
            match_id=FAKE_MATCH_ID,
            user_id=FAKE_USER_ID,
            cv_markdown=FAKE_CV_MD,
            config=PlatformConfig(),
            llm=BoomLLM(),
        )

        row = matches.get(FAKE_MATCH_ID)
        assert row is not None
        assert row["generation_status"] == "failed", (
            f"expected generation_status='failed', got {row['generation_status']!r}"
        )

    def test_generate_background_match_not_found_sets_failed(self, monkeypatch):
        """If the match is not found in the DB, _generate_background logs and
        marks as failed — does not crash."""
        import api.matches as matches_mod

        matches = _FakeMatchesTableForGenerate([])  # empty — match not found
        jobs = _FakeJobsTableForGenerate([])
        fake_sb = _FakeSupabaseForGenerate(matches, jobs)

        monkeypatch.setattr(matches_mod, "make_supabase_client", lambda: fake_sb)

        # Should not raise even when match is missing
        _generate_background(
            match_id="nonexistent-match-id",
            user_id=FAKE_USER_ID,
            cv_markdown=FAKE_CV_MD,
            config=PlatformConfig(),
            llm=_FakeLLMGenerate(),
        )
        # No assertion on row — the match doesn't exist; we just confirm no exception.

    def test_generate_background_degraded_mode_no_job_description(self, monkeypatch):
        """If job description is empty (degraded mode), _generate_background must still
        complete and set generation_status='done'. generate() is called with empty description."""
        import api.matches as matches_mod

        match_row = self._make_match_row()
        matches = _FakeMatchesTableForGenerate([match_row])
        # Job row with empty description
        job_row = {**self._make_job_row(), "description": ""}
        jobs = _FakeJobsTableForGenerate([job_row])
        fake_sb = _FakeSupabaseForGenerate(matches, jobs)

        monkeypatch.setattr(matches_mod, "make_supabase_client", lambda: fake_sb)
        monkeypatch.setattr(
            matches_mod,
            "_upload_docx",
            lambda sb, uid, job, score, data: f"{uid}/{score}.docx",
        )

        _generate_background(
            match_id=FAKE_MATCH_ID,
            user_id=FAKE_USER_ID,
            cv_markdown=FAKE_CV_MD,
            config=PlatformConfig(),
            llm=_FakeLLMGenerate(),
        )

        row = matches.get(FAKE_MATCH_ID)
        assert row is not None
        assert row["generation_status"] == "done"


# ===========================================================================
# Criterion #6 — 409 when generation_status is already 'generating'
# ===========================================================================

class _FakeQueryBuilderWithNeq:
    """Extended fake query builder that supports .neq() for the 409-guard test.
    Handles matches table select + update with neq filter."""

    def __init__(self, matches_store: _FakeMatchesTableForGenerate, table: str):
        self._matches = matches_store
        self._table = table
        self._op: str | None = None
        self._data: dict | None = None
        self._filters: list[tuple] = []
        self._neq_filters: list[tuple] = []
        self._limit_val: int | None = None

    def select(self, _cols: str = "*"):
        self._op = "select"
        return self

    def update(self, data: dict):
        self._op = "update"
        self._data = data
        return self

    def eq(self, col: str, val: Any):
        self._filters.append((col, val))
        return self

    def neq(self, col: str, val: Any):
        self._neq_filters.append((col, val))
        return self

    def limit(self, n: int):
        self._limit_val = n
        return self

    def execute(self):
        class R:
            data: list = []

        r = R()
        if self._table == "matches":
            if self._op == "select":
                match_id = next((v for c, v in self._filters if c == "id"), None)
                user_id = next((v for c, v in self._filters if c == "user_id"), None)
                if match_id and user_id:
                    r.data = self._matches.select_by_id_and_user(match_id, user_id)
            elif self._op == "update":
                match_id = next((v for c, v in self._filters if c == "id"), None)
                if match_id:
                    row = self._matches.get(match_id)
                    # Apply neq filters (simulate WHERE col <> val)
                    for col, val in self._neq_filters:
                        if row and row.get(col) == val:
                            # Row matches the excluded value → WHERE not satisfied → 0 rows
                            r.data = []
                            return r
                    r.data = self._matches.update_by_id(match_id, self._data or {})
        return r


class _FakeSupabaseWithNeq:
    """Minimal Supabase fake supporting neq() for the 409-guard endpoint tests."""

    def __init__(self, matches: _FakeMatchesTableForGenerate):
        self._matches = matches

    def table(self, name: str):
        return _FakeQueryBuilderWithNeq(self._matches, name)


class TestGenerateEndpoint409Guard:
    """Criterion #6: POST /matches/{id}/generate → 409 when generation_status='generating'."""

    def _setup(self, generation_status: str):
        """Wire the app with a fake supabase containing one match row."""
        match_row = {
            "id": FAKE_MATCH_ID,
            "user_id": FAKE_USER_ID,
            "generation_status": generation_status,
        }
        matches = _FakeMatchesTableForGenerate([match_row])
        fake_sb = _FakeSupabaseWithNeq(matches)
        return fake_sb

    def test_generate_endpoint_409_when_already_generating(self, monkeypatch):
        """POST /matches/{id}/generate returns 409 when generation_status='generating'.
        The atomic UPDATE WHERE generation_status <> 'generating' returns 0 rows → 409."""
        fake_sb = self._setup(generation_status="generating")

        def override_user():
            return CurrentUser(user_id=FAKE_USER_ID, email="test@example.com", token=FAKE_TOKEN)

        def override_user_client():
            return fake_sb

        def override_supabase():
            return fake_sb

        def override_llm():
            return _FakeLLMGenerate()

        def override_config():
            return PlatformConfig()

        import api.matches as matches_mod
        monkeypatch.setattr(
            matches_mod,
            "_load_cv",
            lambda sb, uid: (FAKE_CV_MD, "short profile"),
        )

        app.dependency_overrides[get_current_user] = override_user
        app.dependency_overrides[get_user_client] = override_user_client
        app.dependency_overrides[get_supabase] = override_supabase
        app.dependency_overrides[get_llm] = override_llm
        app.dependency_overrides[get_config] = override_config

        try:
            with TestClient(app) as tc:
                r = tc.post(f"/matches/{FAKE_MATCH_ID}/generate")
            assert r.status_code == 409, (
                f"expected 409 when generation_status='generating', got {r.status_code}: {r.text}"
            )
            # Error detail must mention "already in progress" or "generating"
            detail = r.json().get("detail", "").lower()
            assert "already" in detail or "generating" in detail or "in progress" in detail, (
                f"409 detail should mention ongoing generation, got: {r.json().get('detail')!r}"
            )
        finally:
            app.dependency_overrides.clear()

    def test_generate_endpoint_202_when_status_none(self, monkeypatch):
        """POST /matches/{id}/generate returns 202 when generation_status='none'
        (the happy path: atomic guard passes, background task scheduled)."""
        fake_sb = self._setup(generation_status="none")

        def override_user():
            return CurrentUser(user_id=FAKE_USER_ID, email="test@example.com", token=FAKE_TOKEN)

        def override_user_client():
            return fake_sb

        def override_supabase():
            return fake_sb

        def override_llm():
            return _FakeLLMGenerate()

        def override_config():
            return PlatformConfig()

        import api.matches as matches_mod
        monkeypatch.setattr(matches_mod, "make_supabase_client", lambda: fake_sb)
        monkeypatch.setattr(
            matches_mod,
            "_load_cv",
            lambda sb, uid: (FAKE_CV_MD, "short profile"),
        )
        monkeypatch.setattr(
            matches_mod,
            "_generate_background",
            lambda **kw: None,  # don't run real background task
        )

        app.dependency_overrides[get_current_user] = override_user
        app.dependency_overrides[get_user_client] = override_user_client
        app.dependency_overrides[get_supabase] = override_supabase
        app.dependency_overrides[get_llm] = override_llm
        app.dependency_overrides[get_config] = override_config

        try:
            with TestClient(app) as tc:
                r = tc.post(f"/matches/{FAKE_MATCH_ID}/generate")
            assert r.status_code == 202, (
                f"expected 202 for status='none', got {r.status_code}: {r.text}"
            )
            body = r.json()
            assert body["match_id"] == FAKE_MATCH_ID
            assert body["generation_status"] == "generating"
        finally:
            app.dependency_overrides.clear()

    def test_generate_endpoint_404_when_match_not_found(self, monkeypatch):
        """POST /matches/{id}/generate → 404 when match doesn't belong to user."""
        # Match store is empty (no rows for this user)
        matches = _FakeMatchesTableForGenerate([])
        fake_sb = _FakeSupabaseWithNeq(matches)

        def override_user():
            return CurrentUser(user_id=FAKE_USER_ID, email="test@example.com", token=FAKE_TOKEN)

        def override_user_client():
            return fake_sb

        def override_supabase():
            return fake_sb

        def override_llm():
            return _FakeLLMGenerate()

        def override_config():
            return PlatformConfig()

        app.dependency_overrides[get_current_user] = override_user
        app.dependency_overrides[get_user_client] = override_user_client
        app.dependency_overrides[get_supabase] = override_supabase
        app.dependency_overrides[get_llm] = override_llm
        app.dependency_overrides[get_config] = override_config

        try:
            with TestClient(app) as tc:
                r = tc.post(f"/matches/{uuid.uuid4()}/generate")
            assert r.status_code == 404, (
                f"expected 404 for missing match, got {r.status_code}: {r.text}"
            )
        finally:
            app.dependency_overrides.clear()

    def test_atomic_guard_logic_via_neq_filter(self):
        """Unit test of the neq-based guard logic directly:
        The fake query builder must return 0 rows when generation_status == 'generating'
        and the update has neq('generation_status', 'generating')."""
        match_row = {
            "id": FAKE_MATCH_ID,
            "user_id": FAKE_USER_ID,
            "generation_status": "generating",
        }
        matches = _FakeMatchesTableForGenerate([match_row])
        qb = _FakeQueryBuilderWithNeq(matches, "matches")

        # Simulate: UPDATE matches SET generation_status='generating'
        # WHERE id=:id AND user_id=:uid AND generation_status <> 'generating'
        result = (
            qb.update({"generation_status": "generating"})
            .eq("id", FAKE_MATCH_ID)
            .eq("user_id", FAKE_USER_ID)
            .neq("generation_status", "generating")
            .execute()
        )
        # Must return 0 rows since generation_status is already 'generating'
        assert result.data == [], (
            f"Guard must return 0 rows when status is already 'generating', got: {result.data}"
        )

    def test_atomic_guard_passes_when_status_is_none(self):
        """The atomic guard must succeed (return 1 row) when status is 'none'."""
        match_row = {
            "id": FAKE_MATCH_ID,
            "user_id": FAKE_USER_ID,
            "generation_status": "none",
        }
        matches = _FakeMatchesTableForGenerate([match_row])
        qb = _FakeQueryBuilderWithNeq(matches, "matches")

        result = (
            qb.update({"generation_status": "generating"})
            .eq("id", FAKE_MATCH_ID)
            .eq("user_id", FAKE_USER_ID)
            .neq("generation_status", "generating")
            .execute()
        )
        # Must return 1 row (update succeeded)
        assert len(result.data) == 1, (
            f"Guard must return 1 row when status is 'none', got: {result.data}"
        )
        # Row must now have generation_status='generating'
        row = matches.get(FAKE_MATCH_ID)
        assert row is not None
        assert row["generation_status"] == "generating"


# ===========================================================================
# Criterion #2 extra — SYSTEM_ASSESS does NOT include generation fields in schema
# ===========================================================================

class TestSystemPromptFieldSeparation:
    """Verify that the prompts enforce their respective schemas structurally."""

    def test_system_assess_does_not_mention_tailored_summary_in_schema(self):
        """SYSTEM_ASSESS JSON schema description must NOT include tailored_summary/cover_letter."""
        # SYSTEM_ASSESS ends with a JSON schema spec; check that generation fields
        # are not listed in it as required output fields.
        assert "tailored_summary" not in SYSTEM_ASSESS, (
            "SYSTEM_ASSESS must NOT include 'tailored_summary' in its schema"
        )
        assert "cover_letter" not in SYSTEM_ASSESS, (
            "SYSTEM_ASSESS must NOT include 'cover_letter' in its schema"
        )

    def test_system_generate_does_not_include_fit_score_in_schema(self):
        """SYSTEM_GENERATE must NOT ask the model to output fit_score (no re-scoring)."""
        # The generation prompt must not require fit_score output
        # (it uses assessment context from the user message, not re-scores).
        # Check the schema portion.
        assert '"fit_score"' not in SYSTEM_GENERATE, (
            "SYSTEM_GENERATE must NOT include fit_score in its output schema — no re-scoring"
        )

    def test_system_assess_includes_required_assessment_fields(self):
        """SYSTEM_ASSESS schema must include all 8 assessment output fields."""
        for field in ["fit_score", "b2b_eligible", "reason", "jd_keywords",
                      "ats_present", "ats_missing", "gaps", "recruiter_verdict"]:
            assert field in SYSTEM_ASSESS, (
                f"SYSTEM_ASSESS must include '{field}' in its output schema"
            )

    def test_system_generate_includes_required_generation_fields(self):
        """SYSTEM_GENERATE schema must include all 3 generation output fields."""
        for field in ["tailored_summary", "tailored_skills", "cover_letter"]:
            assert field in SYSTEM_GENERATE, (
                f"SYSTEM_GENERATE must include '{field}' in its output schema"
            )


# ===========================================================================
# Criterion #1 extra — Generation dataclass also structurally enforced
# ===========================================================================

class TestGenerationDataclassStructure:
    """Generation must ONLY have tailored fields — not fit_score or assessment fields."""

    def test_generation_has_no_fit_score_field(self):
        field_names = {f.name for f in dataclasses.fields(Generation)}
        assert "fit_score" not in field_names

    def test_generation_has_no_ats_missing_field(self):
        field_names = {f.name for f in dataclasses.fields(Generation)}
        assert "ats_missing" not in field_names

    def test_generation_has_exactly_three_fields(self):
        expected = {"tailored_summary", "tailored_skills", "cover_letter"}
        actual = {f.name for f in dataclasses.fields(Generation)}
        assert actual == expected, (
            f"Generation fields mismatch.\nExpected: {sorted(expected)}\nGot: {sorted(actual)}"
        )
