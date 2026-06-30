"""Rotation logic for OpenRouter free models. No network."""
from __future__ import annotations

import json
from typing import List

import pytest
import requests

from router import openrouter_client as orc_mod
from router import openrouter_free as orf


@pytest.fixture(autouse=True)
def sandbox_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "or.sqlite"))


def _fake_models_response(ids_and_modality: list[tuple[str, str]]):
    """Build a /models response shape with the given ids."""
    data = []
    for mid, modality in ids_and_modality:
        if modality == "text->text":
            arch = {"modality": modality, "input_modalities": ["text"], "output_modalities": ["text"]}
        elif modality == "audio":
            arch = {"modality": "audio->audio", "input_modalities": ["audio"], "output_modalities": ["audio"]}
        elif modality == "vision":
            arch = {"modality": "text+image->text", "input_modalities": ["text", "image"], "output_modalities": ["text"]}
        else:
            arch = {"modality": modality, "input_modalities": ["text"], "output_modalities": ["text"]}
        data.append({"id": mid, "context_length": 16384, "architecture": arch, "pricing": {"prompt": "0", "completion": "0"}})
    return {"data": data}


class _FakeResp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} fake", response=self)

    def json(self):
        return self._p


def test_fetch_filters_audio_and_vision(monkeypatch):
    """list endpoint returns mixed; only text->text passes through."""
    monkeypatch.setattr(
        orf.requests,
        "get",
        lambda *a, **k: _FakeResp(_fake_models_response([
            ("openai/gpt-oss-120b:free", "text->text"),
            ("google/lyria-3-clip-preview", "audio"),
            ("vision-model:free", "vision"),
            ("meta-llama/llama-3.2-3b-instruct:free", "text->text"),
        ])),
    )
    models = orf.fetch_free_models("test-key")
    ids = [m.id for m in models]
    assert "google/lyria-3-clip-preview" not in ids
    assert "vision-model:free" not in ids
    # Preference order: gpt-oss-120b weight=10, llama-3.2-3b weight=2 → 120b first.
    assert ids[0] == "openai/gpt-oss-120b:free"
    assert ids[-1] == "meta-llama/llama-3.2-3b-instruct:free"


def test_rotate_skips_429_and_returns_first_2xx(monkeypatch):
    """First model 429, second 200 → return second."""
    monkeypatch.setattr(
        orf.requests,
        "get",
        lambda *a, **k: _FakeResp(_fake_models_response([
            ("openai/gpt-oss-120b:free", "text->text"),
            ("meta-llama/llama-3.3-70b-instruct:free", "text->text"),
            ("z-ai/glm-4.5-air:free", "text->text"),
        ])),
    )

    calls: list[str] = []
    def fake_post(url, headers=None, json=None, timeout=None):
        model = json["model"]
        calls.append(model)
        if model == "openai/gpt-oss-120b:free":
            return _FakeResp({}, status=429)
        if model == "z-ai/glm-4.5-air:free":
            return _FakeResp(
                {
                    "choices": [{"message": {"content": "hi"}}],
                    "model": model,
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                }
            )
        return _FakeResp({}, status=502)
    monkeypatch.setattr(orc_mod.http_retry, "post", fake_post)

    client = orc_mod.OpenRouterClient(api_key="sk-test")
    res = client.chat([{"role": "user", "content": "x"}], model="free-rotate")
    assert res.text == "hi"
    # Preference order: gpt-oss-120b (10) → glm-4.5-air (9) → llama-3.3-70b (8)
    assert calls == ["openai/gpt-oss-120b:free", "z-ai/glm-4.5-air:free"]

    cached = orf._load_cache()
    assert cached is not None
    assert cached[2] == "z-ai/glm-4.5-air:free"  # last_used


def test_rotate_starts_from_last_used(monkeypatch):
    monkeypatch.setattr(
        orf.requests,
        "get",
        lambda *a, **k: _FakeResp(_fake_models_response([
            ("openai/gpt-oss-120b:free", "text->text"),
            ("z-ai/glm-4.5-air:free", "text->text"),
        ])),
    )
    # Seed cache with last_used = the 2nd entry.
    orf.get_models("sk-test", force_refresh=True)
    orf._save_last_used("z-ai/glm-4.5-air:free")

    calls: list[str] = []
    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        return _FakeResp(
            {
                "choices": [{"message": {"content": "ok"}}],
                "model": json["model"],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        )
    monkeypatch.setattr(orc_mod.http_retry, "post", fake_post)

    client = orc_mod.OpenRouterClient(api_key="sk-test")
    client.chat([{"role": "user", "content": "x"}], model="free-rotate")
    assert calls[0] == "z-ai/glm-4.5-air:free"  # started from last_used, not preference order


def test_rotate_all_fail_raises(monkeypatch):
    monkeypatch.setattr(
        orf.requests,
        "get",
        lambda *a, **k: _FakeResp(_fake_models_response([
            ("a:free", "text->text"),
            ("b:free", "text->text"),
        ])),
    )
    monkeypatch.setattr(orc_mod.http_retry, "post", lambda *a, **k: _FakeResp({}, status=429))
    client = orc_mod.OpenRouterClient(api_key="sk-test")
    with pytest.raises(RuntimeError, match="rate-limited"):
        client.chat([{"role": "user", "content": "x"}], model="free-rotate")


def test_rotate_non_rate_limit_error_raised_immediately(monkeypatch):
    """A 400/401 from one model isn't a rotation signal — surface it."""
    monkeypatch.setattr(
        orf.requests,
        "get",
        lambda *a, **k: _FakeResp(_fake_models_response([("a:free", "text->text")])),
    )
    monkeypatch.setattr(orc_mod.http_retry, "post", lambda *a, **k: _FakeResp({"error": "bad key"}, status=401))
    client = orc_mod.OpenRouterClient(api_key="sk-test")
    with pytest.raises(requests.HTTPError):
        client.chat([{"role": "user", "content": "x"}], model="free-rotate")


def test_non_rotate_model_bypasses_rotation(monkeypatch):
    """If caller asks for a specific model, no rotation, no /models call."""
    def boom(*a, **k):
        raise AssertionError("rotation path was hit; should have used direct model")
    monkeypatch.setattr(orf.requests, "get", boom)

    def fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResp(
            {
                "choices": [{"message": {"content": "direct"}}],
                "model": json["model"],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        )
    monkeypatch.setattr(orc_mod.http_retry, "post", fake_post)

    client = orc_mod.OpenRouterClient(api_key="sk-test")
    res = client.chat([{"role": "user", "content": "x"}], model="meta-llama/llama-3.3-70b-instruct:free")
    assert res.text == "direct"
