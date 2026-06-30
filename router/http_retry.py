"""Shared HTTP helpers with transient-error retries for provider clients."""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

_DEFAULT_RETRIES = int(__import__("os").getenv("ROUTER_HTTP_RETRIES", "3"))
_DEFAULT_BACKOFF = float(__import__("os").getenv("ROUTER_HTTP_BACKOFF", "0.5"))


def _build_session(retries: int = _DEFAULT_RETRIES, backoff: float = _DEFAULT_BACKOFF) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_SESSION: Optional[requests.Session] = None


def get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = _build_session()
    return _SESSION


def post(url: str, *, timeout: float, stream: bool = False, **kwargs: Any) -> requests.Response:
    try:
        return get_session().post(url, timeout=timeout, stream=stream, **kwargs)
    except requests.RequestException as exc:
        logger.warning("HTTP POST failed after retries: %s %s", url, exc)
        raise


def get(url: str, *, timeout: float, **kwargs: Any) -> requests.Response:
    try:
        return get_session().get(url, timeout=timeout, **kwargs)
    except requests.RequestException as exc:
        logger.warning("HTTP GET failed after retries: %s %s", url, exc)
        raise
