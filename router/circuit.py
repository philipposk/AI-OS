"""Per-provider circuit breaker.

After `failure_threshold` consecutive failures the provider is marked
"open" and skipped (treated as unavailable by ModelRouter.resolve) for
`cooldown_seconds`. A single success closes it again. Thread-safe.

Hooked into ModelRouter.chat / chat_stream: success → record_success,
network/server/HTTP-5xx failure → record_failure. Rate-limit 429s do NOT
trip the breaker (they're expected on free tiers; rotation handles
them). Auth errors (401/403) trip immediately and stay open the full
cooldown — they aren't a transient.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Dict


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class _State:
    failures: int = 0
    opened_at: float = 0.0      # 0 = closed
    last_error: str = ""


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int | None = None, cooldown_seconds: float | None = None):
        self.failure_threshold = failure_threshold if failure_threshold is not None else _env_int("CIRCUIT_FAILURE_THRESHOLD", 3)
        self.cooldown_seconds = cooldown_seconds if cooldown_seconds is not None else _env_float("CIRCUIT_COOLDOWN_SECONDS", 60.0)
        self._states: Dict[str, _State] = {}
        self._lock = threading.Lock()

    def _state(self, provider: str) -> _State:
        s = self._states.get(provider)
        if s is None:
            s = _State()
            self._states[provider] = s
        return s

    def is_open(self, provider: str, now: float | None = None) -> bool:
        now = now or time.time()
        with self._lock:
            s = self._state(provider)
            if s.opened_at == 0.0:
                return False
            if now - s.opened_at >= self.cooldown_seconds:
                # Half-open: allow the next attempt; reset on success/failure
                # in record_success / record_failure.
                return False
            return True

    def record_success(self, provider: str) -> None:
        with self._lock:
            s = self._state(provider)
            s.failures = 0
            s.opened_at = 0.0
            s.last_error = ""

    def record_failure(self, provider: str, error: str, *, hard: bool = False) -> None:
        """`hard=True` → trip immediately (auth errors, 5xx-from-misconfig)."""
        with self._lock:
            s = self._state(provider)
            s.failures += 1
            s.last_error = (error or "")[:200]
            if hard or s.failures >= self.failure_threshold:
                s.opened_at = time.time()

    def snapshot(self) -> Dict[str, Dict[str, object]]:
        now = time.time()
        out: Dict[str, Dict[str, object]] = {}
        with self._lock:
            for name, s in self._states.items():
                remaining = 0.0
                if s.opened_at > 0:
                    remaining = max(0.0, self.cooldown_seconds - (now - s.opened_at))
                out[name] = {
                    "failures": s.failures,
                    "open": remaining > 0,
                    "cooldown_remaining_s": round(remaining, 1),
                    "last_error": s.last_error,
                }
        return out

    def reset(self, provider: str | None = None) -> None:
        with self._lock:
            if provider is None:
                self._states.clear()
            else:
                self._states.pop(provider, None)


_singleton: CircuitBreaker | None = None


def get_breaker() -> CircuitBreaker:
    global _singleton
    if _singleton is None:
        _singleton = CircuitBreaker()
    return _singleton


def reset_for_tests() -> None:
    global _singleton
    _singleton = None
