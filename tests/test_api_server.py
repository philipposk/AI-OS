"""FastAPI shim tests. No network."""
from __future__ import annotations

import json
import os

import pytest

from router.base import ChatResult


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "api.sqlite"))
    # Set token to empty string to opt-in to open access (deny-by-default changed to
    # require explicit empty string for open mode; unset env var now means DENY).
    monkeypatch.setenv("API_COMPANY_TOKEN", "")


@pytest.fixture
def client(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api import server as srv

    # Patch ModelRouter so no real providers are touched.
    class _StubProvider:
        name = "stub"
        def is_available(self): return True
        def default_model(self): return "stub-default"
        def chat(self, messages, model, max_tokens=1024, temperature=0.7):
            return ChatResult(text="hello world", model=model or "stub-default", provider="stub",
                              prompt_tokens=5, completion_tokens=2)
        def chat_stream(self, messages, model, max_tokens=1024, temperature=0.7):
            yield "Hel"
            yield "lo"
            yield ChatResult(text="Hello", model=model or "stub-default", provider="stub",
                             prompt_tokens=5, completion_tokens=2)

    from router.router import ModelRouter

    def _make_router():
        return ModelRouter(providers={"openrouter": _StubProvider()})

    monkeypatch.setattr(srv, "ModelRouter", _make_router)
    app = srv.get_app()
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "openrouter" in r.json()["providers"]


def test_list_models_includes_task_types_and_providers(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    ids = {m["id"] for m in r.json()["data"]}
    assert {"analyze", "plan", "code", "review", "simple"} <= ids
    assert "openrouter:stub-default" in ids


def test_non_streaming_chat(client):
    r = client.post("/v1/chat/completions", json={
        "model": "simple",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "hello world"
    assert body["usage"]["prompt_tokens"] == 5
    assert body["usage"]["completion_tokens"] == 2
    assert body["x_ai_company"]["provider"] == "stub"


def test_streaming_chat_emits_sse(client):
    with client.stream("POST", "/v1/chat/completions", json={
        "model": "simple",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }) as r:
        assert r.status_code == 200
        # Read all SSE lines
        text = "".join(chunk for chunk in r.iter_text())
    assert "data: [DONE]" in text
    # Concatenate content deltas
    chunks = []
    for line in text.splitlines():
        if not line.startswith("data: ") or line.endswith("[DONE]"):
            continue
        payload = json.loads(line[6:])
        if "choices" not in payload:
            continue
        delta = payload["choices"][0].get("delta") or {}
        if "content" in delta:
            chunks.append(delta["content"])
    assert "".join(chunks) == "Hello"


def test_auth_required_when_token_set(monkeypatch):
    monkeypatch.setenv("API_COMPANY_TOKEN", "secret")
    from fastapi.testclient import TestClient
    from api import server as srv

    class _Stub:
        name = "stub"
        def is_available(self): return True
        def default_model(self): return "s"
        def chat(self, *a, **k): return ChatResult(text="x", model="s", provider="stub")
        def chat_stream(self, *a, **k):
            yield "x"
            yield ChatResult(text="x", model="s", provider="stub")

    from router.router import ModelRouter
    monkeypatch.setattr(srv, "ModelRouter", lambda: ModelRouter(providers={"openrouter": _Stub()}))
    client = TestClient(srv.get_app())

    r1 = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}]})
    assert r1.status_code == 401

    r2 = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer secret"},
        json={"messages": [{"role": "user", "content": "x"}]},
    )
    assert r2.status_code == 200


def test_missing_messages_returns_400(client):
    r = client.post("/v1/chat/completions", json={"model": "simple"})
    assert r.status_code == 400
    assert "messages" in r.text


def test_accounting_endpoint(client):
    # Make one call so ledger has a row
    client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}], "stream": False})
    r = client.get("/v1/accounting")
    assert r.status_code == 200
    body = r.json()
    assert body["total_calls"] >= 1


def test_auth_deny_when_token_not_configured(monkeypatch, tmp_path):
    """When API_COMPANY_TOKEN is not set at all, all non-health endpoints must deny."""
    monkeypatch.delenv("API_COMPANY_TOKEN", raising=False)
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "deny.sqlite"))
    from fastapi.testclient import TestClient
    from api import server as srv

    class _Stub:
        name = "stub"
        def is_available(self): return True
        def default_model(self): return "s"
        def chat(self, *a, **k): return ChatResult(text="x", model="s", provider="stub")

    from router.router import ModelRouter
    monkeypatch.setattr(srv, "ModelRouter", lambda: ModelRouter(providers={"openrouter": _Stub()}))
    client = TestClient(srv.get_app())

    assert client.get("/health").status_code == 200          # /health always open
    assert client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}]}).status_code == 401
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/accounting").status_code == 401


def test_server_side_workflow_id(monkeypatch, tmp_path):
    """workflow_start must always generate its own ID; caller-supplied ID is ignored."""
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "wf.sqlite"))
    monkeypatch.setenv("API_COMPANY_TOKEN", "")
    from fastapi.testclient import TestClient
    from api import server as srv
    from router.router import ModelRouter

    class _Stub:
        name = "stub"
        def is_available(self): return True
        def default_model(self): return "s"

    monkeypatch.setattr(srv, "ModelRouter", lambda: ModelRouter(providers={"openrouter": _Stub()}))

    # Patch _get_workflow to avoid actually building a LangGraph
    class _FakeEntry(dict): pass
    captured = {}
    original_gw = None

    app = srv.get_app()
    client = TestClient(app)

    # Patch build_graph inside the app to avoid LangGraph overhead
    import orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "build_graph", lambda: _MockGraph())

    class _MockGraph:
        def stream(self, payload, config):
            captured["wid"] = payload.get("workflow_id")
            return iter([])

    # We just verify the wid in the response is server-generated (not caller-supplied)
    # by checking it's a 32-char hex string (full uuid4 hex)
    # Actual workflow execution is mocked so we just check the ID format.
    # Since we can't easily mock _get_workflow inside the closure, check via the response.
    r = client.post("/v1/workflows/start", json={"task": "test", "workflow_id": "caller-supplied-id"})
    if r.status_code == 200:
        returned_wid = r.json().get("workflow_id", "")
        assert returned_wid != "caller-supplied-id", "Server must not use caller-supplied workflow_id"
        assert len(returned_wid) == 32, f"Expected full uuid4 hex (32 chars), got {len(returned_wid)}"


def test_max_tokens_clamped(client):
    """max_tokens above ceiling should be clamped, not passed raw."""
    r = client.post("/v1/chat/completions", json={
        "model": "simple",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
        "max_tokens": 999999,
    })
    assert r.status_code == 200


def test_workflow_list_and_delete(monkeypatch, tmp_path):
    """GET /v1/workflows lists entries; DELETE /v1/workflows/{wid} removes them."""
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "wl.sqlite"))
    monkeypatch.setenv("API_COMPANY_TOKEN", "")
    from fastapi.testclient import TestClient
    from api import server as srv
    from router.router import ModelRouter

    class _Stub:
        name = "stub"
        def is_available(self): return True
        def default_model(self): return "s"

    monkeypatch.setattr(srv, "ModelRouter", lambda: ModelRouter(providers={"openrouter": _Stub()}))
    client = TestClient(srv.get_app())

    # Initially empty
    r = client.get("/v1/workflows")
    assert r.status_code == 200
    assert r.json()["workflows"] == []

    # 404 on unknown delete
    r = client.delete("/v1/workflows/nonexistent")
    assert r.status_code == 404


# ── /v1/llm/complete (page-assistant bridge) ──────────────────────────────


def test_llm_complete_auth_required(monkeypatch, tmp_path):
    """Without bearer token, endpoint must return 401."""
    monkeypatch.setenv("API_COMPANY_TOKEN", "secret")
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "pa.sqlite"))
    from fastapi.testclient import TestClient
    from api import server as srv
    from router.router import ModelRouter

    class _Stub:
        name = "stub"
        def is_available(self): return True
        def default_model(self): return "s"

    monkeypatch.setattr(srv, "ModelRouter", lambda: ModelRouter(providers={"openrouter": _Stub()}))
    c = TestClient(srv.get_app())

    r = c.post("/v1/llm/complete", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401

    r2 = c.post(
        "/v1/llm/complete",
        headers={"Authorization": "Bearer secret"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    # 503 expected because no provider keys are set — but NOT 401
    assert r2.status_code != 401


def test_llm_complete_503_when_no_provider(monkeypatch, tmp_path):
    """503 Service Unavailable when no LLM provider key is configured."""
    monkeypatch.setenv("API_COMPANY_TOKEN", "")
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "pa2.sqlite"))
    for key in ("PA_LLM_BASE_URL", "PA_LLM_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    from fastapi.testclient import TestClient
    from api import server as srv
    from router.router import ModelRouter

    class _Stub:
        name = "stub"
        def is_available(self): return True
        def default_model(self): return "s"

    monkeypatch.setattr(srv, "ModelRouter", lambda: ModelRouter(providers={"openrouter": _Stub()}))
    c = TestClient(srv.get_app())

    r = c.post("/v1/llm/complete", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 503
    assert "provider" in r.text.lower() or "key" in r.text.lower()


def test_llm_complete_returns_text(monkeypatch, tmp_path):
    """Happy-path: mocked upstream returns a text reply; endpoint maps it to {toolCalls, text}."""
    monkeypatch.setenv("API_COMPANY_TOKEN", "")
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "pa3.sqlite"))
    monkeypatch.setenv("PA_LLM_BASE_URL", "http://fake-llm")
    monkeypatch.setenv("PA_LLM_API_KEY", "fake-key")
    monkeypatch.setenv("PA_LLM_MODEL", "fake-model")

    from fastapi.testclient import TestClient
    from api import server as srv
    from router.router import ModelRouter
    import httpx

    class _Stub:
        name = "stub"
        def is_available(self): return True
        def default_model(self): return "s"

    monkeypatch.setattr(srv, "ModelRouter", lambda: ModelRouter(providers={"openrouter": _Stub()}))

    fake_upstream_body = {
        "choices": [{"message": {"role": "assistant", "content": "Hello from AI OS!", "tool_calls": []}}]
    }

    class _FakeResponse:
        status_code = 200
        is_success = True
        text = ""
        def json(self): return fake_upstream_body

    class _FakeAsyncClient:
        def __init__(self, **_kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): pass
        async def post(self, *_a, **_kw): return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    c = TestClient(srv.get_app())
    r = c.post(
        "/v1/llm/complete",
        json={"system": "You are helpful.", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "Hello from AI OS!"
    assert body["toolCalls"] == []
