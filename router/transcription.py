"""Speech-to-text providers.

Currently: Groq Whisper-large-v3-turbo (free dev tier, fast, accurate).
Easy to extend: subclass Transcriber, drop in a factory branch.

Why not Anthropic / OpenRouter? Anthropic has no STT endpoint. OpenRouter is
chat-only. NVIDIA NIM exposes Parakeet but its integrate endpoint expects
chunked encoding that's not worth the extra code right now.
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import requests

from .base import ProviderUnavailable

logger = logging.getLogger(__name__)


@dataclass
class Transcript:
    text: str
    model: str
    provider: str
    language: Optional[str] = None
    raw: dict = None  # type: ignore[assignment]


class Transcriber(ABC):
    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def transcribe(self, audio: bytes, *, filename: str = "audio.webm", mime_type: str = "audio/webm",
                   language: Optional[str] = None) -> Transcript: ...


class GroqTranscriber(Transcriber):
    name = "groq"
    BASE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        # whisper-large-v3-turbo is fastest. whisper-large-v3 is more accurate.
        self.model = model or os.getenv("STT_MODEL", "whisper-large-v3-turbo")

    def is_available(self) -> bool:
        return bool(self.api_key)

    def transcribe(self, audio: bytes, *, filename="audio.webm", mime_type="audio/webm",
                   language: Optional[str] = None) -> Transcript:
        if not self.is_available():
            raise ProviderUnavailable("GROQ_API_KEY not set")
        files = {"file": (filename, audio, mime_type)}
        data = {"model": self.model, "response_format": "json"}
        if language:
            data["language"] = language
        resp = requests.post(
            self.BASE_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            files=files,
            data=data,
            timeout=120,
        )
        resp.raise_for_status()
        body = resp.json()
        return Transcript(
            text=(body.get("text") or "").strip(),
            model=self.model,
            provider=self.name,
            language=body.get("language"),
            raw=body,
        )


_singleton: Optional[Transcriber] = None


def get_transcriber() -> Optional[Transcriber]:
    """Pick a transcriber once at first call. Order: Groq → None."""
    global _singleton
    if _singleton is not None:
        return _singleton if _singleton.is_available() else None
    backend = os.getenv("STT_BACKEND", "").strip().lower()
    if backend == "none":
        return None
    candidate = GroqTranscriber()
    if candidate.is_available():
        _singleton = candidate
        logger.info("STT backend: groq (%s)", candidate.model)
        return _singleton
    return None


def reset_for_tests() -> None:
    global _singleton
    _singleton = None
