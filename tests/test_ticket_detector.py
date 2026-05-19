"""Phase Y2: ticket classifier."""
from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("TICKET_CONFIDENCE_MIN", raising=False)
    monkeypatch.delenv("TICKET_MAX_INPUT_CHARS", raising=False)
    # Reset router singleton between tests so monkeypatched chat sticks.
    import communication.ticket_detector as td
    td._router_singleton = None
    yield


class _FakeResult:
    def __init__(self, text: str):
        self.text = text
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.model = "fake"
        self.provider = "fake"


def _patch_chat(monkeypatch, text: str):
    import communication.ticket_detector as td

    class _FakeRouter:
        def chat(self, *a, **kw):
            return _FakeResult(text)

    monkeypatch.setattr(td, "_router", lambda: _FakeRouter())


def test_classify_recognises_ticket(monkeypatch):
    _patch_chat(monkeypatch, json.dumps({
        "ticket": True, "summary": "Fix the login bug", "confidence": 0.9
    }))
    from communication.ticket_detector import classify

    out = classify("Hey can someone fix the login bug? users can't get in")
    assert out.ticket is True
    assert out.summary == "Fix the login bug"
    assert out.confidence == pytest.approx(0.9)


def test_classify_rejects_chatter(monkeypatch):
    _patch_chat(monkeypatch, json.dumps({
        "ticket": False, "summary": "", "confidence": 0.95
    }))
    from communication.ticket_detector import classify

    assert classify("anyone want lunch?").ticket is False


def test_classify_floors_low_confidence(monkeypatch):
    _patch_chat(monkeypatch, json.dumps({
        "ticket": True, "summary": "maybe a bug", "confidence": 0.4
    }))
    from communication.ticket_detector import classify

    out = classify("the page sometimes does the thing")
    # Below default floor (0.6), so we override ticket → False.
    assert out.ticket is False
    assert out.confidence == pytest.approx(0.4)


def test_classify_floor_env_override(monkeypatch):
    monkeypatch.setenv("TICKET_CONFIDENCE_MIN", "0.3")
    _patch_chat(monkeypatch, json.dumps({
        "ticket": True, "summary": "bug", "confidence": 0.4
    }))
    from communication.ticket_detector import classify

    assert classify("vague bug").ticket is True


def test_classify_json_in_code_fence(monkeypatch):
    _patch_chat(monkeypatch, '```json\n{"ticket": true, "summary": "do x", "confidence": 0.9}\n```')
    from communication.ticket_detector import classify

    out = classify("please do x")
    assert out.ticket is True and out.summary == "do x"


def test_classify_empty_input_short_circuits(monkeypatch):
    """Empty / whitespace input doesn't call the model."""
    called = {"n": 0}

    class _FakeRouter:
        def chat(self, *a, **kw):
            called["n"] += 1
            return _FakeResult("{}")

    import communication.ticket_detector as td
    monkeypatch.setattr(td, "_router", lambda: _FakeRouter())

    out = td.classify("   ")
    assert out.ticket is False
    assert called["n"] == 0


def test_classify_swallows_router_error(monkeypatch):
    class _BoomRouter:
        def chat(self, *a, **kw):
            raise RuntimeError("network down")

    import communication.ticket_detector as td
    monkeypatch.setattr(td, "_router", lambda: _BoomRouter())

    out = td.classify("fix the thing please")
    assert out.ticket is False
    assert out.confidence == 0.0


def test_classify_swallows_bad_json(monkeypatch):
    _patch_chat(monkeypatch, "not json at all")
    from communication.ticket_detector import classify

    out = classify("fix it")
    assert out.ticket is False
