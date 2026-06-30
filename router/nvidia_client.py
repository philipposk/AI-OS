from __future__ import annotations

import logging
import os
from typing import List

from . import http_retry
from .base import BaseProvider, ChatResult, Message, ProviderUnavailable
from .rotation import ROTATE_SENTINELS

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

    def _chat_one(
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
        resp = http_retry.post(self.BASE_URL, headers=headers, json=payload, timeout=60)
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

    def chat(
        self,
        messages: List[Message],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> ChatResult:
        if model in ROTATE_SENTINELS:
            from .nvidia_free import NvidiaRotator
            return NvidiaRotator().chat_rotate(self, messages, max_tokens=max_tokens, temperature=temperature)
        return self._chat_one(messages, model, max_tokens=max_tokens, temperature=temperature)

    def chat_stream(self, messages, model, max_tokens=1024, temperature=0.7):
        if model in ROTATE_SENTINELS:
            from .nvidia_free import NvidiaRotator
            models, _ = NvidiaRotator().get_models(self.api_key)
            if not models:
                raise RuntimeError("nvidia: no free models available for streaming")
            model = models[0].id
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": 0.95,
            "stream": True,
        }
        resp = http_retry.post(self.BASE_URL, headers=headers, json=payload, timeout=120, stream=True)
        resp.raise_for_status()
        from ._sse import parse_openai_sse
        text_parts: list[str] = []
        last_chunk: dict = {}
        for frag, chunk in parse_openai_sse(resp):
            if frag:
                text_parts.append(frag)
                yield frag
            last_chunk = chunk
        usage = last_chunk.get("usage") or {}
        yield ChatResult(
            text="".join(text_parts),
            model=last_chunk.get("model", model),
            provider=self.name,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            raw=last_chunk,
        )
