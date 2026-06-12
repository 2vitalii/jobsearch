"""Platform configuration assembly.

Builds a PlatformConfig from environment/defaults. SECRETS ARE NOT HANDLED HERE
— the Anthropic API key is read directly from the environment by the LLM client
(scoring.AnthropicClient), never stored on a config object that could end up in a
log or repr.
"""

from __future__ import annotations

import os

from .models import PlatformConfig


def load_platform_config() -> PlatformConfig:
    """Assemble PlatformConfig. Only MAX_JOBS is environment-overridable today,
    matching the original prototype; the rest use the dataclass defaults."""
    cfg = PlatformConfig()
    max_jobs = os.environ.get("MAX_JOBS")
    if max_jobs:
        cfg.max_jobs = int(max_jobs)
    return cfg
