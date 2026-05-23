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
from .litellm_client import LiteLLMClient
from .nvidia_client import NvidiaClient
from .ollama_client import OllamaClient
from .openrouter_client import OpenRouterClient

load_dotenv()
logger = logging.getLogger(__name__)


# Fallback chain when a task_type has no explicit override and the preferred
# provider for that model id is unavailable. Order = preference.
# `litellm` sits at the end — only used when an explicit `litellm:<model>`
# is given, or when every dedicated client is unavailable.
DEFAULT_PROVIDER_ORDER = ("anthropic", "openrouter", "groq", "nvidia", "ollama", "litellm")

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


def _call_timeout() -> float | None:
    """Returns ROUTER_FALLBACK_TIMEOUT_S as float, or None if unset/invalid."""
    raw = os.getenv("ROUTER_FALLBACK_TIMEOUT_S", "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
        return v if v > 0 else None
    except ValueError:
        return None


def _is_hard_failure(e: BaseException) -> bool:
    """Auth/config errors trip the breaker immediately; rate limits and 5xx don't."""
    import requests as _r
    if isinstance(e, _r.exceptions.HTTPError):
        status = e.response.status_code if e.response is not None else 0
        if status in (401, 403):
            return True
        # 429 / 5xx are transient — don't trip the breaker hard.
        return False
    if isinstance(e, ProviderUnavailable):
        # Missing creds = hard
        return True
    return False


class ModelRouter:
    def __init__(self, providers: Optional[Dict[str, BaseProvider]] = None):
        if providers is None:
            providers = {
                "anthropic": AnthropicClient(),
                "openrouter": OpenRouterClient(),
                "groq": GroqClient(),
                "nvidia": NvidiaClient(),
                "ollama": OllamaClient(),
                "litellm": LiteLLMClient(),
            }
        self.providers = providers

    def available_providers(self) -> List[str]:
        from .circuit import get_breaker
        bk = get_breaker()
        return [name for name, p in self.providers.items() if p.is_available() and not bk.is_open(name)]

    def resolve(self, task_type: str, override_model: str | None = None,
                *, require_vision: bool = False) -> tuple[str, str]:
        """Return (provider_name, model). Picks the first available in fallback chain.
        Providers whose circuit breaker is open are skipped automatically.
        `require_vision=True` restricts the search to vision-capable providers.

        Selection precedence (high → low):
          1. `override_model` argument (explicit caller intent)
          2. `$USE_LEARNED_MODELS=1` + retrospectives data → best historical
             (provider, model) for this task_type
          3. `$LITELLM_PRIMARY=1` → litellm provider for any task_type that
             doesn't have an explicit `$ROUTER_MODEL_<TASK>` override; the
             model id is `$LITELLM_TASK_MODEL_<TASK>` if set, else
             `$LITELLM_DEFAULT_MODEL`, else the LiteLLMClient default.
          4. `$ROUTER_MODEL_<TASK>` env var
          5. `DEFAULT_TASK_MODELS[task_type]` constant
        """
        from .circuit import get_breaker
        from .vision import VISION_CAPABLE
        bk = get_breaker()
        # Step 2: learned-model auto-pick. Cheap query but guarded by env
        # so noisy early data can't override the safe defaults.
        if override_model is None and os.getenv("USE_LEARNED_MODELS", "").strip().lower() in ("1", "true", "yes", "on"):
            try:
                from storage.model_performance import best_model_for
                best = best_model_for(task_type)
            except Exception:  # noqa: BLE001
                best = None
            if best is not None:
                bp, bm = best
                prov = self.providers.get(bp)
                if prov and prov.is_available() and not bk.is_open(bp):
                    if require_vision and bp not in VISION_CAPABLE:
                        pass  # fall through to default selection
                    else:
                        return bp, bm
        # Step 3: litellm primacy. When the user has set $LITELLM_PRIMARY=1
        # and litellm is available, prefer it for every task_type that
        # lacks an explicit per-task ROUTER_MODEL_<TASK> override. The
        # explicit overrides still win so users can pin one task to a
        # dedicated provider while letting litellm handle the rest.
        if (
            override_model is None
            and os.getenv("LITELLM_PRIMARY", "").strip().lower() in ("1", "true", "yes", "on")
            and not os.getenv(f"ROUTER_MODEL_{task_type.upper()}", "").strip()
        ):
            litellm_prov = self.providers.get("litellm")
            if (litellm_prov and litellm_prov.is_available()
                    and not bk.is_open("litellm")
                    and (not require_vision or "litellm" in VISION_CAPABLE)):
                model = (
                    os.getenv(f"LITELLM_TASK_MODEL_{task_type.upper()}", "").strip()
                    or os.getenv("LITELLM_DEFAULT_MODEL", "").strip()
                    or litellm_prov.default_model()
                )
                return "litellm", model
        model_id = override_model or os.getenv(
            f"ROUTER_MODEL_{task_type.upper()}",
            DEFAULT_TASK_MODELS.get(task_type, DEFAULT_TASK_MODELS["simple"]),
        )
        hinted, model = _parse_model_id(model_id)
        order = ([hinted] if hinted else []) + [p for p in DEFAULT_PROVIDER_ORDER if p != hinted]
        for name in order:
            if require_vision and name not in VISION_CAPABLE:
                continue
            prov = self.providers.get(name)
            if prov and prov.is_available() and not bk.is_open(name):
                # If the bare model id was Anthropic-specific but we fell back to
                # another provider, use that provider's default model.
                if name != hinted and hinted is not None:
                    model = prov.default_model()
                return name, model
        which = "vision-capable " if require_vision else ""
        raise ProviderUnavailable(
            f"No {which}provider available for task_type={task_type}. "
            f"Tried: {order}. Set at least one API key in .env, or wait for the "
            f"circuit breaker cooldown."
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
        from .circuit import get_breaker
        from .vision import VISION_CAPABLE, has_images, text_only
        bk = get_breaker()
        needs_vision = has_images(messages)
        provider_name, resolved_model = self.resolve(task_type, model,
                                                     require_vision=needs_vision)
        if needs_vision and provider_name not in VISION_CAPABLE:
            # Defensive: resolver returned non-vision provider — strip images.
            messages = text_only(messages)
        elif not needs_vision and not isinstance(messages[0].get("content"), str):
            # Some callers pass block-form content with text-only. Flatten.
            messages = text_only(messages)
        provider = self.providers[provider_name]
        logger.info("router.chat task=%s provider=%s model=%s", task_type, provider_name, resolved_model)
        import time as _t
        from observability.metrics import record_chat as _mrec, record_error as _merr
        t0 = _t.perf_counter()
        # Optional per-call timeout so a hung provider doesn't block the whole
        # chain. Set ROUTER_FALLBACK_TIMEOUT_S=30 to enable (default: off).
        _timeout_s = _call_timeout()
        try:
            if _timeout_s:
                import concurrent.futures as _cf
                with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                    fut = _ex.submit(provider.chat, messages, resolved_model,
                                     max_tokens=max_tokens, temperature=temperature)
                    try:
                        result = fut.result(timeout=_timeout_s)
                    except _cf.TimeoutError as _te:
                        raise ProviderUnavailable(
                            f"{provider_name} timed out after {_timeout_s}s"
                        ) from _te
            else:
                result = provider.chat(messages, resolved_model, max_tokens=max_tokens, temperature=temperature)
        except Exception as e:  # noqa: BLE001
            bk.record_failure(provider_name, type(e).__name__ + ": " + str(e)[:160],
                              hard=_is_hard_failure(e))
            _mrec(provider=provider_name, task_type=task_type, status="error",
                  latency_s=_t.perf_counter() - t0)
            _merr(kind=type(e).__name__)
            raise
        bk.record_success(provider_name)
        _mrec(provider=result.provider, task_type=task_type, status="ok",
              latency_s=_t.perf_counter() - t0,
              prompt_tokens=result.prompt_tokens,
              completion_tokens=result.completion_tokens)
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
        from .circuit import get_breaker
        from .vision import VISION_CAPABLE, has_images, text_only
        bk = get_breaker()
        needs_vision = has_images(messages)
        provider_name, resolved_model = self.resolve(task_type, model,
                                                     require_vision=needs_vision)
        if needs_vision and provider_name not in VISION_CAPABLE:
            messages = text_only(messages)
        elif not needs_vision and messages and not isinstance(messages[0].get("content"), str):
            messages = text_only(messages)
        provider = self.providers[provider_name]
        logger.info("router.chat_stream task=%s provider=%s model=%s", task_type, provider_name, resolved_model)
        final: ChatResult | None = None
        try:
            for item in provider.chat_stream(messages, resolved_model, max_tokens=max_tokens, temperature=temperature):
                if isinstance(item, ChatResult):
                    final = item
                else:
                    yield item
        except Exception as e:  # noqa: BLE001
            bk.record_failure(provider_name, type(e).__name__ + ": " + str(e)[:160],
                              hard=_is_hard_failure(e))
            raise
        bk.record_success(provider_name)
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
