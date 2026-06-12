"""Storage seams.

Two *separate* interfaces, because they are two different product layers:

* ``JobStore``  — the global vacancy catalog + dedup (was ``.seen_jobs.txt``).
  One per platform. Dedup-on-insert lives here; the keying logic stays pure in
  filters.compute_dedup_key.
* ``UserState`` — one user's personal state (was ``.processed_urls.txt`` and
  ``applications.csv``). Every personal method takes ``user_id`` already, so the
  migration to a DB becomes "swap the implementation" with no call-site changes.

Security: ``UserState`` has NO method that reads data without a ``user_id`` — no
"read everything across users". That absence is the seam that becomes Postgres
Row-Level Security later. Today both impls are flat files, but the contracts do
not mix.
"""

from __future__ import annotations

import csv
import datetime
import os
from typing import Protocol

from .models import Job, MatchResult

# Один пользователь в текущем (локальном) режиме. В продукте — реальный id из auth.
LOCAL_USER = "local"

SEEN_LOG = ".seen_jobs.txt"
PROCESSED_LOG = ".processed_urls.txt"
APPLICATIONS_CSV = "applications.csv"


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------
class JobStore(Protocol):
    """Global vacancy catalog + dedup. Platform-wide, not per-user."""
    def has_seen(self, dedup_key: str) -> bool: ...
    def save(self, jobs: list) -> None: ...


class UserState(Protocol):
    """Per-user personal state. Every method is scoped by user_id; there is no
    cross-user read by contract."""
    def is_processed(self, user_id: str, dedup_key: str) -> bool: ...
    def mark_processed(self, user_id: str, dedup_key: str) -> None: ...
    def save_application(self, user_id: str, job: Job, res: MatchResult, folder: str) -> None: ...


# ---------------------------------------------------------------------------
# Flat-file implementations
# ---------------------------------------------------------------------------
class FlatFileJobStore:
    """JobStore backed by a flat file of dedup keys (legacy .seen_jobs.txt)."""

    def __init__(self, path: str = SEEN_LOG):
        self.path = path
        self._cache = self._load()

    def _load(self) -> set:
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                return {ln.strip() for ln in f if ln.strip()}
        return set()

    def has_seen(self, dedup_key: str) -> bool:
        return dedup_key in self._cache

    def save(self, jobs: list) -> None:
        # Dedup-on-insert: only persist keys we haven't recorded yet.
        new = [j for j in jobs if j.dedup_key and j.dedup_key not in self._cache]
        if not new:
            return
        with open(self.path, "a", encoding="utf-8") as f:
            for j in new:
                f.write(j.dedup_key + "\n")
                self._cache.add(j.dedup_key)


class FlatFileUserState:
    """UserState backed by flat files. Single local user, so user_id is accepted
    (the seam) but not serialised — the file formats stay exactly as before. A
    future DB impl keys every row on user_id and enforces RLS."""

    def __init__(self, processed_path: str = PROCESSED_LOG, applications_path: str = APPLICATIONS_CSV):
        self.processed_path = processed_path
        self.applications_path = applications_path
        self._processed = self._load_processed()

    def _load_processed(self) -> set:
        if os.path.exists(self.processed_path):
            with open(self.processed_path, encoding="utf-8") as f:
                return {ln.strip() for ln in f if ln.strip()}
        return set()

    def is_processed(self, user_id: str, dedup_key: str) -> bool:
        return dedup_key in self._processed

    def mark_processed(self, user_id: str, dedup_key: str) -> None:
        with open(self.processed_path, "a", encoding="utf-8") as f:
            f.write(dedup_key + "\n")
        self._processed.add(dedup_key)

    def save_application(self, user_id: str, job: Job, res: MatchResult, folder: str) -> None:
        """Дописывает строку в applications.csv — воронка: сгенерировано -> отклик -> ответ."""
        new = not os.path.exists(self.applications_path)
        with open(self.applications_path, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["date_generated", "fit", "b2b", "region", "company", "title",
                            "source", "url", "folder", "status", "applied_date", "response"])
            w.writerow([
                datetime.date.today().isoformat(), res.fit_score, res.b2b, job.region,
                job.company, job.title, job.source, job.url, folder,
                "GENERATED", "", "",
            ])
