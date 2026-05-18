"""Groq + NVIDIA free-model rotation tests. No network."""
from __future__ import annotations

import pytest
import requests

from router import groq_client as gqc_mod
from router import groq_free as gqf
from router import nvidia_client as nvc_mod
from router import nvidia_free as nvf


@pytest.fixture(autouse=True)
def sandbox_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "rot.sqlite"))


class _FakeResp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} fake", response=self)

    def json(self):
        return self._p


# ---------- Groq ----------


def test_groq_filters_whisper_orpheus_guards(monkeypatch):
    monkeypatch.setattr(
        gqf.requests,
        "get",
        lambda *a, **k: _FakeResp({"data": [
            {"id": "openai/gpt-oss-120b", "context_window": 131072},
            {"id": "whisper-large-v3", "context_window": 0},
            {"id": "canopylabs/orpheus-v1-english", "context_window": 0},
            {"id": "meta-llama/llama-prompt-guard-2-22m", "context_window": 0},
            {"id": "openai/gpt-oss-safeguard-20b", "context_window": 0},
            {"id": "groq/compound-mini", "context_window": 0},
            {"id": "llama-3.3-70b-versatile", "context_window": 131072},
        ]}),
    )
    r = gqf.GroqRotator()
    models = r.list_models("gsk-test")
    ids = [m.id for m in models]
    assert "openai/gpt-oss-120b" in ids
    assert "llama-3.3-70b-versatile" in ids
    assert "whisper-large-v3" not in ids
    assert "canopylabs/orpheus-v1-english" not in ids
    assert "meta-llama/llama-prompt-guard-2-22m" not in ids
    assert "groq/compound-mini" not in ids
    # Preference order: gpt-oss-120b (10) before llama-3.3-70b-versatile (9)
    assert ids[0] == "openai/gpt-oss-120b"


def test_groq_rotate_on_429(monkeypatch):
    monkeypatch.setattr(
        gqf.requests,
        "get",
        lambda *a, **k: _FakeResp({"data": [
            {"id": "openai/gpt-oss-120b"},
            {"id": "llama-3.3-70b-versatile"},
        ]}),
    )
    calls = []
    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        if json["model"] == "openai/gpt-oss-120b":
            return _FakeResp({}, status=429)
        return _FakeResp(
            {"choices": [{"message": {"content": "ok"}}], "model": json["model"], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        )
    monkeypatch.setattr(gqc_mod.requests, "post", fake_post)

    client = gqc_mod.GroqClient(api_key="gsk-test")
    res = client.chat([{"role": "user", "content": "x"}], model="free-rotate")
    assert res.text == "ok"
    assert calls == ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]


def test_groq_non_sentinel_bypasses_rotation(monkeypatch):
    monkeypatch.setattr(gqf.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rotation path used")))
    monkeypatch.setattr(
        gqc_mod.requests,
        "post",
        lambda *a, **k: _FakeResp({"choices": [{"message": {"content": "direct"}}], "model": k["json"]["model"], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
    )
    client = gqc_mod.GroqClient(api_key="gsk-test")
    res = client.chat([{"role": "user", "content": "x"}], model="llama-3.3-70b-versatile")
    assert res.text == "direct"


# ---------- NVIDIA ----------


def test_nvidia_filters_vision_embed_guard_code(monkeypatch):
    monkeypatch.setattr(
        nvf.requests,
        "get",
        lambda *a, **k: _FakeResp({"data": [
            {"id": "meta/llama-3.3-70b-instruct", "context_length": 131072},
            {"id": "meta/llama-3.2-11b-vision-instruct"},
            {"id": "baai/bge-m3"},
            {"id": "google/deplot"},
            {"id": "adept/fuyu-8b"},
            {"id": "meta/llama-guard-4-12b"},
            {"id": "google/codegemma-7b"},
            {"id": "bigcode/starcoder2-15b"},
            {"id": "ibm/granite-34b-code-instruct"},
            {"id": "nvidia/llama-3.1-nemotron-70b-instruct"},
            {"id": "meta/llama-3.3-70b-instruct"},  # duplicate
        ]}),
    )
    r = nvf.NvidiaRotator()
    models = r.list_models("nv-test")
    ids = [m.id for m in models]
    assert "meta/llama-3.3-70b-instruct" in ids
    assert ids.count("meta/llama-3.3-70b-instruct") == 1  # dedup
    assert "nvidia/llama-3.1-nemotron-70b-instruct" in ids
    for bad in ("meta/llama-3.2-11b-vision-instruct", "baai/bge-m3", "google/deplot",
                "adept/fuyu-8b", "meta/llama-guard-4-12b", "google/codegemma-7b",
                "bigcode/starcoder2-15b", "ibm/granite-34b-code-instruct"):
        assert bad not in ids, f"{bad} should have been filtered"
    # Preference: llama-3.3-70b (10) before nemotron-70b (9.5)
    assert ids[0] == "meta/llama-3.3-70b-instruct"


def test_nvidia_rotate_skips_429(monkeypatch):
    monkeypatch.setattr(
        nvf.requests,
        "get",
        lambda *a, **k: _FakeResp({"data": [
            {"id": "meta/llama-3.3-70b-instruct"},
            {"id": "nvidia/llama-3.1-nemotron-70b-instruct"},
        ]}),
    )
    calls = []
    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        if json["model"] == "meta/llama-3.3-70b-instruct":
            return _FakeResp({}, status=429)
        return _FakeResp(
            {"choices": [{"message": {"content": "v"}}], "model": json["model"], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        )
    monkeypatch.setattr(nvc_mod.requests, "post", fake_post)

    client = nvc_mod.NvidiaClient(api_key="nv-test")
    res = client.chat([{"role": "user", "content": "x"}], model="free-rotate")
    assert res.text == "v"
    assert calls == ["meta/llama-3.3-70b-instruct", "nvidia/llama-3.1-nemotron-70b-instruct"]


def test_provider_isolation_cache_tables_dont_collide(monkeypatch):
    """Each rotator must use a different SQLite table so caches are independent.

    Note: gqf.requests and nvf.requests are the *same* requests module, so we
    URL-dispatch in a single monkeypatch rather than patching twice.
    """
    import requests as real_requests

    def fake_get(url, headers=None, timeout=None):
        if "groq.com" in url:
            return _FakeResp({"data": [{"id": "openai/gpt-oss-120b"}]})
        return _FakeResp({"data": [{"id": "meta/llama-3.3-70b-instruct"}]})
    monkeypatch.setattr(real_requests, "get", fake_get)

    g = gqf.GroqRotator()
    n = nvf.NvidiaRotator()
    g_models, _ = g.get_models("gsk", force_refresh=True)
    n_models, _ = n.get_models("nv", force_refresh=True)
    assert g_models[0].id == "openai/gpt-oss-120b"
    assert n_models[0].id == "meta/llama-3.3-70b-instruct"
    assert g._table() == "groq_free"
    assert n._table() == "nvidia_free"
