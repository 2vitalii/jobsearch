"""Render tests: in-memory docx bytes, master-CV parsing, package assembly, and
the safe_name path-traversal guard.
"""

from jobsearch import render
from jobsearch.models import Job, MatchResult

CV = """# Vitalii Vlasov
Support & Integration Engineer
contact@example.com

## Professional Summary
Old summary line.

## Core Skills
- SQL
- REST API

## Experience
### Axxonsoft
Support Engineer
- Did things
"""


def _res(summary="New tailored summary.", skills=None):
    return MatchResult(fit_score=80, b2b="yes", reason="r", jd_keywords=["sql"],
                       ats_present=["api"], ats_missing=["k8s"], tailored_summary=summary,
                       tailored_skills=skills or ["Backend: SQL, REST API"], gaps="g",
                       recruiter_verdict="shortlist", cover_letter="Dear team,\n")


def _job():
    return Job(dedup_key="k", source="s", url="https://x", company="Acme",
               title="Support Engineer", location="Remote", region="WORLDWIDE", description="d")


def test_safe_name_blocks_path_separators():
    out = render.safe_name("../../etc/passwd")
    assert "/" not in out and "\\" not in out and ".." not in out
    out2 = render.safe_name("a/b\\c..d")
    assert "/" not in out2 and "\\" not in out2


def test_parse_master_extracts_name_and_sections():
    name, extras, sections = render.parse_master(CV)
    assert name == "Vitalii Vlasov"
    titles = [s["title"] for s in sections]
    assert "Professional Summary" in titles
    assert "Core Skills" in titles


def test_render_cv_returns_docx_bytes():
    name, extras, sections = render.parse_master(CV)
    data = render.render_cv(name, extras, sections)
    assert isinstance(data, (bytes, bytearray))
    assert data[:2] == b"PK"        # .docx is a zip container


def test_build_package_in_memory():
    pkg = render.build_package(_job(), _res(), CV)
    assert pkg.cv_docx[:2] == b"PK"
    assert pkg.cover_letter.startswith("Dear team,")
    assert "Support Engineer" in pkg.ats_report
    assert "Fit: **80/100**" in pkg.ats_report
