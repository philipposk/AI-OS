"""OpenHandsRole unit tests — no real OpenHands, no real LLM."""
from __future__ import annotations

import os
import json
import urllib.error

import pytest

from orchestrator.crew.roles import OpenHandsRole, Coder, RoleResult


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("CREW_OPENHANDS", raising=False)
    monkeypatch.delenv("OPENHANDS_API_URL", raising=False)
    monkeypatch.delenv("OPENHANDS_API_KEY", raising=False)


def test_disabled_by_default():
    assert OpenHandsRole.enabled() is False


def test_enabled_when_env_set(monkeypatch):
    monkeypatch.setenv("CREW_OPENHANDS", "true")
    assert OpenHandsRole.enabled() is True


def test_falls_back_to_coder_when_disabled(monkeypatch):
    """When CREW_OPENHANDS not set, respond() delegates to Coder with stub router."""
    from router.base import ChatResult
    import orchestrator.crew.roles as roles_mod

    class FakeRouter:
        def chat(self, msgs, **kw):
            return ChatResult(text='{"a.py": "print(1)"}',
                              model="stub", provider="stub",
                              prompt_tokens=1, completion_tokens=1)

    role = OpenHandsRole(FakeRouter())
    res = role.respond("do x")
    assert res.role == "coder"   # fell back to Coder
    assert "a.py" in res.content


def test_falls_back_to_coder_on_network_error(monkeypatch):
    """When CREW_OPENHANDS=true but server unreachable, fall back to Coder."""
    monkeypatch.setenv("CREW_OPENHANDS", "true")
    monkeypatch.setenv("OPENHANDS_API_URL", "http://localhost:19999")  # nothing there

    from router.base import ChatResult
    class FakeRouter:
        def chat(self, msgs, **kw):
            return ChatResult(text='{"fallback": true}',
                              model="stub", provider="stub",
                              prompt_tokens=1, completion_tokens=1)

    role = OpenHandsRole(FakeRouter())
    res = role.respond("do y", workflow_id="wf1")
    # Should have fallen back — role is coder, not openhands
    assert res.role == "coder"


def test_call_openhands_success(monkeypatch):
    """Successful OpenHands round-trip via stubbed urllib.request.urlopen."""
    monkeypatch.setenv("CREW_OPENHANDS", "true")
    monkeypatch.setenv("OPENHANDS_API_URL", "http://fake-oh:3000")
    monkeypatch.setenv("OPENHANDS_API_KEY", "tok123")

    call_count = [0]

    class FakeResp:
        def __init__(self, data): self._data = data
        def read(self): return json.dumps(self._data).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout=None):
        call_count[0] += 1
        url = req.full_url
        if "/api/conversations" == url[len("http://fake-oh:3000"):] and req.method == "POST":
            return FakeResp({"conversation_id": "conv42"})
        if "/api/conversations/conv42" in url:
            return FakeResp({"status": "finished", "result": "patched a.py"})
        raise AssertionError(f"unexpected url: {url}")

    import urllib.request as ur_mod
    monkeypatch.setattr(ur_mod, "urlopen", fake_urlopen)
    # Speed up poll
    monkeypatch.setattr(OpenHandsRole, "_POLL_INTERVAL", 0)

    role = OpenHandsRole()
    res = role._call_openhands("fix bug", workflow_id="wf1")
    assert res.role == "openhands"
    assert res.provider == "openhands"
    assert "patched" in res.content


def test_coordinate_code_openhands_disabled(monkeypatch):
    """coordinate_code falls back to Coder when OpenHands off."""
    import orchestrator.crew.coordinator as coord
    import orchestrator.crew.roles as roles_mod
    from router.base import ChatResult

    class FR:
        def chat(self, msgs, **kw):
            return ChatResult(text='{"stub": true}', model="s", provider="s",
                              prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(roles_mod, "OpenHandsRole", lambda *a, **k: Coder(FR()))
    result = coord.coordinate_code("task", "analysis", [], workflow_id="wf2")
    assert isinstance(result, str)
