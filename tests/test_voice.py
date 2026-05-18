"""STT (Groq Whisper) + browser TTS HTML snippet. No network."""
from __future__ import annotations

import pytest
import requests

from router import transcription as tr_mod
from router.base import ProviderUnavailable


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    tr_mod.reset_for_tests()
    yield
    tr_mod.reset_for_tests()


def test_groq_transcriber_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    t = tr_mod.GroqTranscriber()
    assert not t.is_available()
    with pytest.raises(ProviderUnavailable):
        t.transcribe(b"\x00\x00", filename="x.webm")


def test_groq_transcriber_uploads_multipart_and_parses(monkeypatch):
    captured = {}

    class _R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"text": " hello world ", "language": "en"}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["files_keys"] = list(files.keys())
        captured["model"] = data["model"]
        captured["response_format"] = data["response_format"]
        return _R()

    monkeypatch.setattr(tr_mod.requests, "post", fake_post)

    t = tr_mod.GroqTranscriber(api_key="gsk-test", model="whisper-large-v3-turbo")
    out = t.transcribe(b"<webm bytes>", filename="mic.webm", mime_type="audio/webm")
    assert out.text == "hello world"
    assert out.model == "whisper-large-v3-turbo"
    assert out.language == "en"
    assert captured["url"].endswith("/audio/transcriptions")
    assert captured["headers"]["Authorization"] == "Bearer gsk-test"
    assert captured["files_keys"] == ["file"]
    assert captured["model"] == "whisper-large-v3-turbo"
    assert captured["response_format"] == "json"


def test_get_transcriber_returns_groq_when_key_set(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("STT_BACKEND", raising=False)
    t = tr_mod.get_transcriber()
    assert t is not None and t.name == "groq"


def test_get_transcriber_none_when_no_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("STT_BACKEND", raising=False)
    assert tr_mod.get_transcriber() is None


def test_get_transcriber_honours_backend_none(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("STT_BACKEND", "none")
    assert tr_mod.get_transcriber() is None


# ---------- TTS HTML snippet ----------


def test_speak_html_escapes_quotes_safely():
    from ui.dashboard import _speak_html
    payload = 'He said "hi" and </script><script>alert(1)</script>'
    html = _speak_html(payload)
    # JSON-escaped text inside the script — backslash-quote sequence, no raw "
    assert '"He said \\"hi\\"' in html
    # The closing </script> in the payload must not break out — JSON encodes it as <... or escapes the slash.
    # Either way, no literal "</script>" before our closing </script>.
    body_before_close = html.rsplit("</script>", 1)[0]
    assert "</script>" not in body_before_close
