"""Tests for dedup keying and in-run dedup."""

from jobsearch import filters
from jobsearch.models import Job


def test_compute_dedup_key_normalizes_company_and_title():
    k1 = filters.compute_dedup_key("Acme Inc.", "Support Engineer", "u1")
    k2 = filters.compute_dedup_key("ACME  inc", "support  engineer!", "u2")
    assert k1 == k2                       # url ignored when company/title present
    assert k1 == "acmeinc|supportengineer"


def test_compute_dedup_key_falls_back_to_url():
    assert filters.compute_dedup_key("", "", "https://X/Job") == "https://x/job"


def _job(key):
    return Job(dedup_key=key, source="s", url="u" + key, company="c", title="t",
               location="l", region="WORLDWIDE", description="d")


def test_dedupe_keeps_first_of_each_key():
    jobs = [_job("a"), _job("a"), _job("b")]
    out = filters.dedupe(jobs)
    assert [j.dedup_key for j in out] == ["a", "b"]
