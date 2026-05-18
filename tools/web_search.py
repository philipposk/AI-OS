"""Web search with provider cascade.

Order (first available wins):

  1. Brave    — $BRAVE_API_KEY,    https://api.search.brave.com/res/v1/web/search
  2. Serper   — $SERPER_API_KEY,   https://google.serper.dev/search
  3. Tavily   — $TAVILY_API_KEY,   https://api.tavily.com/search
  4. DDG html — no key, parses duckduckgo.com/html scrape (best-effort)

Returns list[dict] with keys: title, url, snippet, source (provider name).

Override the search backend with $WEB_SEARCH_BACKEND=brave|serper|tavily|ddg
(forces that one, falls back to "none" if its key is missing).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import List, Optional
from urllib.parse import unquote

import requests

logger = logging.getLogger(__name__)


def _brave(query: str, k: int) -> Optional[List[dict]]:
    key = os.getenv("BRAVE_API_KEY")
    if not key:
        return None
    r = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
        params={"q": query, "count": k},
        timeout=15,
    )
    r.raise_for_status()
    web = (r.json() or {}).get("web") or {}
    out = []
    for h in (web.get("results") or [])[:k]:
        out.append({
            "title": h.get("title", "").strip(),
            "url": h.get("url", ""),
            "snippet": h.get("description", "").strip(),
            "source": "brave",
        })
    return out


def _serper(query: str, k: int) -> Optional[List[dict]]:
    key = os.getenv("SERPER_API_KEY")
    if not key:
        return None
    r = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        data=json.dumps({"q": query, "num": k}),
        timeout=15,
    )
    r.raise_for_status()
    body = r.json() or {}
    out = []
    for h in (body.get("organic") or [])[:k]:
        out.append({
            "title": (h.get("title") or "").strip(),
            "url": h.get("link", ""),
            "snippet": (h.get("snippet") or "").strip(),
            "source": "serper",
        })
    return out


def _tavily(query: str, k: int) -> Optional[List[dict]]:
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return None
    r = requests.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": query, "max_results": k},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json() or {}
    out = []
    for h in (body.get("results") or [])[:k]:
        out.append({
            "title": (h.get("title") or "").strip(),
            "url": h.get("url", ""),
            "snippet": (h.get("content") or "").strip()[:400],
            "source": "tavily",
        })
    return out


_DDG_RESULT_RE = re.compile(
    r'<a\s+rel="nofollow"\s+class="result__a"\s+href="([^"]+)"[^>]*>(.*?)</a>.*?'
    r'<a\s+class="result__snippet"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

_TAG_RE = re.compile(r"<[^>]+>")


def _ddg(query: str, k: int) -> List[dict]:
    """Last-resort scrape. Brittle by design; the API providers are preferred."""
    try:
        r = requests.post(
            "https://duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (ai_company)"},
            timeout=15,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        logger.warning("ddg search failed: %s", e)
        return []
    out: List[dict] = []
    for m in _DDG_RESULT_RE.finditer(r.text):
        url = m.group(1)
        # DDG often wraps in /l/?uddg=<encoded>
        if "uddg=" in url:
            try:
                url = unquote(url.split("uddg=")[1].split("&")[0])
            except Exception:
                pass
        title = _TAG_RE.sub("", m.group(2)).strip()
        snippet = _TAG_RE.sub("", m.group(3)).strip()
        out.append({"title": title, "url": url, "snippet": snippet, "source": "ddg"})
        if len(out) >= k:
            break
    return out


def _backend_override() -> Optional[str]:
    raw = os.getenv("WEB_SEARCH_BACKEND", "").strip().lower()
    return raw or None


def search(query: str, *, k: int = 5) -> List[dict]:
    """Try providers in cascade order; return first non-empty result list."""
    if not query.strip():
        return []
    forced = _backend_override()
    cascade = [("brave", _brave), ("serper", _serper), ("tavily", _tavily)]
    if forced:
        cascade = [(n, fn) for n, fn in cascade if n == forced]
    for name, fn in cascade:
        try:
            res = fn(query, k)
        except Exception as e:  # noqa: BLE001
            logger.warning("%s search failed: %s", name, e)
            res = None
        if res:
            return res

    if forced and forced != "ddg":
        return []
    # Final fallback
    if forced in (None, "ddg"):
        return _ddg(query, k)
    return []


def available_backend() -> Optional[str]:
    """Which backend would `search()` use, given env? None = ddg or nothing."""
    forced = _backend_override()
    if forced:
        return forced
    for name in ("brave", "serper", "tavily"):
        if os.getenv({"brave": "BRAVE_API_KEY", "serper": "SERPER_API_KEY", "tavily": "TAVILY_API_KEY"}[name]):
            return name
    return "ddg"
