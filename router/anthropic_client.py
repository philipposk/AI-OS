from __future__ import annotations

import logging
import os
from typing import List

from .base import BaseProvider, ChatResult, Message, ProviderUnavailable

logger = logging.getLogger(__name__)


class AnthropicClient(BaseProvider):
    name = "anthropic"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._sdk = None

    def is_available(self) -> bool:
        return bool(self.api_key)

    def default_model(self) -> str:
        return os.getenv("ROUTER_ANTHROPIC_DEFAULT", "claude-haiku-4-5")

    def _client(self):
        if self._sdk is None:
            try:
                from anthropic import Anthropic
            except ImportError as e:
                raise ProviderUnavailable(
                    "anthropic SDK not installed. `pip install anthropic`"
                ) from e
            self._sdk = Anthropic(api_key=self.api_key)
        return self._sdk

    def chat(
        self,
        messages: List[Message],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> ChatResult:
        if not self.is_available():
            raise ProviderUnavailable("ANTHROPIC_API_KEY not set")
        system = None
        anth_msgs = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"] if system is None else f"{system}\n\n{m['content']}"
            else:
                anth_msgs.append({"role": m["role"], "content": m["content"]})
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=anth_msgs,
        )
        if system:
            kwargs["system"] = system
        resp = self._client().messages.create(**kwargs)
        text = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
        return ChatResult(
            text=text,
            model=resp.model,
            provider=self.name,
            prompt_tokens=resp.usage.input_tokens,
            completion_tokens=resp.usage.output_tokens,
            raw={"id": resp.id, "stop_reason": resp.stop_reason},
        )

    def chat_stream(self, messages, model, max_tokens=1024, temperature=0.7):
        if not self.is_available():
            raise ProviderUnavailable("ANTHROPIC_API_KEY not set")
        system = None
        anth_msgs = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"] if system is None else f"{system}\n\n{m['content']}"
            else:
                anth_msgs.append({"role": m["role"], "content": m["content"]})
        kwargs = dict(model=model, max_tokens=max_tokens, temperature=temperature, messages=anth_msgs)
        if system:
            kwargs["system"] = system
        text_parts: list[str] = []
        with self._client().messages.stream(**kwargs) as stream:
            for fragment in stream.text_stream:
                if fragment:
                    text_parts.append(fragment)
                    yield fragment
            final = stream.get_final_message()
        yield ChatResult(
            text="".join(text_parts),
            model=final.model,
            provider=self.name,
            prompt_tokens=final.usage.input_tokens,
            completion_tokens=final.usage.output_tokens,
            raw={"id": final.id, "stop_reason": final.stop_reason},
        )
