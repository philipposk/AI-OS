from __future__ import annotations

import logging
import os
from typing import List

import requests

from .base import BaseProvider, ChatResult, Message, ProviderUnavailable

logger = logging.getLogger(__name__)


class OllamaClient(BaseProvider):
    name = "ollama"

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")

    def is_available(self) -> bool:
        # Cheap: assume reachable if URL is set. We don't probe here to keep init sync + offline-safe.
        return bool(self.base_url)

    def default_model(self) -> str:
        return os.getenv("ROUTER_OLLAMA_DEFAULT", "llama3.2:3b")

    def chat(
        self,
        messages: List[Message],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> ChatResult:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        try:
            resp = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=120)
        except requests.exceptions.ConnectionError as e:
            raise ProviderUnavailable(f"Ollama not reachable at {self.base_url}: {e}") from e
        resp.raise_for_status()
        body = resp.json()
        msg = body.get("message") or {}
        return ChatResult(
            text=msg.get("content", ""),
            model=body.get("model", model),
            provider=self.name,
            prompt_tokens=int(body.get("prompt_eval_count", 0)),
            completion_tokens=int(body.get("eval_count", 0)),
            raw=body,
        )

    def chat_stream(self, messages, model, max_tokens=1024, temperature=0.7):
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        try:
            resp = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=120, stream=True)
        except requests.exceptions.ConnectionError as e:
            raise ProviderUnavailable(f"Ollama not reachable at {self.base_url}: {e}") from e
        resp.raise_for_status()
        import json
        text_parts: list[str] = []
        prompt_tokens = 0
        completion_tokens = 0
        last_model = model
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message") or {}
            frag = msg.get("content") or ""
            if frag:
                text_parts.append(frag)
                yield frag
            last_model = obj.get("model", last_model)
            if obj.get("done"):
                prompt_tokens = int(obj.get("prompt_eval_count", 0))
                completion_tokens = int(obj.get("eval_count", 0))
        yield ChatResult(
            text="".join(text_parts),
            model=last_model,
            provider=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            raw={},
        )
