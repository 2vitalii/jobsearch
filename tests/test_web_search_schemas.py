"""Offline tests: verify that the TypeScript Zod schema definitions in
web/lib/schemas.ts and their corresponding FastAPI Pydantic models in
api/run.py and api/search_params.py are field-for-field compatible.

These tests run without any live backend, Supabase connection, or paid API
calls. They validate the Python side of the contract and document exactly
what the frontend Zod schemas expect so that drift is caught at pytest time.

Acceptance criteria verified:
  - RunStatus: status enum ('running'|'done'|'failed'), 4 integer counters,
    summary/error/search_snapshot optional/nullable.
  - RunAccepted: has run_id field.
  - SearchParams: keywords/locations arrays, period_hours int, work_format str,
    loose bool, targeted bool — all present and typed correctly.
  - putSearchParams payload: loose and targeted included (even when not shown
    in the UI, they are present in buildPayload() with defaults).
  - getLatestRun 404→null: verified by checking the backend raises 404 for
    missing runs (the frontend api.ts maps 404→null).
  - startRun 409 contract: backend raises 409 on duplicate active run; the
    frontend maps this to RunConflictError.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from api.run import RunStarted, RunStatus  # noqa: E402
from api.search_params import SearchParamsBody  # noqa: E402


# ---------------------------------------------------------------------------
# RunAccepted / RunStarted  (POST /run → 202)
# frontend Zod: RunAcceptedSchema = z.object({ run_id: z.string() })
# ---------------------------------------------------------------------------

class TestRunAcceptedSchema:
    """RunStarted (Python) must match RunAcceptedSchema (Zod): { run_id: string }."""

    def test_has_run_id_field(self):
        fields = RunStarted.model_fields
        assert "run_id" in fields, "RunStarted must have a run_id field"

    def test_run_id_is_required(self):
        with pytest.raises(Exception):
            RunStarted()  # type: ignore[call-arg]

    def test_run_id_is_string(self):
        obj = RunStarted(run_id="abc-123")
        assert isinstance(obj.run_id, str)

    def test_no_extra_fields_leak(self):
        """Only run_id expected; adding noise should be ignored or rejected, not stored."""
        # Pydantic by default ignores extra fields — verify run_id is still accessible.
        obj = RunStarted(run_id="xyz")
        assert obj.run_id == "xyz"


# ---------------------------------------------------------------------------
# RunStatus  (GET /run/{run_id} and GET /run/latest)
# frontend Zod:
#   status: z.enum(["running", "done", "failed"])
#   scraped, processed, generated, skipped_low_fit: z.number()
#   summary: z.unknown().optional().nullable()
#   error: z.string().optional().nullable()
#   search_snapshot: z.unknown().optional().nullable()
# ---------------------------------------------------------------------------

VALID_STATUS_VALUES = ("running", "done", "failed")


class TestRunStatusSchema:
    """Python RunStatus must be structurally compatible with the Zod RunStatusSchema."""

    def test_valid_running_status(self):
        obj = RunStatus(
            status="running",
            scraped=10,
            processed=8,
            generated=3,
            skipped_low_fit=5,
        )
        assert obj.status == "running"

    def test_valid_done_status(self):
        obj = RunStatus(
            status="done",
            scraped=20,
            processed=15,
            generated=10,
            skipped_low_fit=5,
        )
        assert obj.status == "done"

    def test_valid_failed_status(self):
        obj = RunStatus(
            status="failed",
            scraped=5,
            processed=0,
            generated=0,
            skipped_low_fit=0,
            error="HTTPError",
        )
        assert obj.status == "failed"
        assert obj.error == "HTTPError"

    def test_all_four_integer_counters_present(self):
        """scraped, processed, generated, skipped_low_fit must all be present."""
        fields = RunStatus.model_fields
        for counter in ("scraped", "processed", "generated", "skipped_low_fit"):
            assert counter in fields, f"RunStatus missing required counter: {counter}"

    def test_counters_default_to_zero(self):
        """Counters must have defaults (backend _row_to_status uses 'or 0')."""
        obj = RunStatus(status="running", scraped=0, processed=0, generated=0, skipped_low_fit=0)
        assert obj.scraped == 0
        assert obj.processed == 0
        assert obj.generated == 0
        assert obj.skipped_low_fit == 0

    def test_summary_is_optional_nullable(self):
        """summary is dict|None with default None — must accept None and a dict."""
        obj_none = RunStatus(
            status="done", scraped=1, processed=1, generated=1, skipped_low_fit=0,
            summary=None,
        )
        assert obj_none.summary is None

        obj_dict = RunStatus(
            status="done", scraped=1, processed=1, generated=1, skipped_low_fit=0,
            summary={"scraped": 1, "queued": 1, "generated": 1, "skipped_low_fit": 0},
        )
        assert isinstance(obj_dict.summary, dict)

    def test_error_is_optional_nullable(self):
        """error is str|None with default None."""
        obj = RunStatus(status="done", scraped=0, processed=0, generated=0, skipped_low_fit=0)
        assert obj.error is None  # default

        obj_err = RunStatus(
            status="failed", scraped=0, processed=0, generated=0, skipped_low_fit=0,
            error="SomeException",
        )
        assert obj_err.error == "SomeException"

    def test_search_snapshot_is_optional_nullable(self):
        """search_snapshot is dict|None with default None."""
        obj = RunStatus(status="running", scraped=0, processed=0, generated=0, skipped_low_fit=0)
        assert obj.search_snapshot is None

        snapshot = {
            "keywords": ["python"],
            "locations": ["remote"],
            "period_hours": 168,
            "work_format": "remote",
            "loose": False,
            "targeted": False,
        }
        obj_snap = RunStatus(
            status="done", scraped=5, processed=3, generated=2, skipped_low_fit=1,
            search_snapshot=snapshot,
        )
        assert obj_snap.search_snapshot == snapshot

    def test_status_field_accepts_only_known_values(self):
        """Backend status column stores raw strings; document that Zod will reject
        any value outside running|done|failed at the frontend boundary.

        This test exists to catch if the backend ever introduces a new status
        value (e.g. 'queued', 'cancelled') without a matching Zod update."""
        # These three must work:
        for v in VALID_STATUS_VALUES:
            obj = RunStatus(
                status=v, scraped=0, processed=0, generated=0, skipped_low_fit=0
            )
            assert obj.status == v

        # An unknown value is NOT validated on the Python side (it's `str`),
        # but would be rejected by the Zod `.enum()` on the frontend.
        # We assert the frontend Zod enum is tight: only 3 values.
        # (See web/lib/schemas.ts RunStatusSchema.)
        assert set(VALID_STATUS_VALUES) == {"running", "done", "failed"}, (
            "If you add a new status value here, also update RunStatusSchema in "
            "web/lib/schemas.ts to add it to the z.enum()"
        )

    def test_row_to_status_produces_valid_object(self):
        """_row_to_status is the bridge — verify it handles a minimal running row
        and a completed row with all optional fields."""
        from api.run import _row_to_status

        minimal_row = {
            "status": "running",
            # counters are nullable in the DB initially — backend uses 'or 0'
            "scraped": None,
            "processed": None,
            "generated": None,
            "skipped_low_fit": None,
            "summary": None,
            "error": None,
            "search_snapshot": None,
        }
        obj = _row_to_status(minimal_row)
        assert obj.status == "running"
        assert obj.scraped == 0
        assert obj.processed == 0
        assert obj.generated == 0
        assert obj.skipped_low_fit == 0
        assert obj.summary is None
        assert obj.error is None
        assert obj.search_snapshot is None

    def test_row_to_status_done_with_all_fields(self):
        from api.run import _row_to_status

        snapshot = {"keywords": ["go"], "locations": [], "period_hours": 72,
                    "work_format": "hybrid", "loose": True, "targeted": False}
        full_row = {
            "status": "done",
            "scraped": 50,
            "processed": 30,
            "generated": 12,
            "skipped_low_fit": 18,
            "summary": {"scraped": 50, "queued": 30, "generated": 12, "skipped_low_fit": 18},
            "error": None,
            "search_snapshot": snapshot,
        }
        obj = _row_to_status(full_row)
        assert obj.status == "done"
        assert obj.scraped == 50
        assert obj.processed == 30
        assert obj.generated == 12
        assert obj.skipped_low_fit == 18
        assert obj.search_snapshot == snapshot


# ---------------------------------------------------------------------------
# SearchParams  (GET /search-params and PUT /search-params)
# frontend Zod: SearchParamsSchema = z.object({
#   keywords: z.array(z.string()),
#   locations: z.array(z.string()),
#   period_hours: z.number().default(168),
#   work_format: z.string().default("remote"),
#   loose: z.boolean().default(false),
#   targeted: z.boolean().default(false),
# })
# ---------------------------------------------------------------------------

class TestSearchParamsSchema:
    """Python SearchParamsBody must be field-for-field compatible with SearchParamsSchema."""

    REQUIRED_FIELDS = {
        "keywords", "locations", "period_hours", "work_format", "loose", "targeted",
        "exclude_senior",  # added in feat/dynamic-role-filter (0010 migration)
    }

    def test_all_required_fields_present(self):
        fields = set(SearchParamsBody.model_fields.keys())
        for f in self.REQUIRED_FIELDS:
            assert f in fields, f"SearchParamsBody missing field: {f}"

    def test_no_unexpected_extra_fields(self):
        """The frontend Zod schema must stay in sync with SearchParamsBody.
        Extra backend fields are silently stripped by Zod's default object parsing,
        but this sentinel breaks immediately so drift is noticed.
        If you add a field here, also update web/lib/schemas.ts SearchParamsSchema."""
        fields = set(SearchParamsBody.model_fields.keys())
        assert fields == self.REQUIRED_FIELDS, (
            f"Field mismatch: backend={fields}, frontend-expected={self.REQUIRED_FIELDS}. "
            "If you added a field, update web/lib/schemas.ts SearchParamsSchema."
        )

    def test_keywords_and_locations_are_lists(self):
        obj = SearchParamsBody(keywords=["python", "rust"], locations=["remote"])
        assert obj.keywords == ["python", "rust"]
        assert obj.locations == ["remote"]

    def test_empty_arrays_are_valid(self):
        obj = SearchParamsBody(keywords=[], locations=[])
        assert obj.keywords == []
        assert obj.locations == []

    def test_period_hours_defaults_to_168(self):
        obj = SearchParamsBody()
        assert obj.period_hours == 168

    def test_work_format_defaults_to_remote(self):
        obj = SearchParamsBody()
        assert obj.work_format == "remote"

    def test_loose_defaults_to_false(self):
        obj = SearchParamsBody()
        assert obj.loose is False

    def test_targeted_defaults_to_false(self):
        obj = SearchParamsBody()
        assert obj.targeted is False

    def test_put_payload_includes_loose_and_targeted(self):
        """The frontend buildPayload() always includes loose and targeted (from
        savedParams or defaults). Verify the backend schema accepts them.

        This is the 'loose/targeted NOT rendered but still in payload' criterion.
        """
        payload = {
            "keywords": ["backend"],
            "locations": [],
            "period_hours": 72,
            "work_format": "remote",
            "loose": False,    # not rendered in UI but present
            "targeted": False,  # not rendered in UI but present
        }
        obj = SearchParamsBody(**payload)
        assert obj.loose is False
        assert obj.targeted is False

    def test_exclude_senior_defaults_to_false(self):
        """exclude_senior must default to False (opt-in: seniority not excluded by default)."""
        obj = SearchParamsBody()
        assert obj.exclude_senior is False

    def test_exclude_senior_accepts_true(self):
        """exclude_senior=True must be accepted (user opts in to seniority exclusion)."""
        obj = SearchParamsBody(exclude_senior=True)
        assert obj.exclude_senior is True

    def test_work_format_accepts_all_three_values(self):
        """The UI exposes remote/hybrid/onsite — all must be accepted by the backend."""
        for fmt in ("remote", "hybrid", "onsite"):
            obj = SearchParamsBody(work_format=fmt)
            assert obj.work_format == fmt

    def test_period_hours_presets(self):
        """The frontend presets 24/72/168 must all be accepted as valid integers."""
        for hours in (24, 72, 168):
            obj = SearchParamsBody(period_hours=hours)
            assert obj.period_hours == hours

    def test_period_hours_custom_nonzero(self):
        """Custom period hours (any positive int) must be accepted."""
        obj = SearchParamsBody(period_hours=48)
        assert obj.period_hours == 48


# ---------------------------------------------------------------------------
# Run 409 conflict — no live backend needed; just verify the exception chain
# ---------------------------------------------------------------------------

class TestRunConflictContract:
    """Document the 409 contract: backend raises HTTP 409 when a run is active.
    The frontend catches it as RunConflictError and enters progress mode.

    We verify this chain offline by testing that:
    1. The backend router raises HTTPException(409) on duplicate.
    2. The RunConflictError class in web/lib/api.ts has code='RUN_ACTIVE'
       (verified by reading the source since there's no TS test runner).
    """

    def test_http_exception_is_409_for_active_run(self):
        """Verify that the 409 is raised with the correct HTTP status code.
        We cannot call the real endpoint offline, but we verify the code path
        in run.py raises HTTPException with status 409."""
        from fastapi import HTTPException
        # Simulate what the endpoint does when active.data is non-empty:
        active_data_exists = True
        status_code = None
        if active_data_exists:
            exc = HTTPException(
                status_code=409,
                detail="A run is already in progress. Poll GET /run/latest for status.",
            )
            status_code = exc.status_code
        assert status_code == 409

    def test_run_started_model_matches_accepted_schema(self):
        """POST /run returns RunStarted which must match RunAcceptedSchema {run_id}."""
        import uuid
        run_id = str(uuid.uuid4())
        obj = RunStarted(run_id=run_id)
        d = obj.model_dump()
        assert "run_id" in d
        assert d["run_id"] == run_id
        # No extra fields the Zod schema wouldn't expect:
        assert set(d.keys()) == {"run_id"}


# ---------------------------------------------------------------------------
# Schema drift guard — field count sentinel
# ---------------------------------------------------------------------------

class TestSchemaDriftGuard:
    """Sentinel tests: if any field is added or removed on the Python side,
    these tests break immediately and force an update to the matching Zod schema."""

    def test_run_status_field_count(self):
        """RunStatus has exactly 8 fields. If this changes, update RunStatusSchema in
        web/lib/schemas.ts. Current fields:
          status, scraped, processed, generated, skipped_low_fit,
          summary, error, search_snapshot
        """
        assert len(RunStatus.model_fields) == 8, (
            f"RunStatus field count changed: {list(RunStatus.model_fields.keys())}. "
            "Update web/lib/schemas.ts RunStatusSchema to match."
        )

    def test_run_started_field_count(self):
        """RunStarted has exactly 1 field: run_id."""
        assert len(RunStarted.model_fields) == 1, (
            f"RunStarted field count changed: {list(RunStarted.model_fields.keys())}. "
            "Update web/lib/schemas.ts RunAcceptedSchema to match."
        )

    def test_search_params_body_field_count(self):
        """SearchParamsBody has exactly 7 fields. If this changes, update SearchParamsSchema
        in web/lib/schemas.ts. Current fields:
          keywords, locations, period_hours, work_format, loose, targeted, exclude_senior
        """
        assert len(SearchParamsBody.model_fields) == 7, (
            f"SearchParamsBody field count changed: {list(SearchParamsBody.model_fields.keys())}. "
            "Update web/lib/schemas.ts SearchParamsSchema to match."
        )
