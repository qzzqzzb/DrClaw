"""Reusable test doubles and response factories for the LLM provider layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from drclaw.models.project import Project
from drclaw.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class MockProvider(LLMProvider):
    """Deterministic LLM provider for testing.

    Queued responses are returned FIFO.  When the queue is exhausted, a default
    text-only response is returned so tests never raise unexpectedly.
    """

    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self._responses: list[LLMResponse] = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools, "system": system})
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(
            content="mock response",
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=5,
        )

    def queue(self, *responses: LLMResponse) -> None:
        """Enqueue responses to be returned by future complete() calls."""
        self._responses.extend(responses)


# ---------------------------------------------------------------------------
# Response factories — reusable across test files
# ---------------------------------------------------------------------------


def make_text_response(text: str) -> LLMResponse:
    """Build a simple text-only LLM response."""
    return LLMResponse(
        content=text,
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=5,
    )


def make_tool_response(
    tool_name: str, arguments: dict[str, Any], call_id: str = "tc1"
) -> LLMResponse:
    """Build an LLM response that requests a single tool call."""
    return LLMResponse(
        content=None,
        tool_calls=[ToolCallRequest(id=call_id, name=tool_name, arguments=arguments)],
        stop_reason="tool_use",
        input_tokens=10,
        output_tokens=5,
    )


def make_error_response() -> LLMResponse:
    """Build an LLM response with stop_reason='error'."""
    return LLMResponse(
        content=None,
        tool_calls=[],
        stop_reason="error",
        input_tokens=0,
        output_tokens=0,
    )


def make_project(project_id: str = "test-proj-abc12345", name: str = "Test Project") -> Project:
    """Build a Project dataclass for testing."""
    now = datetime.now(tz=timezone.utc)
    return Project(id=project_id, name=name, created_at=now, updated_at=now)
