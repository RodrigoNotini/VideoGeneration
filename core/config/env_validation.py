"""Phase-aware environment validation."""

from __future__ import annotations

import os
from typing import Mapping


def required_env_vars_for_phase(phase: int, openai_api_key_var: str) -> list[str]:
    """Return required env vars for a given phase.

    Phase 0 and 1 require no secrets. OpenAI key starts at phase 2.
    """
    if phase <= 1:
        return []
    return [openai_api_key_var]


def validate_environment(
    *,
    phase: int,
    openai_api_key_var: str,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Validate required environment variables for current phase.

    Returns a list of missing variable names.
    """
    active_env = env if env is not None else os.environ
    required = required_env_vars_for_phase(phase, openai_api_key_var)
    return [name for name in required if not str(active_env.get(name, "")).strip()]
