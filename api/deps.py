"""Dependency wiring: build the backend singletons and hand them to routes
through FastAPI ``Depends``.

Config comes from the environment only:
  * SUPABASE_URL / SUPABASE_SECRET_KEY  — read by make_supabase_client()
  * ANTHROPIC_API_KEY                   — read by the core's AnthropicClient

Singletons are created lazily on first request and cached (``lru_cache``), so the
process holds one Supabase client / one store pair / one LLM client. Secrets are
never logged.
"""

from __future__ import annotations

from functools import lru_cache

from jobsearch.models import PlatformConfig
from jobsearch.scoring import AnthropicClient
from jobsearch.supabase_store import (
    SupabaseJobStore,
    SupabaseUserState,
    make_supabase_client,
)


@lru_cache(maxsize=1)
def get_supabase():
    """The shared Supabase client (service_role key, bypasses RLS)."""
    return make_supabase_client()


@lru_cache(maxsize=1)
def get_job_store() -> SupabaseJobStore:
    """Shared vacancy pool (platform-wide JobStore)."""
    return SupabaseJobStore(get_supabase())


@lru_cache(maxsize=1)
def get_user_state() -> SupabaseUserState:
    """Per-user personal state (UserState)."""
    return SupabaseUserState(get_supabase())


@lru_cache(maxsize=1)
def get_llm() -> AnthropicClient:
    """Real LLM client; reads ANTHROPIC_API_KEY from the environment itself."""
    return AnthropicClient()


@lru_cache(maxsize=1)
def get_config() -> PlatformConfig:
    """Platform config (models, thresholds). Defaults for now."""
    return PlatformConfig()


def get_scraper():
    """The scrape callable ``scrape(params, config) -> list[Job]``. Returned as a
    dependency (not called) so tests can override it with a fake — no network."""
    from jobsearch.sources import scrape
    return scrape
