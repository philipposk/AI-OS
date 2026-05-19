"""Vision content-block handling. No network."""
from __future__ import annotations

import pytest

from router import vision as v
from router.base import ChatResult, ProviderUnavailable


_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkAAAAB"
    "gAB6Y7zwwAAAABJRU5ErkJggg=="
)


# ---------- parse_data_url ----------


def test_parse_data_url_png():
    mt, data = v.parse_data_url(f"data:image/png;base64,{_TINY_PNG_B64}")
    assert mt == "image/png"
    assert data == _TINY_PNG_B64


def test_parse_data_url_rejects_non_data_url():
    assert v.parse_data_url("https://example.com/x.png") == (None, None)
    assert v.parse_data_url("not a url") == (None, None)
    assert v.parse_data_url(None) == (None, None)


# ---------- has_images / text_only ----------


def test_has_images_detects_image_url_block():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "what's this?"},
        {"type": "image_url", "image_url": {"url": "https://x.example/a.png"}},
    ]}]
    assert v.has_images(msgs) is True


def test_has_images_false_on_plain_text():
    assert v.has_images([{"role": "user", "content": "hi"}]) is False
    assert v.has_images([{"role": "user", "content": [
        {"type": "text", "text": "no images here"},
    ]}]) is False


def test_text_only_strips_images_with_marker():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "describe"},
        {"type": "image_url", "image_url": {"url": "https://x.example/a.png"}},
    ]}]
    out = v.text_only(msgs)
    assert out[0]["content"].startswith("describe")
    assert "[image omitted" in out[0]["content"]


def test_text_only_passes_string_content_through():
    out = v.text_only([{"role": "user", "content": "hello"}])
    assert out == [{"role": "user", "content": "hello"}]


# ---------- to_anthropic ----------


def test_to_anthropic_translates_data_url():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_TINY_PNG_B64}"}},
    ]}]
    out = v.to_anthropic(msgs)
    blocks = out[0]["content"]
    assert blocks[0] == {"type": "text", "text": "look"}
    assert blocks[1]["type"] == "image"
    src = blocks[1]["source"]
    assert src["type"] == "base64"
    assert src["media_type"] == "image/png"
    assert src["data"] == _TINY_PNG_B64


def test_to_anthropic_translates_http_url():
    msgs = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}},
    ]}]
    out = v.to_anthropic(msgs)
    src = out[0]["content"][0]["source"]
    assert src == {"type": "url", "url": "https://example.com/img.jpg"}


def test_to_anthropic_preserves_string_content():
    out = v.to_anthropic([{"role": "user", "content": "just text"}])
    assert out == [{"role": "user", "content": "just text"}]


def test_to_anthropic_passes_anthropic_image_block_through():
    msgs = [{"role": "user", "content": [
        {"type": "image", "source": {"type": "url", "url": "https://x/a.png"}},
    ]}]
    out = v.to_anthropic(msgs)
    assert out[0]["content"][0]["source"]["url"] == "https://x/a.png"


# ---------- router integration ----------


class _Fake:
    def __init__(self, name, *, available=True):
        self.name = name
        self._available = available
        self.last_messages = None
    def is_available(self): return self._available
    def default_model(self): return "stub"
    def chat(self, messages, model, max_tokens=1024, temperature=0.7):
        self.last_messages = messages
        return ChatResult(text="ok", model=model, provider=self.name,
                          prompt_tokens=1, completion_tokens=1)
    def chat_stream(self, messages, model, max_tokens=1024, temperature=0.7):
        self.last_messages = messages
        yield "ok"
        yield ChatResult(text="ok", model=model, provider=self.name,
                         prompt_tokens=1, completion_tokens=1)


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "v.sqlite"))
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "none")
    from router import circuit as cb_mod
    cb_mod.reset_for_tests()


def test_router_routes_image_to_anthropic_when_available():
    from router.router import ModelRouter
    anth = _Fake("anthropic")
    groq = _Fake("groq")
    r = ModelRouter(providers={"anthropic": anth, "groq": groq})

    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "describe"},
        {"type": "image_url", "image_url": {"url": "https://x/img.png"}},
    ]}]
    res = r.chat(msgs, task_type="simple", model="stub")
    assert res.provider == "anthropic"
    # Anthropic received block-form content (image preserved through router;
    # the client itself translates via to_anthropic, but router passed through).
    assert isinstance(anth.last_messages[0]["content"], list)


def test_router_skips_text_only_provider_when_vision_required():
    from router.router import ModelRouter
    # Only groq available — text only
    groq = _Fake("groq")
    r = ModelRouter(providers={"groq": groq})

    msgs = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "https://x/img.png"}},
    ]}]
    with pytest.raises(ProviderUnavailable, match="vision-capable"):
        r.chat(msgs, task_type="simple", model="stub")


def test_router_strips_images_when_text_only_used_with_blocks():
    """If somehow a non-vision provider ends up getting a block-form text-only
    message (no images), router flattens to string so it doesn't error."""
    from router.router import ModelRouter
    groq = _Fake("groq")
    r = ModelRouter(providers={"groq": groq})

    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    r.chat(msgs, task_type="simple", model="stub")
    # Groq got plain string content, not list
    assert isinstance(groq.last_messages[0]["content"], str)
    assert "hi" in groq.last_messages[0]["content"]


def test_router_stream_handles_vision_path():
    from router.router import ModelRouter
    anth = _Fake("anthropic")
    r = ModelRouter(providers={"anthropic": anth})

    msgs = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "https://x/img.png"}},
    ]}]
    items = list(r.chat_stream(msgs, task_type="simple", model="stub"))
    assert any(isinstance(i, ChatResult) for i in items)
    assert isinstance(anth.last_messages[0]["content"], list)
