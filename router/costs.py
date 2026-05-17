"""Per-million-token USD prices. Source of truth for billing.

Numbers reflect public list prices as of early 2026. Override per env if needed.
Free-tier providers (Ollama, NVIDIA NIM build endpoints, OpenRouter `:free` tags,
Groq dev tier) cost $0.
"""
from __future__ import annotations

from typing import Tuple

# (input $/MTok, output $/MTok)
PRICES: dict[str, Tuple[float, float]] = {
    # Anthropic
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (15.0, 75.0),
    # OpenRouter free tier
    "meta-llama/llama-3.2-3b-instruct:free": (0.0, 0.0),
    # NVIDIA NIM free integrate endpoints
    "meta/llama-3.1-8b-instruct": (0.0, 0.0),
    # Groq dev tier (free w/ rate limits)
    "llama-3.3-70b-versatile": (0.0, 0.0),
    # Ollama (local)
    "llama3.2:3b": (0.0, 0.0),
}


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return USD cost; unknown models priced at 0.0 (logged elsewhere)."""
    in_price, out_price = PRICES.get(model, (0.0, 0.0))
    return (prompt_tokens / 1_000_000) * in_price + (completion_tokens / 1_000_000) * out_price
