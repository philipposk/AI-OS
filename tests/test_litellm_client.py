"""LiteLLM provider — unit tests with the real package stubbed."""
from __future__ import annotations

import sys
import types

import pytest

from router.base import ChatResult


@pytest.fixture
def fake_litellm(monkeypatch):
    """Inject a fake `litellm` module so we don't hit the network."""
    mod = types.SimpleNamespace()

    class _Msg:
        def __init__(self, content): self.content = content

    class _Choice:
        def __init__(self, content, role="assistant"):
            self.message = _Msg(content)
            self.delta = _Msg(content)

    class _Resp:
        def __init__(self, content="hello from litellm", model="openai/gpt-4o-mini",
                     prompt_tokens=11, completion_tokens=22):
            self.choices = [_Choice(content)]
            self.model = model
            self.id = "fake-id"
            self.usage = types.SimpleNamespace(prompt_tokens=prompt_tokens,
                                                completion_tokens=completion_tokens)

    def completion(model, messages, max_tokens=1024, temperature=0.7, stream=False, **kw):
        if stream:
            return iter([_Resp(content="he", model=model), _Resp(content="llo", model=model)])
        return _Resp(model=model)

    mod.completion = completion
    monkeypatch.setitem(sys.modules, "litellm", mod)
    return mod


def test_is_available_requires_key(monkeypatch, fake_litellm):
    from router.litellm_client import LiteLLMClient
    # Clear EVERY key _has_any_provider_key checks so leftover .env doesn't taint.
    for k in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
        "GROQ_API_KEY", "MISTRAL_API_KEY", "COHERE_API_KEY",
        "TOGETHER_API_KEY", "FIREWORKS_API_KEY", "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY", "PERPLEXITYAI_API_KEY", "XAI_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    assert LiteLLMClient().is_available() is False

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    assert LiteLLMClient().is_available() is True


def test_chat_returns_chatresult(monkeypatch, fake_litellm):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    from router.litellm_client import LiteLLMClient
    c = LiteLLMClient()
    out = c.chat([{"role": "user", "content": "hi"}], model="openai/gpt-4o-mini")
    assert isinstance(out, ChatResult)
    assert out.provider == "litellm"
    assert out.text == "hello from litellm"
    assert out.prompt_tokens == 11
    assert out.completion_tokens == 22


def test_chat_stream_yields_chunks_then_chatresult(monkeypatch, fake_litellm):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    from router.litellm_client import LiteLLMClient
    c = LiteLLMClient()
    seen = []
    for item in c.chat_stream([{"role": "user", "content": "hi"}], model="openai/gpt-4o-mini"):
        seen.append(item)
    assert isinstance(seen[-1], ChatResult)
    assert seen[-1].text == "hello"  # concatenated chunks
    assert [s for s in seen[:-1] if isinstance(s, str)] == ["he", "llo"]


def test_router_registers_litellm_as_provider(monkeypatch, fake_litellm):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    from router.router import ModelRouter
    r = ModelRouter()
    assert "litellm" in r.providers
    assert r.providers["litellm"].is_available()


def test_router_resolves_litellm_prefix(monkeypatch, fake_litellm):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    from router.router import ModelRouter
    r = ModelRouter()
    prov, model = r.resolve("simple", override_model="litellm:openai/gpt-4o-mini")
    assert prov == "litellm"
    assert model == "openai/gpt-4o-mini"
