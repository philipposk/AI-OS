"""LiteLLM-backed provider — single client, 100+ models.

Opt-in path. Use by setting model IDs like `litellm:openai/gpt-4o-mini`,
`litellm:gemini/gemini-2.0-flash`, `litellm:anthropic/claude-haiku-4-5`,
etc. The bare `litellm` prefix routes any model litellm understands.

Why parallel-path instead of replacing the dedicated clients:
  - existing clients (anthropic, groq, nvidia, openrouter, ollama) have
    per-provider free-tier rotation logic, vision routing, and accounting
    hooks tested in 260 tests. Ripping them out risks regressions.
  - litellm gives an instant escape hatch to any provider not yet in our
    dedicated set (Gemini, Mistral, Cohere, Together, Fireworks, etc.)
    without writing a new client.

Credentials: litellm reads provider keys from the same env vars our
dedicated clients use (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY,
GROQ_API_KEY, ...). Add the relevant key to `.env` and the model is live.
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from .base import BaseProvider, ChatResult, Message, ProviderUnavailable

logger = logging.getLogger(__name__)


def _litellm_available() -> bool:
    try:
        import litellm  # noqa: F401
        return True
    except ImportError:
        return False


def _has_any_provider_key() -> bool:
    """Cheap check: at least one common LLM provider key is set in env.
    Avoids advertising litellm as available with zero credentials.
    """
    keys = (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
        "GROQ_API_KEY", "MISTRAL_API_KEY", "COHERE_API_KEY",
        "TOGETHER_API_KEY", "FIREWORKS_API_KEY", "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY", "PERPLEXITYAI_API_KEY", "XAI_API_KEY",
    )
    return any(os.getenv(k, "").strip() for k in keys)


class LiteLLMClient(BaseProvider):
    name = "litellm"

    def __init__(self, default: str = "openai/gpt-4o-mini"):
        self._default = os.getenv("LITELLM_DEFAULT_MODEL", default)

    def is_available(self) -> bool:
        return _litellm_available() and _has_any_provider_key()

    def default_model(self) -> str:
        return self._default

    def chat(
        self,
        messages: List[Message],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> ChatResult:
        if not _litellm_available():
            raise ProviderUnavailable("litellm not installed; pip install litellm")
        import litellm

        try:
            resp = litellm.completion(
                model=model,
                messages=list(messages),
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as e:  # noqa: BLE001
            # Re-raise as a recognizable error so the circuit breaker classifies it.
            raise

        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None) or {}
        prompt_tokens = int(getattr(usage, "prompt_tokens", None) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", None) or 0)
        return ChatResult(
            text=text,
            model=getattr(resp, "model", model),
            provider="litellm",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            raw={"id": getattr(resp, "id", "")},
        )

    def chat_stream(
        self,
        messages: List[Message],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ):
        if not _litellm_available():
            raise ProviderUnavailable("litellm not installed; pip install litellm")
        import litellm

        chunks: list[str] = []
        last_model = model
        try:
            stream = litellm.completion(
                model=model,
                messages=list(messages),
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )
            for ev in stream:
                try:
                    delta = ev.choices[0].delta.content
                except Exception:  # noqa: BLE001
                    delta = None
                if delta:
                    chunks.append(delta)
                    yield delta
                last_model = getattr(ev, "model", last_model) or last_model
        except Exception:  # noqa: BLE001
            raise

        full = "".join(chunks)
        # Stream usage isn't always populated; estimate as 0 if missing.
        yield ChatResult(
            text=full,
            model=last_model,
            provider="litellm",
            prompt_tokens=0,
            completion_tokens=0,
            raw={},
        )
