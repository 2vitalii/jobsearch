"""Unit tests for suggest_search_roles in jobsearch.cv.

Pure function tests using a fake LLMClient — no network, no API keys needed.
"""

from __future__ import annotations

import json
import os
import pytest

from jobsearch.cv import suggest_search_roles
from jobsearch.models import PlatformConfig


# ---------------------------------------------------------------------------
# Fake LLMClient
# ---------------------------------------------------------------------------

class FakeLLM:
    """Fake LLMClient that returns a pre-configured response string."""

    def __init__(self, response: str) -> None:
        self._response = response

    def complete(self, *, model: str, system: str, messages: list, max_tokens: int) -> str:  # noqa: ARG002
        return self._response


def _config() -> PlatformConfig:
    return PlatformConfig(
        model_tailor="claude-3-5-sonnet-20241022",
        model_score="claude-3-5-haiku-20241022",
    )


# ---------------------------------------------------------------------------
# Parsing and happy-path tests
# ---------------------------------------------------------------------------

def test_parses_json_array():
    """A well-formed JSON array is parsed and returned."""
    roles = ["Technical Support Engineer", "Implementation Specialist", "Project Coordinator"]
    llm = FakeLLM(json.dumps(roles))
    result = suggest_search_roles("MASTER CV TEXT", llm, _config())
    assert result == roles


def test_returns_ordered_list():
    """Roles are returned in the order provided by the LLM."""
    roles = ["Role A", "Role B", "Role C", "Role D", "Role E"]
    llm = FakeLLM(json.dumps(roles))
    result = suggest_search_roles("CV", llm, _config())
    assert result == roles


def test_strips_and_drops_empty_strings():
    """Items that are empty or whitespace-only are filtered out."""
    roles = ["Support Engineer", "", "  ", "Customer Success Manager"]
    llm = FakeLLM(json.dumps(roles))
    result = suggest_search_roles("CV", llm, _config())
    assert result == ["Support Engineer", "Customer Success Manager"]


def test_caps_at_8_roles():
    """At most 8 roles are returned even if LLM returns more."""
    roles = [f"Role {i}" for i in range(15)]
    llm = FakeLLM(json.dumps(roles))
    result = suggest_search_roles("CV", llm, _config())
    assert len(result) == 8
    assert result == roles[:8]


def test_strips_whitespace_from_items():
    """Leading/trailing whitespace on individual role strings is stripped."""
    roles = ["  Technical Support Engineer  ", "Project Coordinator\t"]
    llm = FakeLLM(json.dumps(roles))
    result = suggest_search_roles("CV", llm, _config())
    assert result == ["Technical Support Engineer", "Project Coordinator"]


# ---------------------------------------------------------------------------
# Robustness / fallback tests
# ---------------------------------------------------------------------------

def test_returns_empty_list_on_garbage_response():
    """Completely unparseable response → []."""
    llm = FakeLLM("This is not JSON at all.")
    result = suggest_search_roles("CV", llm, _config())
    assert result == []


def test_returns_empty_list_on_json_object_not_list():
    """A JSON object (not array) → [] (wrong shape)."""
    llm = FakeLLM(json.dumps({"roles": ["Support Engineer"]}))
    result = suggest_search_roles("CV", llm, _config())
    assert result == []


def test_returns_empty_list_on_empty_array():
    """Empty array from LLM → []."""
    llm = FakeLLM("[]")
    result = suggest_search_roles("CV", llm, _config())
    assert result == []


def test_handles_json_with_code_fence():
    """JSON wrapped in ```json ... ``` code fences is handled correctly."""
    roles = ["Support Engineer", "Project Coordinator"]
    raw = f"```json\n{json.dumps(roles)}\n```"
    llm = FakeLLM(raw)
    result = suggest_search_roles("CV", llm, _config())
    assert result == roles


def test_ignores_non_string_items_in_array():
    """Non-string items in the array are silently ignored."""
    raw = json.dumps(["Technical Support Engineer", 42, None, "Project Coordinator"])
    llm = FakeLLM(raw)
    result = suggest_search_roles("CV", llm, _config())
    assert result == ["Technical Support Engineer", "Project Coordinator"]


# ---------------------------------------------------------------------------
# Live test (skipped when ANTHROPIC_API_KEY is absent)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.path.exists("/Users/vitaliivlasov/Desktop/jobsearch/master_cv.md")
    or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set or master_cv.md not present — skipping live test",
)
def test_live_suggest_roles_are_job_titles_not_skills():
    """Live test: roles returned are job titles, not bare skills/technologies.

    This test is skipped unless ANTHROPIC_API_KEY is set AND master_cv.md exists.
    It verifies the honesty invariant: returned items look like role names, not
    tool/technology strings.
    """
    from jobsearch.scoring import AnthropicClient
    from jobsearch.config import load_platform_config

    with open("/Users/vitaliivlasov/Desktop/jobsearch/master_cv.md") as f:
        cv_text = f.read()

    config = load_platform_config()
    llm = AnthropicClient()
    roles = suggest_search_roles(cv_text, llm, config)

    print(f"\nLIVE: suggest_search_roles returned {len(roles)} roles:")
    for role in roles:
        print(f"  - {role}")

    # Must return at least 1 role.
    assert len(roles) >= 1

    # Block-list of known bare skill/tech tokens (lower-cased).
    TECH_TOKEN_BLOCKLIST = {
        "sql", "python", "azure", "aws", "mqtt", "git", "docker",
        "kubernetes", "java", "javascript", "typescript", "react", "node",
        "postgres", "postgresql", "redis", "mongodb", "kafka", "terraform",
        "ansible", "linux", "bash", "excel", "powerpoint", "jira", "confluence",
    }

    for role in roles:
        lower = role.strip().lower()
        assert lower not in TECH_TOKEN_BLOCKLIST, (
            f"'{role}' looks like a bare skill/technology, not a job title"
        )
        # A role should have at least 2 words (job titles are multi-word).
        words = lower.split()
        assert len(words) >= 2, (
            f"'{role}' looks too short to be a job title (single word)"
        )
