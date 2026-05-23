"""Root pytest conftest.

Scrubs every Phase-Z opt-in env var BEFORE each test runs. Reason: the
project's own `.env` now enables structured outputs / learned prompts /
litellm primacy / auto-tune by default for production use. Tests must be
hermetic — they stub `_chat`, route to fake providers, and assert byte-
exact behavior. Leaving production flags on at test time would short-
circuit those stubs through the real structured/litellm code paths.

Individual tests that exercise these features re-enable the flag they
need via `monkeypatch.setenv(...)`. Default-off here is the canonical
test environment.
"""
from __future__ import annotations

import pytest

_OPTIN_ENV_VARS = (
    # Persistent state
    "LANGGRAPH_CHECKPOINT_DB",
    # Self-improvement
    "USE_LEARNED_PROMPTS",
    "LEARNED_PROMPTS_PATH",
    "USE_LEARNED_MODELS",
    "BEST_MODEL_MAX_COST_PER_CALL",
    # Auto-tune
    "AUTO_TUNE_MIN_TOTAL",
    "AUTO_TUNE_MIN_NEW",
    "AUTO_TUNE_STATE_PATH",
    "AUTO_TUNE_TASK_TYPES",
    # Litellm primacy
    "LITELLM_PRIMARY",
    "LITELLM_DEFAULT_MODEL",
    "LITELLM_TASK_MODEL_ANALYZE",
    "LITELLM_TASK_MODEL_PLAN",
    "LITELLM_TASK_MODEL_CODE",
    "LITELLM_TASK_MODEL_REVIEW",
    "LITELLM_TASK_MODEL_SIMPLE",
    "LITELLM_TASK_MODEL_SUMMARIZE",
    "LITELLM_TASK_MODEL_TEST",
    # Structured outputs
    "USE_STRUCTURED_OUTPUTS",
    "STRUCTURED_MODEL",
    # Crew (legacy, already test-scrubbed individually — belt-and-braces)
    "CREW_MODE",
)


@pytest.fixture(autouse=True)
def _scrub_optin_flags(monkeypatch):
    for name in _OPTIN_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield
