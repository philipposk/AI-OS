"""Per-provider wire tests.

Mocks the network layer (requests / anthropic SDK) so we verify each client
builds the correct request and parses the canonical response shape. No live
calls, no recorded cassettes.
"""
from __future__ import annotations

from typing import Any

import pytest

from router.base import ProviderUnavailable


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} fake error", response=self)

    def json(self):
        return self._payload


def _make_post_capture(payload: dict, capture: dict):
    def post(url, headers=None, json=None, timeout=None):
        capture["url"] = url
        capture["headers"] = headers or {}
        capture["payload"] = json or {}
        capture["timeout"] = timeout
        return _FakeResponse(payload)
    return post


# ---------- OpenAI-compatible providers (openrouter, nvidia, groq) ----------


def test_openrouter_round_trip(monkeypatch):
    from router import openrouter_client as mod

    captured: dict = {}
    monkeypatch.setattr(
        mod.requests,
        "post",
        _make_post_capture(
            {
                "choices": [{"message": {"content": "pong"}}],
                "model": "meta-llama/llama-3.2-3b-instruct:free",
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
            captured,
        ),
    )
    client = mod.OpenRouterClient(api_key="sk-test")
    res = client.chat([{"role": "user", "content": "ping"}], model="meta-llama/llama-3.2-3b-instruct:free", max_tokens=8, temperature=0.1)
    assert res.text == "pong"
    assert res.provider == "openrouter"
    assert res.prompt_tokens == 3 and res.completion_tokens == 1
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["payload"]["model"] == "meta-llama/llama-3.2-3b-instruct:free"
    assert captured["payload"]["max_tokens"] == 8
    assert captured["payload"]["temperature"] == 0.1


def test_openrouter_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from router import openrouter_client as mod
    client = mod.OpenRouterClient(api_key=None)
    with pytest.raises(ProviderUnavailable):
        client.chat([{"role": "user", "content": "x"}], model="m")


def test_nvidia_round_trip(monkeypatch):
    from router import nvidia_client as mod

    captured: dict = {}
    monkeypatch.setattr(
        mod.requests,
        "post",
        _make_post_capture(
            {
                "choices": [{"message": {"content": "hello"}}],
                "model": "meta/llama-3.1-8b-instruct",
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
            captured,
        ),
    )
    client = mod.NvidiaClient(api_key="nvapi-test")
    res = client.chat([{"role": "user", "content": "hi"}], model="meta/llama-3.1-8b-instruct")
    assert res.text == "hello"
    assert captured["headers"]["Authorization"].startswith("Bearer nvapi-")
    assert captured["payload"]["top_p"] == 0.95


def test_groq_round_trip(monkeypatch):
    from router import groq_client as mod

    captured: dict = {}
    monkeypatch.setattr(
        mod.requests,
        "post",
        _make_post_capture(
            {
                "choices": [{"message": {"content": "g"}}],
                "model": "llama-3.3-70b-versatile",
                "usage": {"prompt_tokens": 7, "completion_tokens": 1},
            },
            captured,
        ),
    )
    client = mod.GroqClient(api_key="gsk-test")
    res = client.chat([{"role": "user", "content": "x"}], model="llama-3.3-70b-versatile")
    assert res.text == "g"
    assert captured["url"].endswith("groq.com/openai/v1/chat/completions")


# ---------- Ollama ----------


def test_ollama_round_trip(monkeypatch):
    from router import ollama_client as mod

    captured: dict = {}
    monkeypatch.setattr(
        mod.requests,
        "post",
        _make_post_capture(
            {
                "model": "llama3.2:3b",
                "message": {"role": "assistant", "content": "local"},
                "prompt_eval_count": 4,
                "eval_count": 2,
            },
            captured,
        ),
    )
    client = mod.OllamaClient(base_url="http://localhost:11434")
    res = client.chat([{"role": "user", "content": "hi"}], model="llama3.2:3b", max_tokens=64, temperature=0.5)
    assert res.text == "local"
    assert res.prompt_tokens == 4 and res.completion_tokens == 2
    assert captured["url"].endswith("/api/chat")
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["options"]["temperature"] == 0.5
    assert captured["payload"]["options"]["num_predict"] == 64


def test_ollama_unavailable_when_endpoint_down(monkeypatch):
    from router import ollama_client as mod
    import requests as real_requests

    def boom(*a, **k):
        raise real_requests.exceptions.ConnectionError("no route to ollama")
    monkeypatch.setattr(mod.requests, "post", boom)
    client = mod.OllamaClient(base_url="http://10.255.255.1:11434")
    with pytest.raises(ProviderUnavailable):
        client.chat([{"role": "user", "content": "x"}], model="llama3.2:3b")


# ---------- Anthropic (SDK mock) ----------


class _FakeAnthropicResp:
    class _Block:
        def __init__(self, text):
            self.type = "text"
            self.text = text

    class _Usage:
        def __init__(self, i, o):
            self.input_tokens = i
            self.output_tokens = o

    def __init__(self, text, model, in_tok, out_tok):
        self.content = [self._Block(text)]
        self.model = model
        self.id = "msg_fake"
        self.stop_reason = "end_turn"
        self.usage = self._Usage(in_tok, out_tok)


class _FakeAnthropicSDK:
    def __init__(self):
        self.messages = self
        self.last: dict = {}

    def create(self, **kwargs):
        self.last = kwargs
        return _FakeAnthropicResp("hi from claude", kwargs["model"], 9, 4)


def test_anthropic_round_trip_and_system_concat(monkeypatch):
    from router import anthropic_client as mod

    fake = _FakeAnthropicSDK()
    client = mod.AnthropicClient(api_key="sk-ant-test")
    monkeypatch.setattr(client, "_sdk", fake, raising=False)

    res = client.chat(
        [
            {"role": "system", "content": "S1"},
            {"role": "system", "content": "S2"},
            {"role": "user", "content": "u"},
        ],
        model="claude-haiku-4-5",
        max_tokens=32,
        temperature=0.2,
    )
    assert res.text == "hi from claude"
    assert res.model == "claude-haiku-4-5"
    assert res.prompt_tokens == 9 and res.completion_tokens == 4
    assert fake.last["system"] == "S1\n\nS2"
    assert fake.last["messages"] == [{"role": "user", "content": "u"}]


def test_anthropic_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from router import anthropic_client as mod
    client = mod.AnthropicClient(api_key=None)
    with pytest.raises(ProviderUnavailable):
        client.chat([{"role": "user", "content": "x"}], model="claude-haiku-4-5")
