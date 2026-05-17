from __future__ import annotations

import logging
import os
from typing import List

import requests

from .base import BaseProvider, ChatResult, Message, ProviderUnavailable

logger = logging.getLogger(__name__)


class NvidiaClient(BaseProvider):
    name = "nvidia"
    BASE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("NVCF_API_KEY")

    def is_available(self) -> bool:
        return bool(self.api_key)

    def default_model(self) -> str:
        return os.getenv("ROUTER_NVIDIA_DEFAULT", "meta/llama-3.1-8b-instruct")

    def chat(
        self,
        messages: List[Message],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> ChatResult:
        if not self.is_available():
            raise ProviderUnavailable("NVCF_API_KEY not set")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": 0.95,
        }
        resp = requests.post(self.BASE_URL, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        body = resp.json()
        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError(f"NVIDIA returned no choices: {body!r}")
        usage = body.get("usage") or {}
        return ChatResult(
            text=choices[0]["message"]["content"],
            model=body.get("model", model),
            provider=self.name,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            raw=body,
        )
