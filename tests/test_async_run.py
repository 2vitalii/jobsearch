"""Offline tests for the async POST /run → GET /run/{id} flow (SG-03).

These tests are fully offline: no Supabase, no LLM, no real scraper.
Everything is replaced with in-memory fakes via FastAPI dependency_overrides
and monkeypatching.

Scenarios covered:
  1. POST /run returns 202 + run_id immediately (no blocking).
  2. Second POST while a run is 'running' → 409 Conflict.
  3. GET /run/{id} returns current progress; 404 for unknown/wrong-user run_id.
  4. GET /run/latest returns the most recent run or 404 when none.
  5. Background loop on success → sets status='done' + summary.
  6. Background loop on exception → sets status='failed' + error without crash.
  7. Startup lifespan cleanup → orphaned 'running' runs are set to 'failed'.
  8. Response uses 'processed' field name (not the old 'queued').

The in-memory FakeRunsDB is the heart of the fake: it acts as a single dict
keyed by run_id and is injected everywhere via dependency_overrides.
"""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.main import app
from api.deps import (
    get_config,
    get_job_store,
    get_llm,
    get_scraper,
    get_supabase,
    get_user_client,
    get_user_state,
)
from api.auth import get_current_user, CurrentUser
from api.run import _run_background
from jobsearch.models import Job, PlatformConfig, SearchParams


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FAKE_USER_ID = "00000000-0000-0000-0000-000000000001"
FAKE_USER_ID_2 = "00000000-0000-0000-0000-000000000002"
FAKE_TOKEN = "fake-token"

FAKE_CV_MD = (
    "# Jane Doe\n"
    "Support Engineer\n\n"
    "## Professional Summary\nSupport engineer with 2 years experience.\n\n"
    "## Core Skills\n- Technical Support: SQL, REST APIs\n\n"
    "## Professional Experience\n### Support Engineer — Acme\n2024 - 2026\n- Resolved tickets.\n\n"
    "## Education\n### B.Sc. IT\nSome University\n"
)
FAKE_SHORT_PROFILE = "Support engineer, 2 years, SQL/REST."

RICH = {
    "fit_score": 88,
    "b2b_eligible": "yes",
    "reason": "strong match",
    "jd_keywords": ["sql", "rest api", "support"],
    "ats_present": ["sql", "support"],
    "ats_missing": [],
    "tailored_summary": "Support engineer aligned to the role.",
    "tailored_skills": ["Technical Support: SQL, REST APIs"],
    "gaps": "none",
    "recruiter_verdict": "shortlist",
    "cover_letter": "Dear team, I am a strong fit.",
}


class FakeLLM:
    def complete(self, *, model, system, messages, max_tokens) -> str:
        return json.dumps(RICH)


def _make_jobs(n: int = 2) -> list[Job]:
    out = []
    for i in range(n):
        key = f"offline_{uuid.uuid4().hex}"
        out.append(Job(
            dedup_key=key,
            source="LinkedIn",
            url=f"https://x/{key}",
            company=f"Acme{i}",
            title="Technical Support Engineer",
            location="Remote",
            region="WORLDWIDE",
            description="Fully remote support role. Work from anywhere. SQL, REST APIs.",
            date_posted="2026-06-20",
        ))
    return out


# ---------------------------------------------------------------------------
# In-memory fake stores
# ---------------------------------------------------------------------------

class FakeRunsDB:
    """Thread-safe in-memory store for the ``runs`` table."""

    def __init__(self):
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}

    def insert(self, row: dict) -> dict:
        row = dict(row)
        row.setdefault("id", str(uuid.uuid4()))
        row.setdefault("scraped", 0)
        row.setdefault("processed", 0)
        row.setdefault("generated", 0)
        row.setdefault("skipped_low_fit", 0)
        row.setdefault("error", None)
        row.setdefault("summary", None)
        row.setdefault("created_at", "2026-06-22T00:00:00Z")
        row.setdefault("updated_at", "2026-06-22T00:00:00Z")
        with self._lock:
            self._rows[row["id"]] = row
        return row

    def update_matching(self, filters: list[tuple], fields: dict) -> list[dict]:
        """Update rows matching all (col, val) pairs; return updated rows."""
        updated = []
        with self._lock:
            for row in self._rows.values():
                if all(row.get(col) == val for col, val in filters):
                    row.update(fields)
                    updated.append(dict(row))
        return updated

    def select_matching(self, filters: list[tuple]) -> list[dict]:
        with self._lock:
            return [
                dict(r) for r in self._rows.values()
                if all(r.get(col) == val for col, val in filters)
            ]

    def get(self, run_id: str) -> dict | None:
        with self._lock:
            return dict(self._rows[run_id]) if run_id in self._rows else None

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()


class FakeUserState:
    """In-memory fake UserState."""
    def __init__(self):
        self._processed: set[tuple[str, str]] = set()

    def is_processed(self, user_id: str, dedup_key: str) -> bool:
        return (user_id, dedup_key) in self._processed

    def mark_processed(self, user_id: str, dedup_key: str) -> None:
        self._processed.add((user_id, dedup_key))


class FakeJobStore:
    """In-memory fake JobStore."""
    def __init__(self):
        self._jobs: list[Job] = []

    def save(self, jobs: list[Job]) -> None:
        self._jobs.extend(jobs)


# ---------------------------------------------------------------------------
# Fake Supabase client
# ---------------------------------------------------------------------------

class _FakeQueryBuilder:
    """Minimal fluent builder delegating to FakeRunsDB."""

    def __init__(self, db: FakeRunsDB, table: str):
        self._db = db
        self._table = table
        self._op: str | None = None
        self._data: dict | None = None
        self._filters: list[tuple] = []
        self._order_col: str | None = None
        self._order_desc: bool = False
        self._limit_val: int | None = None

    def insert(self, data: dict):
        self._op = "insert"
        self._data = data
        return self

    def update(self, data: dict):
        self._op = "update"
        self._data = data
        return self

    def select(self, _cols: str = "*"):
        self._op = "select"
        return self

    def upsert(self, data: dict, **_kw):
        self._op = "insert"
        self._data = data
        return self

    def eq(self, col: str, val: Any):
        self._filters.append((col, val))
        return self

    def order(self, col: str, desc: bool = False):
        self._order_col = col
        self._order_desc = desc
        return self

    def limit(self, n: int):
        self._limit_val = n
        return self

    def execute(self):
        class _Result:
            data: list = []

        result = _Result()

        if self._table != "runs":
            result.data = []
            return result

        if self._op == "insert":
            row = self._db.insert(self._data or {})
            result.data = [row]

        elif self._op == "update":
            result.data = self._db.update_matching(self._filters, self._data or {})

        elif self._op == "select":
            rows = self._db.select_matching(self._filters)
            if self._order_col:
                rows.sort(
                    key=lambda r: r.get(self._order_col) or "",
                    reverse=self._order_desc,
                )
            if self._limit_val is not None:
                rows = rows[: self._limit_val]
            result.data = rows

        return result


class FakeSupabase:
    """Minimal Supabase-like object routing through FakeRunsDB."""

    def __init__(self, db: FakeRunsDB):
        self._db = db

    def table(self, name: str) -> _FakeQueryBuilder:
        return _FakeQueryBuilder(self._db, name)


# ---------------------------------------------------------------------------
# Fake upload helper
# ---------------------------------------------------------------------------

def _fake_upload_docx(supabase, user_id, job, score, data) -> str:
    return f"fake/{user_id}/{score}_{job.dedup_key}.docx"


def _fake_write_match(supabase, user_id, job, res, ats_report, cv_docx_path, run_id=None) -> None:
    pass


# ---------------------------------------------------------------------------
# Shared fixture factory
# ---------------------------------------------------------------------------

def _make_app_overrides(runs_db: FakeRunsDB, user_id: str, jobs: list[Job], monkeypatch):
    """Wire all offline fakes into the app and return (tc, runs_db, user_state)."""
    import api.run as run_mod

    fake_supabase = FakeSupabase(runs_db)
    fake_user_state = FakeUserState()
    fake_job_store = FakeJobStore()
    fake_llm = FakeLLM()
    fake_config = PlatformConfig()

    # Patch storage + DB-write helpers (not injected via DI) so no real calls.
    monkeypatch.setattr(run_mod, "_upload_docx", _fake_upload_docx)
    monkeypatch.setattr(run_mod, "_write_match", _fake_write_match)
    # Patch make_supabase_client so _run_background gets the fake client.
    monkeypatch.setattr(run_mod, "make_supabase_client", lambda: fake_supabase)

    # Patch _load_search_params / _load_cv to never hit the DB.
    monkeypatch.setattr(
        run_mod,
        "_load_search_params",
        lambda sb, uid: SearchParams(
            keywords=["support"], locations=["Worldwide"],
            period_hours=168, work_format="remote", loose=False, targeted=False,
        ),
    )
    monkeypatch.setattr(
        run_mod,
        "_load_cv",
        lambda sb, uid: (FAKE_CV_MD, FAKE_SHORT_PROFILE),
    )

    def override_user():
        return CurrentUser(user_id=user_id, email="test@example.com", token=FAKE_TOKEN)

    def override_supabase():
        return fake_supabase

    def override_user_client():
        return fake_supabase

    def override_job_store():
        return fake_job_store

    def override_user_state():
        return fake_user_state

    def override_llm():
        return fake_llm

    def override_config():
        return fake_config

    def override_scraper():
        return lambda params, config: jobs

    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_supabase] = override_supabase
    app.dependency_overrides[get_user_client] = override_user_client
    app.dependency_overrides[get_job_store] = override_job_store
    app.dependency_overrides[get_user_state] = override_user_state
    app.dependency_overrides[get_llm] = override_llm
    app.dependency_overrides[get_config] = override_config
    app.dependency_overrides[get_scraper] = override_scraper

    return fake_supabase, fake_user_state, fake_job_store


@pytest.fixture()
def runs_db() -> FakeRunsDB:
    db = FakeRunsDB()
    yield db
    db.clear()


@pytest.fixture()
def fake_jobs() -> list[Job]:
    return _make_jobs(2)


@pytest.fixture()
def client(runs_db, fake_jobs, monkeypatch):
    """TestClient with full dependency overrides for offline testing."""
    _make_app_overrides(runs_db, FAKE_USER_ID, fake_jobs, monkeypatch)
    with TestClient(app) as tc:
        yield tc, runs_db
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests: POST /run
# ---------------------------------------------------------------------------

class TestPostRun:
    def test_returns_202_with_run_id(self, client):
        tc, db = client
        r = tc.post("/run")
        assert r.status_code == 202, r.text
        body = r.json()
        assert "run_id" in body
        uuid.UUID(body["run_id"])  # must be a valid UUID

    def test_run_row_created_in_db(self, client):
        tc, db = client
        r = tc.post("/run")
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        row = db.get(run_id)
        assert row is not None
        assert row["user_id"] == FAKE_USER_ID

    def test_run_is_non_blocking(self, client, monkeypatch):
        """POST returns before (or just as) the background task finishes.
        We verify this structurally: the handler returns 202 and background
        tasks run in the test client's synchronous flow, but the key point is
        the response shape is run_id (not the old RunSummary)."""
        tc, db = client
        r = tc.post("/run")
        assert r.status_code == 202
        body = r.json()
        # Must have run_id, must NOT have the old synchronous fields at top level.
        assert "run_id" in body
        assert "scraped" not in body
        assert "queued" not in body
        assert "generated" not in body

    def test_second_post_returns_409_while_active(self, runs_db, monkeypatch):
        """If the user already has a 'running' row, a second POST must 409."""
        import api.run as run_mod

        jobs = _make_jobs(0)
        _make_app_overrides(runs_db, FAKE_USER_ID, jobs, monkeypatch)

        # Insert a 'running' row BEFORE making the request.
        runs_db.insert({"user_id": FAKE_USER_ID, "status": "running"})

        # Also prevent the background task from running so the row stays running.
        # (In real code the task clears it after the fact; here we test the 409 guard.)
        monkeypatch.setattr(run_mod, "_run_background", lambda **kw: None)

        with TestClient(app) as tc:
            r = tc.post("/run")
        assert r.status_code == 409, r.text
        assert "already in progress" in r.json()["detail"].lower()

        app.dependency_overrides.clear()

    def test_404_when_search_params_missing(self, client, monkeypatch):
        import api.run as run_mod

        monkeypatch.setattr(
            run_mod,
            "_load_search_params",
            lambda sb, uid: (_ for _ in ()).throw(HTTPException(status_code=404, detail="No saved search")),
        )
        tc, db = client
        r = tc.post("/run")
        assert r.status_code == 404

    def test_404_when_cv_missing(self, client, monkeypatch):
        import api.run as run_mod

        monkeypatch.setattr(
            run_mod,
            "_load_cv",
            lambda sb, uid: (_ for _ in ()).throw(HTTPException(status_code=404, detail="No CV")),
        )
        tc, db = client
        r = tc.post("/run")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /run/{run_id}
# ---------------------------------------------------------------------------

class TestGetRunStatus:
    def test_get_run_status_after_post(self, client):
        """After POST, GET /run/{id} should return a valid status body."""
        tc, db = client
        r_post = tc.post("/run")
        assert r_post.status_code == 202
        run_id = r_post.json()["run_id"]

        r = tc.get(f"/run/{run_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] in ("running", "done", "failed")
        assert isinstance(body["scraped"], int)
        assert isinstance(body["processed"], int)
        assert isinstance(body["generated"], int)
        assert isinstance(body["skipped_low_fit"], int)

    def test_get_run_404_for_unknown_id(self, client):
        tc, db = client
        r = tc.get(f"/run/{uuid.uuid4()}")
        assert r.status_code == 404

    def test_get_run_404_for_other_users_run(self, client, runs_db):
        """A run belonging to user2 is not visible to user1 (RLS simulation)."""
        tc, db = client
        # Insert a run for a different user directly into the fake DB.
        other_run = runs_db.insert({"user_id": FAKE_USER_ID_2, "status": "done"})
        # The FakeSupabase.select filters by user_id via eq() in the endpoint.
        r = tc.get(f"/run/{other_run['id']}")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /run/latest
# ---------------------------------------------------------------------------

class TestGetRunLatest:
    def test_latest_returns_most_recent(self, client):
        tc, db = client
        r_post = tc.post("/run")
        assert r_post.status_code == 202

        r = tc.get("/run/latest")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "status" in body
        assert "processed" in body

    def test_latest_404_when_no_runs(self, runs_db, monkeypatch):
        """A user with no runs gets 404 from GET /run/latest."""
        jobs: list[Job] = []
        _make_app_overrides(runs_db, FAKE_USER_ID_2, jobs, monkeypatch)

        # Make sure there are no runs for user2.
        with TestClient(app) as tc2:
            r = tc2.get("/run/latest")
        assert r.status_code == 404

        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests: background task unit-tests (_run_background in isolation)
# ---------------------------------------------------------------------------

class TestBackgroundTask:
    def _setup(self) -> tuple[FakeRunsDB, str, FakeSupabase]:
        db = FakeRunsDB()
        run_id = db.insert({"user_id": FAKE_USER_ID, "status": "running"})["id"]
        sb = FakeSupabase(db)
        return db, run_id, sb

    def test_successful_run_sets_done(self, monkeypatch):
        import api.run as run_mod

        db, run_id, fake_sb = self._setup()
        monkeypatch.setattr(run_mod, "_upload_docx", _fake_upload_docx)
        monkeypatch.setattr(run_mod, "_write_match", _fake_write_match)
        monkeypatch.setattr(run_mod, "make_supabase_client", lambda: fake_sb)

        _run_background(
            run_id=run_id,
            user_id=FAKE_USER_ID,
            params=SearchParams(
                keywords=["support"], locations=["WW"],
                period_hours=168, work_format="remote", loose=False, targeted=False,
            ),
            cv_markdown=FAKE_CV_MD,
            short_profile=FAKE_SHORT_PROFILE,
            config=PlatformConfig(),
            scraper=lambda p, c: _make_jobs(2),
            job_store=FakeJobStore(),
            user_state=FakeUserState(),
            llm=FakeLLM(),
        )

        row = db.get(run_id)
        assert row["status"] == "done", f"expected done, got: {row}"
        # 2 WORLDWIDE jobs, both above pre_min_fit (88) and min_fit (80 default).
        assert row["scraped"] == 2
        assert row["processed"] == 2   # both passed the filter step
        assert row["generated"] == 2   # both survived scoring and got packages
        assert row["skipped_low_fit"] == 0
        assert row["summary"] is not None
        assert row["error"] is None

    def test_successful_run_summary_has_expected_fields(self, monkeypatch):
        import api.run as run_mod

        db, run_id, fake_sb = self._setup()
        monkeypatch.setattr(run_mod, "_upload_docx", _fake_upload_docx)
        monkeypatch.setattr(run_mod, "_write_match", _fake_write_match)
        monkeypatch.setattr(run_mod, "make_supabase_client", lambda: fake_sb)

        _run_background(
            run_id=run_id,
            user_id=FAKE_USER_ID,
            params=SearchParams(
                keywords=["support"], locations=["WW"],
                period_hours=168, work_format="remote", loose=False, targeted=False,
            ),
            cv_markdown=FAKE_CV_MD,
            short_profile=FAKE_SHORT_PROFILE,
            config=PlatformConfig(),
            scraper=lambda p, c: _make_jobs(2),
            job_store=FakeJobStore(),
            user_state=FakeUserState(),
            llm=FakeLLM(),
        )

        row = db.get(run_id)
        summary = row["summary"]
        assert "scraped" in summary
        assert "queued" in summary   # stored as 'queued' inside the jsonb
        assert "generated" in summary
        assert "skipped_low_fit" in summary

    def test_exception_in_scraper_sets_failed(self, monkeypatch):
        """If the scraper throws, the run is marked failed without crashing."""
        import api.run as run_mod

        db, run_id, fake_sb = self._setup()
        monkeypatch.setattr(run_mod, "make_supabase_client", lambda: fake_sb)

        def bad_scraper(p, c):
            raise RuntimeError("network error")

        _run_background(
            run_id=run_id,
            user_id=FAKE_USER_ID,
            params=SearchParams(keywords=[], locations=[], period_hours=168, work_format="remote", loose=False, targeted=False),
            cv_markdown=FAKE_CV_MD,
            short_profile=FAKE_SHORT_PROFILE,
            config=PlatformConfig(),
            scraper=bad_scraper,
            job_store=FakeJobStore(),
            user_state=FakeUserState(),
            llm=FakeLLM(),
        )

        row = db.get(run_id)
        assert row["status"] == "failed", f"expected failed, got: {row}"
        # Error is sanitized: only the exception class name is stored (no raw
        # exception message that might contain scraped text or internal paths).
        assert row["error"] == "RuntimeError"

    def test_exception_in_llm_sets_failed(self, monkeypatch):
        """If the LLM throws mid-loop, the run is marked failed."""
        import api.run as run_mod

        db, run_id, fake_sb = self._setup()
        monkeypatch.setattr(run_mod, "make_supabase_client", lambda: fake_sb)
        monkeypatch.setattr(run_mod, "_upload_docx", _fake_upload_docx)
        monkeypatch.setattr(run_mod, "_write_match", _fake_write_match)

        class BoomLLM:
            def complete(self, **_kw) -> str:
                raise ConnectionError("LLM unreachable")

        _run_background(
            run_id=run_id,
            user_id=FAKE_USER_ID,
            params=SearchParams(keywords=["support"], locations=["WW"], period_hours=168, work_format="remote", loose=False, targeted=False),
            cv_markdown=FAKE_CV_MD,
            short_profile=FAKE_SHORT_PROFILE,
            config=PlatformConfig(),
            scraper=lambda p, c: _make_jobs(2),
            job_store=FakeJobStore(),
            user_state=FakeUserState(),
            llm=BoomLLM(),
        )

        row = db.get(run_id)
        assert row["status"] == "failed"
        # Error is sanitized: only the exception class name is stored.
        assert row["error"] == "ConnectionError"

    def test_failed_mark_doesnt_crash_if_db_unreachable(self, monkeypatch):
        """If _mark_run_failed itself throws, _run_background must not propagate."""
        import api.run as run_mod

        db = FakeRunsDB()
        run_id = db.insert({"user_id": FAKE_USER_ID, "status": "running"})["id"]

        class BrokenSupabase:
            def table(self, _name):
                raise OSError("DB unreachable")

        monkeypatch.setattr(run_mod, "make_supabase_client", lambda: BrokenSupabase())

        # Must not raise — the process must stay alive.
        _run_background(
            run_id=run_id,
            user_id=FAKE_USER_ID,
            params=SearchParams(keywords=[], locations=[], period_hours=168, work_format="remote", loose=False, targeted=False),
            cv_markdown=FAKE_CV_MD,
            short_profile=FAKE_SHORT_PROFILE,
            config=PlatformConfig(),
            scraper=lambda p, c: (_ for _ in ()).throw(RuntimeError("boom")),
            job_store=FakeJobStore(),
            user_state=FakeUserState(),
            llm=FakeLLM(),
        )
        # Row stays as-is (DB was unreachable), but process didn't crash.
        assert db.get(run_id)["status"] == "running"


# ---------------------------------------------------------------------------
# Tests: startup lifespan orphan cleanup
# ---------------------------------------------------------------------------

class TestStartupCleanup:
    def test_orphaned_runs_are_marked_failed(self):
        """Simulates the lifespan update query against the fake DB."""
        db = FakeRunsDB()
        r1 = db.insert({"user_id": FAKE_USER_ID, "status": "running"})
        r2 = db.insert({"user_id": FAKE_USER_ID_2, "status": "running"})
        r3 = db.insert({"user_id": FAKE_USER_ID, "status": "done"})

        fake_sb = FakeSupabase(db)

        # Simulate the exact query the lifespan executes.
        fake_sb.table("runs").update(
            {"status": "failed", "error": "interrupted by restart", "updated_at": "2026-06-22T00:00:00Z"}
        ).eq("status", "running").execute()

        assert db.get(r1["id"])["status"] == "failed"
        assert db.get(r2["id"])["status"] == "failed"
        assert db.get(r1["id"])["error"] == "interrupted by restart"
        assert db.get(r2["id"])["error"] == "interrupted by restart"
        # Already-done row is untouched.
        assert db.get(r3["id"])["status"] == "done"
        assert db.get(r3["id"])["error"] is None

    def test_lifespan_drives_real_cleanup(self, monkeypatch):
        """The real lifespan() context manager flips orphaned 'running' rows to
        'failed' at startup and does NOT prevent startup when the DB raises."""
        import api.main as main_mod
        from api.main import lifespan

        db = FakeRunsDB()
        r1 = db.insert({"user_id": FAKE_USER_ID, "status": "running"})
        r2 = db.insert({"user_id": FAKE_USER_ID_2, "status": "running"})
        r3 = db.insert({"user_id": FAKE_USER_ID, "status": "done"})
        fake_sb = FakeSupabase(db)

        # Provide env vars so the lifespan takes the active branch.
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SECRET_KEY", "fake-secret-key")

        # Redirect make_supabase_client inside api.main to the fake client.
        import jobsearch.supabase_store as store_mod
        monkeypatch.setattr(store_mod, "make_supabase_client", lambda: fake_sb)

        import asyncio

        async def _run():
            async with lifespan(main_mod.app):
                pass  # startup ran; now assert cleanup happened

        asyncio.run(_run())

        # Orphaned 'running' rows must be 'failed'.
        assert db.get(r1["id"])["status"] == "failed"
        assert db.get(r2["id"])["status"] == "failed"
        assert db.get(r1["id"])["error"] == "interrupted by restart"
        # Already-done row must be untouched.
        assert db.get(r3["id"])["status"] == "done"
        assert db.get(r3["id"])["error"] is None

    def test_lifespan_continues_when_supabase_raises(self, monkeypatch):
        """Startup must complete (yield) even if the orphan-cleanup query
        raises — e.g. DB unreachable at boot time."""
        import api.main as main_mod
        from api.main import lifespan

        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SECRET_KEY", "fake-secret-key")

        class ExplodingSupabase:
            def table(self, _name):
                raise OSError("DB unreachable at startup")

        import jobsearch.supabase_store as store_mod
        monkeypatch.setattr(store_mod, "make_supabase_client", lambda: ExplodingSupabase())

        import asyncio

        reached_yield = False

        async def _run():
            nonlocal reached_yield
            async with lifespan(main_mod.app):
                reached_yield = True  # proves startup didn't crash

        asyncio.run(_run())
        assert reached_yield, "lifespan must yield even when cleanup raises"


# ---------------------------------------------------------------------------
# Tests: response field names / contract
# ---------------------------------------------------------------------------

class TestContractShape:
    def test_post_returns_run_id_only(self, client):
        """POST /run must return {run_id} at 202, not the old RunSummary fields."""
        tc, db = client
        r = tc.post("/run")
        assert r.status_code == 202
        body = r.json()
        assert set(body.keys()) == {"run_id"}

    def test_get_status_uses_processed_not_queued(self, client):
        """GET /run/{id} must use 'processed' (SG-03 contract), not 'queued'."""
        tc, db = client
        r_post = tc.post("/run")
        assert r_post.status_code == 202
        run_id = r_post.json()["run_id"]

        r = tc.get(f"/run/{run_id}")
        assert r.status_code == 200
        body = r.json()
        assert "processed" in body
        assert "queued" not in body

    def test_get_latest_uses_processed_not_queued(self, client):
        """GET /run/latest must use 'processed', not 'queued'."""
        tc, db = client
        tc.post("/run")  # create a run

        r = tc.get("/run/latest")
        assert r.status_code == 200
        body = r.json()
        assert "processed" in body
        assert "queued" not in body

    def test_get_status_fields_present(self, client):
        """All required RunStatus fields must be present in the response."""
        tc, db = client
        r_post = tc.post("/run")
        run_id = r_post.json()["run_id"]

        r = tc.get(f"/run/{run_id}")
        body = r.json()
        required = {"status", "scraped", "processed", "generated", "skipped_low_fit"}
        assert required.issubset(set(body.keys()))

    def test_done_run_has_summary(self, client):
        """After a successful background task, the run should have a summary."""
        tc, db = client
        r_post = tc.post("/run")
        run_id = r_post.json()["run_id"]

        # TestClient runs background tasks synchronously before returning.
        r = tc.get(f"/run/{run_id}")
        body = r.json()
        if body["status"] == "done":
            assert body["summary"] is not None


# ---------------------------------------------------------------------------
# Tests: attribution seam — run_id + search_snapshot (SG-04)
# ---------------------------------------------------------------------------

class TestAttributionSeam:
    """Verify the run_id / search_snapshot attribution wiring added in 0009."""

    FAKE_PARAMS = SearchParams(
        keywords=["support", "python"],
        locations=["Worldwide", "EU"],
        period_hours=48,
        work_format="remote",
        loose=True,
        targeted=False,
    )
    EXPECTED_SNAPSHOT = {
        "keywords": ["support", "python"],
        "locations": ["Worldwide", "EU"],
        "period_hours": 48,
        "work_format": "remote",
        "loose": True,
        "targeted": False,
        "exclude_senior": False,
    }

    def test_post_run_writes_search_snapshot_to_run_row(self, runs_db, monkeypatch):
        """POST /run must store the search_snapshot in the runs row."""
        import api.run as run_mod

        _make_app_overrides(runs_db, FAKE_USER_ID, [], monkeypatch)

        # Override _load_search_params with known params.
        monkeypatch.setattr(
            run_mod,
            "_load_search_params",
            lambda sb, uid: self.FAKE_PARAMS,
        )
        # Prevent the background task from running (no jobs to process).
        monkeypatch.setattr(run_mod, "_run_background", lambda **kw: None)

        with TestClient(app) as tc:
            r = tc.post("/run")

        assert r.status_code == 202, r.text
        run_id = r.json()["run_id"]
        row = runs_db.get(run_id)
        assert row is not None
        assert row.get("search_snapshot") == self.EXPECTED_SNAPSHOT

        app.dependency_overrides.clear()

    def test_get_run_status_exposes_search_snapshot(self, runs_db, monkeypatch):
        """GET /run/{id} must include search_snapshot in the response."""
        import api.run as run_mod

        _make_app_overrides(runs_db, FAKE_USER_ID, [], monkeypatch)
        monkeypatch.setattr(
            run_mod,
            "_load_search_params",
            lambda sb, uid: self.FAKE_PARAMS,
        )
        monkeypatch.setattr(run_mod, "_run_background", lambda **kw: None)

        with TestClient(app) as tc:
            r_post = tc.post("/run")
            assert r_post.status_code == 202
            run_id = r_post.json()["run_id"]

            r_get = tc.get(f"/run/{run_id}")
            assert r_get.status_code == 200, r_get.text
            body = r_get.json()

        assert "search_snapshot" in body
        assert body["search_snapshot"] == self.EXPECTED_SNAPSHOT

        app.dependency_overrides.clear()

    def test_get_run_latest_exposes_search_snapshot(self, runs_db, monkeypatch):
        """GET /run/latest must include search_snapshot in the response."""
        import api.run as run_mod

        _make_app_overrides(runs_db, FAKE_USER_ID, [], monkeypatch)
        monkeypatch.setattr(
            run_mod,
            "_load_search_params",
            lambda sb, uid: self.FAKE_PARAMS,
        )
        monkeypatch.setattr(run_mod, "_run_background", lambda **kw: None)

        with TestClient(app) as tc:
            r_post = tc.post("/run")
            assert r_post.status_code == 202

            r_latest = tc.get("/run/latest")
            assert r_latest.status_code == 200, r_latest.text
            body = r_latest.json()

        assert "search_snapshot" in body
        assert body["search_snapshot"] == self.EXPECTED_SNAPSHOT

        app.dependency_overrides.clear()

    def test_get_run_snapshot_none_for_legacy_row(self, runs_db, monkeypatch):
        """A run row without search_snapshot (legacy/pre-migration) must still
        serialize cleanly — search_snapshot must be None, not raise."""
        _make_app_overrides(runs_db, FAKE_USER_ID, [], monkeypatch)

        # Insert a legacy row without search_snapshot (simulates pre-0009 row).
        legacy_run = runs_db.insert({"user_id": FAKE_USER_ID, "status": "done"})
        assert legacy_run.get("search_snapshot") is None

        with TestClient(app) as tc:
            r = tc.get(f"/run/{legacy_run['id']}")

        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("search_snapshot") is None

        app.dependency_overrides.clear()

    def test_write_match_receives_run_id_in_background_loop(self, monkeypatch):
        """_run_background must pass run_id to _write_match for every match."""
        import api.run as run_mod

        db = FakeRunsDB()
        run_id = db.insert({"user_id": FAKE_USER_ID, "status": "running"})["id"]
        fake_sb = FakeSupabase(db)

        captured_run_ids: list[str | None] = []

        def _capturing_write_match(supabase, user_id, job, res, ats_report, cv_docx_path, run_id=None):
            captured_run_ids.append(run_id)

        monkeypatch.setattr(run_mod, "_upload_docx", _fake_upload_docx)
        monkeypatch.setattr(run_mod, "_write_match", _capturing_write_match)
        monkeypatch.setattr(run_mod, "make_supabase_client", lambda: fake_sb)

        _run_background(
            run_id=run_id,
            user_id=FAKE_USER_ID,
            params=SearchParams(
                keywords=["support"], locations=["WW"],
                period_hours=168, work_format="remote", loose=False, targeted=False,
            ),
            cv_markdown=FAKE_CV_MD,
            short_profile=FAKE_SHORT_PROFILE,
            config=PlatformConfig(),
            scraper=lambda p, c: _make_jobs(2),
            job_store=FakeJobStore(),
            user_state=FakeUserState(),
            llm=FakeLLM(),
        )

        # Both generated matches must carry the correct run_id.
        assert len(captured_run_ids) == 2, f"expected 2 calls, got: {captured_run_ids}"
        for rid in captured_run_ids:
            assert rid == run_id, f"expected run_id={run_id!r}, got {rid!r}"


# ---------------------------------------------------------------------------
# Tests: MatchDetail model — nullable run_id (offline unit test)
# ---------------------------------------------------------------------------

class TestMatchDetailNullableRunId:
    """Verify MatchDetail correctly handles rows with and without run_id."""

    def test_match_detail_with_run_id_none(self):
        """A match row missing run_id (legacy or pre-0009) must serialize cleanly."""
        from api.matches import MatchDetail

        row_without_run_id = {
            "id": str(uuid.uuid4()),
            # run_id absent — simulates a pre-0009 row
            "status": "GENERATED",
            "fit_score": 75,
            "b2b_eligible": "no",
            "analysis": {},
            "cover_letter": "Dear team",
            "ats_report": "# ATS",
        }
        detail = MatchDetail(
            id=row_without_run_id["id"],
            run_id=row_without_run_id.get("run_id"),  # None
            status=row_without_run_id.get("status"),
            fit_score=row_without_run_id.get("fit_score"),
        )
        assert detail.run_id is None
        assert detail.id == row_without_run_id["id"]
        assert detail.fit_score == 75

    def test_match_detail_with_run_id_present(self):
        """A match row with a run_id must expose it on the model."""
        from api.matches import MatchDetail

        fake_run_id = str(uuid.uuid4())
        detail = MatchDetail(
            id=str(uuid.uuid4()),
            run_id=fake_run_id,
            status="GENERATED",
            fit_score=88,
        )
        assert detail.run_id == fake_run_id


# ---------------------------------------------------------------------------
# Tests: RunStatus.status Literal validation (type-tightening)
# ---------------------------------------------------------------------------

class TestRunStatusLiteralValidation:
    """Verify that RunStatus.status is a Literal["running","done","failed"].

    The DB CHECK constraint enforces the same set at the database layer; this
    test ensures Pydantic catches any value that somehow bypasses the DB (e.g.
    a future migration mistake or a stale row from a different schema version).
    _row_to_status is the seam where a raw DB dict flows into the typed model,
    so it is the natural place to test both valid and invalid inputs.
    """

    def _base_row(self, status: str) -> dict:
        """Minimal runs-table row dict with the given status."""
        return {
            "status": status,
            "scraped": 0,
            "processed": 0,
            "generated": 0,
            "skipped_low_fit": 0,
            "summary": None,
            "error": None,
            "search_snapshot": None,
        }

    def test_valid_status_running(self):
        from api.run import _row_to_status
        result = _row_to_status(self._base_row("running"))
        assert result.status == "running"

    def test_valid_status_done(self):
        from api.run import _row_to_status
        result = _row_to_status(self._base_row("done"))
        assert result.status == "done"

    def test_valid_status_failed(self):
        from api.run import _row_to_status
        result = _row_to_status(self._base_row("failed"))
        assert result.status == "failed"

    def test_invalid_status_raises_validation_error(self):
        """A status value not in the Literal must raise a Pydantic ValidationError."""
        from pydantic import ValidationError
        from api.run import _row_to_status

        with pytest.raises(ValidationError):
            _row_to_status(self._base_row("queued"))

    def test_invalid_status_pending_raises(self):
        from pydantic import ValidationError
        from api.run import _row_to_status

        with pytest.raises(ValidationError):
            _row_to_status(self._base_row("pending"))

    def test_invalid_status_empty_string_raises(self):
        from pydantic import ValidationError
        from api.run import _row_to_status

        with pytest.raises(ValidationError):
            _row_to_status(self._base_row(""))
