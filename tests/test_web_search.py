"""Web search cascade tests. No network."""
from __future__ import annotations

from typing import Any, Dict

import pytest
import requests

from tools import web_search as ws


class _Resp:
    def __init__(self, payload, status=200, text=""):
        self._p = payload
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} fake", response=self)

    def json(self):
        return self._p


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("BRAVE_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY", "WEB_SEARCH_BACKEND"):
        monkeypatch.delenv(k, raising=False)


def test_brave_used_first_when_key_set(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "k1")
    called: Dict[str, Any] = {}
    def fake_get(url, headers=None, params=None, timeout=None):
        called["url"] = url
        called["header"] = headers["X-Subscription-Token"]
        return _Resp({"web": {"results": [
            {"title": "Brave title", "url": "https://a", "description": "snip"},
        ]}})
    monkeypatch.setattr(ws.requests, "get", fake_get)

    out = ws.search("hello")
    assert out and out[0]["source"] == "brave"
    assert called["url"].startswith("https://api.search.brave.com")
    assert called["header"] == "k1"


def test_serper_used_when_brave_absent(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "k2")
    def fake_post(url, headers=None, data=None, json=None, timeout=None):
        return _Resp({"organic": [{"title": "S", "link": "https://s", "snippet": "x"}]})
    monkeypatch.setattr(ws.requests, "post", fake_post)
    out = ws.search("hello")
    assert out[0]["source"] == "serper"


def test_tavily_used_when_brave_and_serper_absent(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k3")
    def fake_post(url, headers=None, data=None, json=None, timeout=None):
        return _Resp({"results": [{"title": "T", "url": "https://t", "content": "x"}]})
    monkeypatch.setattr(ws.requests, "post", fake_post)
    out = ws.search("hello")
    assert out[0]["source"] == "tavily"


def test_ddg_when_no_keys(monkeypatch):
    # Real DDG hit; mock the HTML response so we don't go to network.
    html = (
        '<a rel="nofollow" class="result__a" href="https://x.example/page">Result <b>title</b></a>'
        '<a class="result__snippet">A snippet here</a>'
    )
    def fake_post(url, data=None, headers=None, timeout=None):
        return _Resp({}, text=html)
    monkeypatch.setattr(ws.requests, "post", fake_post)
    out = ws.search("hello")
    assert out and out[0]["source"] == "ddg"
    assert out[0]["url"] == "https://x.example/page"
    assert "title" in out[0]["title"].lower()


def test_forced_backend(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "kb")
    monkeypatch.setenv("SERPER_API_KEY", "ks")
    monkeypatch.setenv("WEB_SEARCH_BACKEND", "serper")  # force the lower one

    calls = []
    def fake_get(*a, **k): calls.append("brave-get"); return _Resp({"web": {"results": []}})
    def fake_post(*a, **k):
        calls.append("serper-post")
        return _Resp({"organic": [{"title": "S", "link": "https://s", "snippet": "x"}]})
    monkeypatch.setattr(ws.requests, "get", fake_get)
    monkeypatch.setattr(ws.requests, "post", fake_post)

    out = ws.search("hello")
    assert out[0]["source"] == "serper"
    assert "brave-get" not in calls   # forced — skipped Brave entirely


def test_empty_query_returns_empty():
    assert ws.search("") == []
    assert ws.search("    ") == []


def test_provider_failure_falls_through(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "kb")
    monkeypatch.setenv("SERPER_API_KEY", "ks")

    def fake_get(*a, **k): raise requests.ConnectionError("brave down")
    def fake_post(url, headers=None, data=None, json=None, timeout=None):
        return _Resp({"organic": [{"title": "S", "link": "https://s", "snippet": "x"}]})
    monkeypatch.setattr(ws.requests, "get", fake_get)
    monkeypatch.setattr(ws.requests, "post", fake_post)

    out = ws.search("q")
    assert out[0]["source"] == "serper"


def test_available_backend_reports(monkeypatch):
    assert ws.available_backend() == "ddg"
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    assert ws.available_backend() == "tavily"
    monkeypatch.setenv("BRAVE_API_KEY", "y")
    assert ws.available_backend() == "brave"
    monkeypatch.setenv("WEB_SEARCH_BACKEND", "serper")
    assert ws.available_backend() == "serper"
