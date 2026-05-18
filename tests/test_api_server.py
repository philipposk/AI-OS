"""FastAPI shim tests. No network."""
from __future__ import annotations

import json
import os

import pytest

from router.base import ChatResult


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "api.sqlite"))
    monkeypatch.delenv("API_COMPANY_TOKEN", raising=False)


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
