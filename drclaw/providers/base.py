"""LLM provider abstraction layer."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

ASSISTANT_PROVIDER_FIELDS_KEY = "_provider_fields"


@dataclass
class ToolCallRequest:
    """A tool call requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Normalized response from an LLM provider."""

    content: str | None
    tool_calls: list[ToolCallRequest]
    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens" | "error" | "unknown"
    input_tokens: int
    output_tokens: int
    assistant_metadata: dict[str, Any] | None = None
    model: str | None = None
    input_cost_usd: float = 0.0
    output_cost_usd: float = 0.0
    total_cost_usd: float = 0.0


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> LLMResponse:
        """Call the LLM and return a normalized response."""
        ...
