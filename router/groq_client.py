from __future__ import annotations

import logging
import os
from typing import List

import requests

from .base import BaseProvider, ChatResult, Message, ProviderUnavailable
from .rotation import ROTATE_SENTINELS

logger = logging.getLogger(__name__)


class GroqClient(BaseProvider):
    name = "groq"
    BASE_URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")

    def is_available(self) -> bool:
        return bool(self.api_key)

    def default_model(self) -> str:
        return os.getenv("ROUTER_GROQ_DEFAULT", "llama-3.3-70b-versatile")

    def _chat_one(
        self,
        messages: List[Message],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> ChatResult:
        if not self.is_available():
            raise ProviderUnavailable("GROQ_API_KEY not set")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        resp = requests.post(self.BASE_URL, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        body = resp.json()
        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError(f"Groq returned no choices: {body!r}")
        usage = body.get("usage") or {}
        return ChatResult(
            text=choices[0]["message"]["content"],
            model=body.get("model", model),
            provider=self.name,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            raw=body,
        )

    def chat(
        self,
        messages: List[Message],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> ChatResult:
        if model in ROTATE_SENTINELS:
            from .groq_free import GroqRotator
            return GroqRotator().chat_rotate(self, messages, max_tokens=max_tokens, temperature=temperature)
        return self._chat_one(messages, model, max_tokens=max_tokens, temperature=temperature)
