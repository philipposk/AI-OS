"""Streaming tests across providers. No network."""
from __future__ import annotations

import io
import json
from typing import Iterable

import pytest
import requests

from router import openrouter_client as orc_mod
from router import groq_client as gqc_mod
from router import nvidia_client as nvc_mod
from router import ollama_client as ol_mod
from router.base import ChatResult


class _StreamingResp:
    """Mimics requests.Response with iter_lines for SSE / NDJSON."""

    def __init__(self, lines: Iterable[str], status: int = 200):
        self._lines = list(lines)
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} fake", response=self)

    def iter_lines(self, decode_unicode=False):
        for line in self._lines:
            yield line


def _sse(content_chunks, *, model="m", usage=None):
    """Build an OpenAI-style SSE stream."""
    out = []
    for c in content_chunks:
        out.append("data: " + json.dumps({"choices": [{"delta": {"content": c}}], "model": model}))
        out.append("")
    if usage is not None:
        out.append("data: " + json.dumps({"choices": [{"delta": {}}], "model": model, "usage": usage}))
        out.append("")
    out.append("data: [DONE]")
    out.append("")
    return out


# ---------- OpenAI-compat providers ----------


@pytest.mark.parametrize("module,client_cls,api_kw", [
    (orc_mod, "OpenRouterClient", {"api_key": "sk-test"}),
    (gqc_mod, "GroqClient", {"api_key": "gsk-test"}),
    (nvc_mod, "NvidiaClient", {"api_key": "nv-test"}),
])
def test_openai_compat_stream_yields_chunks_then_result(monkeypatch, module, client_cls, api_kw):
    def fake_post(url, headers=None, json=None, timeout=None, stream=False):
        assert json["stream"] is True
        return _StreamingResp(_sse(["Hel", "lo ", "world"], model="m", usage={"prompt_tokens": 3, "completion_tokens": 4}))
    monkeypatch.setattr(module.http_retry, "post", fake_post)

    client = getattr(module, client_cls)(**api_kw)
    items = list(client.chat_stream([{"role": "user", "content": "hi"}], model="m"))
    chunks = [i for i in items if isinstance(i, str)]
    results = [i for i in items if isinstance(i, ChatResult)]
    assert "".join(chunks) == "Hello world"
    assert len(results) == 1
    assert results[0].text == "Hello world"
    assert results[0].prompt_tokens == 3
    assert results[0].completion_tokens == 4


# ---------- Ollama (newline-delimited JSON) ----------


def test_ollama_stream(monkeypatch):
    lines = [
        json.dumps({"model": "llama3.2:3b", "message": {"content": "He"}, "done": False}),
        json.dumps({"model": "llama3.2:3b", "message": {"content": "llo"}, "done": False}),
        json.dumps({"model": "llama3.2:3b", "message": {"content": ""}, "done": True,
                    "prompt_eval_count": 2, "eval_count": 5}),
    ]
    def fake_post(url, json=None, timeout=None, stream=False):
        return _StreamingResp(lines)
    monkeypatch.setattr(ol_mod.http_retry, "post", fake_post)

    client = ol_mod.OllamaClient(base_url="http://localhost:11434")
    items = list(client.chat_stream([{"role": "user", "content": "x"}], model="llama3.2:3b"))
    chunks = [i for i in items if isinstance(i, str)]
    final = [i for i in items if isinstance(i, ChatResult)][0]
    assert "".join(chunks) == "Hello"
    assert final.prompt_tokens == 2
    assert final.completion_tokens == 5


# ---------- Router-level streaming records to ledger ----------


def test_router_chat_stream_records_accounting(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "stream.sqlite"))

    class FakeProvider:
        name = "fake"
        def is_available(self): return True
        def default_model(self): return "fake-model"
        def chat(self, *a, **k): raise AssertionError("chat() should not be called")
        def chat_stream(self, messages, model, max_tokens=1024, temperature=0.7):
            yield "Hel"
            yield "lo"
            yield ChatResult(text="Hello", model=model, provider=self.name, prompt_tokens=7, completion_tokens=2)

    from router.router import ModelRouter
    r = ModelRouter(providers={"openrouter": FakeProvider()})
    items = list(r.chat_stream([{"role": "user", "content": "hi"}], task_type="plan", model="fake-model"))
    assert items[:-1] == ["Hel", "lo"]
    assert isinstance(items[-1], ChatResult)
    from storage.accounting import iter_entries
    entries = list(iter_entries())
    assert len(entries) == 1
    assert entries[0].prompt_tokens == 7 and entries[0].completion_tokens == 2
