"""ModelRouter — single entry point for all LLM calls.

Maps a `task_type` ("analyze" / "plan" / "code" / "review" / etc.) to
(provider, model). Falls back across providers when the preferred one is
unavailable. Every successful call records to the accounting ledger.

Task → model mapping is driven by env vars so the user can swap models
without touching code:

    ROUTER_MODEL_ANALYZE=claude-haiku-4-5
    ROUTER_MODEL_PLAN=claude-opus-4-7
    ROUTER_MODEL_CODE=claude-sonnet-4-6
    ROUTER_MODEL_REVIEW=claude-haiku-4-5

Model id format: either bare `claude-haiku-4-5` (resolves to anthropic) or
`provider:model_id`, e.g. `openrouter:meta-llama/llama-3.2-3b-instruct:free`.
"""
from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

from dotenv import load_dotenv

from storage.accounting import record as record_call

from .anthropic_client import AnthropicClient
from .base import BaseProvider, ChatResult, Message, ProviderUnavailable
from .groq_client import GroqClient
from .nvidia_client import NvidiaClient
from .ollama_client import OllamaClient
from .openrouter_client import OpenRouterClient

load_dotenv()
logger = logging.getLogger(__name__)


# Fallback chain when a task_type has no explicit override and the preferred
# provider for that model id is unavailable. Order = preference.
DEFAULT_PROVIDER_ORDER = ("anthropic", "openrouter", "groq", "nvidia", "ollama")

# Default model per task_type if env var not set. Picked to be cheap-or-free.
DEFAULT_TASK_MODELS: Dict[str, str] = {
    "analyze": "claude-haiku-4-5",
    "plan": "claude-haiku-4-5",
    "code": "claude-sonnet-4-6",
    "review": "claude-haiku-4-5",
    "summarize": "claude-haiku-4-5",
    "test": "claude-haiku-4-5",
    "simple": "openrouter:meta-llama/llama-3.2-3b-instruct:free",
}

# Map bare model id → provider (for shorthand resolution).
MODEL_PROVIDER_HINT: Dict[str, str] = {
    "claude-opus-4-7": "anthropic",
    "claude-sonnet-4-6": "anthropic",
    "claude-haiku-4-5": "anthropic",
    "llama-3.3-70b-versatile": "groq",
    "meta/llama-3.1-8b-instruct": "nvidia",
    "llama3.2:3b": "ollama",
}


def _parse_model_id(model_id: str) -> tuple[str | None, str]:
    """`provider:model` → (provider, model). Bare → (hinted_provider_or_none, model)."""
    if ":" in model_id:
        head, rest = model_id.split(":", 1)
        if head in DEFAULT_PROVIDER_ORDER:
            return head, rest
    return MODEL_PROVIDER_HINT.get(model_id), model_id


class ModelRouter:
    def __init__(self, providers: Optional[Dict[str, BaseProvider]] = None):
        if providers is None:
            providers = {
                "anthropic": AnthropicClient(),
                "openrouter": OpenRouterClient(),
                "groq": GroqClient(),
                "nvidia": NvidiaClient(),
                "ollama": OllamaClient(),
            }
        self.providers = providers

    def available_providers(self) -> List[str]:
        return [name for name, p in self.providers.items() if p.is_available()]

    def resolve(self, task_type: str, override_model: str | None = None) -> tuple[str, str]:
        """Return (provider_name, model). Picks the first available in fallback chain."""
        model_id = override_model or os.getenv(
            f"ROUTER_MODEL_{task_type.upper()}",
            DEFAULT_TASK_MODELS.get(task_type, DEFAULT_TASK_MODELS["simple"]),
        )
        hinted, model = _parse_model_id(model_id)
        order = ([hinted] if hinted else []) + [p for p in DEFAULT_PROVIDER_ORDER if p != hinted]
        for name in order:
            prov = self.providers.get(name)
            if prov and prov.is_available():
                # If the bare model id was Anthropic-specific but we fell back to
                # another provider, use that provider's default model.
                if name != hinted and hinted is not None:
                    model = prov.default_model()
                return name, model
        raise ProviderUnavailable(
            f"No provider available for task_type={task_type}. "
            f"Tried: {order}. Set at least one API key in .env."
        )

    def chat(
        self,
        messages: List[Message],
        task_type: str = "simple",
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        workflow_id: str | None = None,
    ) -> ChatResult:
        provider_name, resolved_model = self.resolve(task_type, model)
        provider = self.providers[provider_name]
        logger.info("router.chat task=%s provider=%s model=%s", task_type, provider_name, resolved_model)
        result = provider.chat(messages, resolved_model, max_tokens=max_tokens, temperature=temperature)
        record_call(
            provider=result.provider,
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            task_type=task_type,
            workflow_id=workflow_id,
        )
        return result

    def chat_stream(
        self,
        messages: List[Message],
        task_type: str = "simple",
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        workflow_id: str | None = None,
    ):
        """Yield text fragments; last yield is a ChatResult. Records accounting after the stream completes."""
        provider_name, resolved_model = self.resolve(task_type, model)
        provider = self.providers[provider_name]
        logger.info("router.chat_stream task=%s provider=%s model=%s", task_type, provider_name, resolved_model)
        final: ChatResult | None = None
        for item in provider.chat_stream(messages, resolved_model, max_tokens=max_tokens, temperature=temperature):
            if isinstance(item, ChatResult):
                final = item
            else:
                yield item
        if final is not None:
            record_call(
                provider=final.provider,
                model=final.model,
                prompt_tokens=final.prompt_tokens,
                completion_tokens=final.completion_tokens,
                task_type=task_type,
                workflow_id=workflow_id,
            )
            yield final
