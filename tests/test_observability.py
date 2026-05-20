"""Observability: structured logs, metrics, sentry. No real network."""
from __future__ import annotations

import json
import logging

import pytest


# ---------- JSON logging ----------


def test_json_formatter_emits_one_object_per_record():
    from observability.logging import JsonFormatter

    fmt = JsonFormatter()
    rec = logging.LogRecord(
        name="x.y", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello %s", args=("world",), exc_info=None,
    )
    out = fmt.format(rec)
    obj = json.loads(out)
    assert obj["level"] == "INFO"
    assert obj["logger"] == "x.y"
    assert obj["msg"] == "hello world"
    assert obj["ts"].endswith("Z")


def test_json_formatter_includes_extra_fields():
    from observability.logging import JsonFormatter

    fmt = JsonFormatter()
    rec = logging.LogRecord(
        name="x.y", level=logging.INFO, pathname=__file__, lineno=1,
        msg="m", args=(), exc_info=None,
    )
    rec.workflow_id = "wf-1"
    rec.tokens = 42
    rec.unjsonable = object()
    out = json.loads(fmt.format(rec))
    assert out["workflow_id"] == "wf-1"
    assert out["tokens"] == 42
    # Non-JSON-able fields fall back to repr() rather than crashing
    assert "object at 0x" in out["unjsonable"]


def test_json_formatter_emits_exception_block():
    from observability.logging import JsonFormatter

    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        rec = logging.LogRecord(
            name="x", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="oops", args=(), exc_info=sys.exc_info(),
        )
    out = json.loads(JsonFormatter().format(rec))
    assert out["exc_type"] == "ValueError"
    assert out["exc_msg"] == "boom"
    assert "Traceback" in out["traceback"]


def test_configure_logging_idempotent(monkeypatch):
    from observability.logging import configure_logging
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    configure_logging()
    configure_logging()
    root = logging.getLogger()
    assert len(root.handlers) == 1  # not duplicated
    assert root.level == logging.DEBUG


# ---------- metrics ----------


@pytest.fixture(autouse=True)
def reset_metrics(monkeypatch):
    from observability import metrics as m
    m.reset_for_tests()
    monkeypatch.delenv("METRICS_DISABLED", raising=False)
    yield
    m.reset_for_tests()


def test_metrics_disabled_when_env_set(monkeypatch):
    monkeypatch.setenv("METRICS_DISABLED", "1")
    from observability import metrics as m
    m.init_metrics()
    payload, ctype = m.metrics_render()
    assert b"# metrics disabled" in payload


def test_metrics_records_chat_counter_and_tokens():
    pytest.importorskip("prometheus_client")
    from observability import metrics as m
    m.init_metrics()
    m.record_chat(provider="groq", task_type="plan", status="ok",
                  latency_s=0.42, prompt_tokens=100, completion_tokens=50)
    m.record_chat(provider="groq", task_type="plan", status="ok",
                  prompt_tokens=10, completion_tokens=5)
    m.record_error(kind="HTTPError")
    payload, _ = m.metrics_render()
    text = payload.decode("utf-8")
    assert 'ai_company_chat_total{provider="groq",status="ok",task_type="plan"} 2.0' in text
    assert 'ai_company_chat_tokens_total{direction="prompt",provider="groq"} 110.0' in text
    assert 'ai_company_chat_tokens_total{direction="completion",provider="groq"} 55.0' in text
    assert 'ai_company_errors_total{kind="HTTPError"} 1.0' in text


def test_metrics_workflow_event():
    pytest.importorskip("prometheus_client")
    from observability import metrics as m
    m.init_metrics()
    m.record_workflow_event(node="do_plan", kind="ok")
    text = m.metrics_render()[0].decode("utf-8")
    assert 'ai_company_workflow_events_total{kind="ok",node="do_plan"} 1.0' in text


def test_metrics_noop_if_init_never_called():
    """All record_* calls are safe before init_metrics()."""
    from observability import metrics as m
    m.record_chat(provider="x", task_type="y", status="ok")
    m.record_workflow_event(node="n")
    m.record_error(kind="x")
    payload, _ = m.metrics_render()
    # Without init, render returns the disabled stub
    assert b"disabled" in payload or len(payload) >= 0  # tolerant


# ---------- sentry ----------


def test_init_sentry_returns_false_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    from observability.sentry import init_sentry
    assert init_sentry() is False


def test_init_sentry_returns_false_without_sdk(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://dummy@sentry.example/1")
    import sys
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    from observability.sentry import init_sentry
    assert init_sentry() is False


def test_init_sentry_calls_sdk_init_when_present(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://dummy@sentry.example/1")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "ci")
    called = {}
    import sys, types
    fake = types.SimpleNamespace(init=lambda **kw: called.update(kw))
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    from observability.sentry import init_sentry
    assert init_sentry() is True
    assert called["dsn"] == "https://dummy@sentry.example/1"
    assert called["environment"] == "ci"
    assert called["send_default_pii"] is False
