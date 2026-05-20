"""Prometheus metrics — optional.

prometheus_client is NOT in requirements.txt; the module gracefully no-ops
when it isn't installed so the rest of the app keeps working.

Counters:
  ai_company_chat_total{provider, task_type, status}
  ai_company_workflow_events_total{node, kind}
  ai_company_errors_total{kind}

Histograms:
  ai_company_chat_latency_seconds{provider, task_type}
  ai_company_chat_tokens_total{provider, direction}  (prompt|completion)

Render via metrics_render() — the api/server.py /v1/metrics route uses it.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Histogram,
        generate_latest,
    )
    METRICS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when prom client missing
    METRICS_AVAILABLE = False
    CONTENT_TYPE_LATEST = "text/plain; charset=utf-8"


_registry = None
_chat_counter = None
_workflow_counter = None
_error_counter = None
_chat_latency = None
_chat_tokens = None


def init_metrics() -> None:
    """Idempotent. Skips silently if prometheus_client is missing or metrics off."""
    global _registry, _chat_counter, _workflow_counter, _error_counter
    global _chat_latency, _chat_tokens

    if not METRICS_AVAILABLE:
        return
    if _registry is not None:
        return
    if os.getenv("METRICS_DISABLED", "").lower() in ("1", "true", "yes"):
        return

    _registry = CollectorRegistry()
    _chat_counter = Counter(
        "ai_company_chat_total", "LLM chat completions",
        ["provider", "task_type", "status"], registry=_registry,
    )
    _workflow_counter = Counter(
        "ai_company_workflow_events_total", "Graph node events",
        ["node", "kind"], registry=_registry,
    )
    _error_counter = Counter(
        "ai_company_errors_total", "Recorded errors",
        ["kind"], registry=_registry,
    )
    _chat_latency = Histogram(
        "ai_company_chat_latency_seconds", "Chat round-trip latency",
        ["provider", "task_type"], registry=_registry,
    )
    _chat_tokens = Counter(
        "ai_company_chat_tokens_total", "Tokens billed",
        ["provider", "direction"], registry=_registry,
    )


def record_chat(*, provider: str, task_type: str, status: str,
                latency_s: float = 0.0,
                prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    if not METRICS_AVAILABLE or _registry is None:
        return
    _chat_counter.labels(provider, task_type, status).inc()
    if latency_s > 0:
        _chat_latency.labels(provider, task_type).observe(latency_s)
    if prompt_tokens:
        _chat_tokens.labels(provider, "prompt").inc(prompt_tokens)
    if completion_tokens:
        _chat_tokens.labels(provider, "completion").inc(completion_tokens)


def record_workflow_event(*, node: str, kind: str = "ok") -> None:
    if not METRICS_AVAILABLE or _registry is None:
        return
    _workflow_counter.labels(node, kind).inc()


def record_error(*, kind: str) -> None:
    if not METRICS_AVAILABLE or _registry is None:
        return
    _error_counter.labels(kind).inc()


def metrics_render() -> tuple[bytes, str]:
    """Return (payload, content_type) for the /v1/metrics endpoint."""
    if not METRICS_AVAILABLE or _registry is None:
        return b"# metrics disabled (prometheus_client not installed or METRICS_DISABLED=1)\n", "text/plain"
    return generate_latest(_registry), CONTENT_TYPE_LATEST


def reset_for_tests() -> None:
    global _registry, _chat_counter, _workflow_counter, _error_counter
    global _chat_latency, _chat_tokens
    _registry = None
    _chat_counter = _workflow_counter = _error_counter = None
    _chat_latency = _chat_tokens = None
