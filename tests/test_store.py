"""Flat-file store tests: seen-catalog roundtrip, processed roundtrip, the exact
applications.csv format (no user_id column), and the no-cross-user-read seam.
"""

import csv

from jobsearch.store import FlatFileJobStore, FlatFileUserState, UserState, LOCAL_USER
from jobsearch.models import Job, MatchResult


def _job(key="acme|supporteng", url="https://x/1"):
    return Job(dedup_key=key, source="LinkedIn", url=url, company="Acme",
               title="Support Engineer", location="Remote", region="WORLDWIDE", description="d")


def _res():
    return MatchResult(fit_score=72, b2b="yes", reason="r", jd_keywords=[], ats_present=[],
                       ats_missing=[], tailored_summary="", tailored_skills=[], gaps="",
                       recruiter_verdict="", cover_letter="")


def test_jobstore_seen_roundtrip(tmp_path):
    p = tmp_path / "seen.txt"
    store = FlatFileJobStore(str(p))
    j = _job()
    assert store.has_seen(j.dedup_key) is False
    store.save([j])
    assert store.has_seen(j.dedup_key) is True
    # persisted across instances
    assert FlatFileJobStore(str(p)).has_seen(j.dedup_key) is True


def test_jobstore_dedup_on_insert(tmp_path):
    p = tmp_path / "seen.txt"
    store = FlatFileJobStore(str(p))
    j = _job()
    store.save([j])
    store.save([j])
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines.count(j.dedup_key) == 1


def test_userstate_processed_roundtrip(tmp_path):
    st = FlatFileUserState(str(tmp_path / "processed.txt"), str(tmp_path / "apps.csv"))
    url = "https://x/job"
    assert st.is_processed(LOCAL_USER, url) is False
    st.mark_processed(LOCAL_USER, url)
    assert st.is_processed(LOCAL_USER, url) is True


def test_applications_csv_keeps_12_columns(tmp_path):
    ap = tmp_path / "apps.csv"
    st = FlatFileUserState(str(tmp_path / "processed.txt"), str(ap))
    st.save_application(LOCAL_USER, _job(), _res(), "review/072_Acme_Support")
    with open(ap, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    assert len(header) == 12
    assert "user_id" not in header
    assert header[:3] == ["date_generated", "fit", "b2b"]
    assert len(rows) == 2  # header + one record


def test_list_applications_roundtrip(tmp_path):
    ap = tmp_path / "apps.csv"
    st = FlatFileUserState(str(tmp_path / "processed.txt"), str(ap))
    assert st.list_applications(LOCAL_USER) == []  # no file yet
    st.save_application(LOCAL_USER, _job(), _res(), "review/072_Acme_Support")
    rows = st.list_applications(LOCAL_USER)
    assert len(rows) == 1
    assert rows[0]["company"] == "Acme"
    assert rows[0]["status"] == "GENERATED"


def test_delete_user_data_clears_processed_and_applications(tmp_path):
    pp = tmp_path / "processed.txt"
    ap = tmp_path / "apps.csv"
    st = FlatFileUserState(str(pp), str(ap))
    st.mark_processed(LOCAL_USER, "https://x/job")
    st.save_application(LOCAL_USER, _job(), _res(), "review/072_Acme_Support")
    assert pp.exists() and ap.exists()

    st.delete_user_data(LOCAL_USER)

    assert not pp.exists()
    assert not ap.exists()
    assert st.is_processed(LOCAL_USER, "https://x/job") is False
    assert st.list_applications(LOCAL_USER) == []


def test_userstate_has_no_cross_user_bulk_read():
    # Security seam: UserState must not expose a user_id-less bulk read.
    forbidden = {"read_all", "all", "list_all", "dump", "export_all", "all_applications"}
    assert not (set(dir(FlatFileUserState)) & forbidden)
    assert not (set(dir(UserState)) & forbidden)
