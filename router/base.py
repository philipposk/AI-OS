from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TypedDict


class Message(TypedDict):
    role: str
    content: str


@dataclass
class ChatResult:
    text: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ProviderUnavailable(Exception):
    """Raised when a provider has no credentials or its endpoint is unreachable."""


class BaseProvider(ABC):
    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if credentials/endpoint are present. No network calls."""

    @abstractmethod
    def chat(
        self,
        messages: List[Message],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> ChatResult: ...

    @abstractmethod
    def default_model(self) -> str: ...

    def chat_stream(
        self,
        messages: List[Message],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ):
        """Yield text chunks; the LAST yield is a ChatResult with the full text + usage.

        Default impl: call chat() and yield once. Override for real token streaming.
        """
        result = self.chat(messages, model, max_tokens=max_tokens, temperature=temperature)
        yield result.text
        yield result
