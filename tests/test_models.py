"""Tests for the result dataclasses' from_dict mapping (LLM JSON -> typed)."""

from jobsearch.models import PreScore, MatchResult


def test_prescore_from_dict_defaults_and_mapping():
    empty = PreScore.from_dict({})
    assert empty.fit_score == 0 and empty.b2b == "" and empty.reason == ""

    ps = PreScore.from_dict({"fit_score": "55", "b2b_eligible": "maybe", "reason": "r"})
    assert ps.fit_score == 55          # coerced from str
    assert ps.b2b == "maybe"           # mapped from b2b_eligible


def test_matchresult_from_dict_maps_and_defaults():
    mr = MatchResult.from_dict({"fit_score": 90, "b2b_eligible": "yes", "cover_letter": "C"})
    assert mr.fit_score == 90
    assert mr.b2b == "yes"
    assert mr.cover_letter == "C"
    # missing list fields default to empty lists, not None
    assert mr.jd_keywords == [] and mr.tailored_skills == [] and mr.ats_missing == []
