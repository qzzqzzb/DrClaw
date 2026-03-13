"""Tests for the LLM provider layer."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from drclaw.config.schema import ProviderConfig
from drclaw.providers.base import LLMProvider, LLMResponse
from drclaw.providers.litellm_provider import LiteLLMProvider
from tests.mocks import MockProvider

# ---------------------------------------------------------------------------
# MockProvider tests
# ---------------------------------------------------------------------------


async def test_mock_provider_is_llm_provider(mock_provider: MockProvider) -> None:
    """MockProvider satisfies the LLMProvider interface contract."""
    assert isinstance(mock_provider, LLMProvider)


async def test_mock_provider_returns_default_text_response(mock_provider: MockProvider) -> None:
    """MockProvider returns a sensible default when no responses are queued."""
    resp = await mock_provider.complete([{"role": "user", "content": "hello"}])
    assert resp.content == "mock response"
    assert resp.tool_calls == []
    assert resp.stop_reason == "end_turn"
    assert resp.input_tokens > 0
    assert resp.output_tokens > 0


async def test_mock_provider_returns_queued_responses_in_order(
    mock_provider: MockProvider,
) -> None:
    """Queued LLMResponse objects are consumed FIFO."""
    first = LLMResponse(
        content="first", tool_calls=[], stop_reason="end_turn", input_tokens=1, output_tokens=1
    )
    second = LLMResponse(
        content="second", tool_calls=[], stop_reason="end_turn", input_tokens=2, output_tokens=2
    )
    mock_provider.queue(first, second)

    r1 = await mock_provider.complete([])
    r2 = await mock_provider.complete([])
    assert r1.content == "first"
    assert r2.content == "second"


async def test_mock_provider_records_calls(mock_provider: MockProvider) -> None:
    """MockProvider records every complete() invocation for assertions."""
    msgs = [{"role": "user", "content": "ping"}]
    tools = [{"type": "function", "function": {"name": "noop"}}]
    await mock_provider.complete(msgs, tools=tools, system="sys")

    assert len(mock_provider.calls) == 1
    call = mock_provider.calls[0]
    assert call["messages"] == msgs
    assert call["tools"] == tools
    assert call["system"] == "sys"


# ---------------------------------------------------------------------------
# LiteLLMProvider._parse_response tests (no network)
# ---------------------------------------------------------------------------


def _make_litellm_response(
    content: str | None = None,
    tool_calls: list[Any] | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> Any:
    """Build a minimal litellm-shaped response object."""
    tc_objs = []
    for tc in tool_calls or []:
        func = SimpleNamespace(name=tc["name"], arguments=json.dumps(tc["arguments"]))
        tc_objs.append(SimpleNamespace(id=tc["id"], function=func))

    message = SimpleNamespace(content=content, tool_calls=tc_objs or None)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


async def test_litellm_provider_parses_text_response() -> None:
    """LiteLLMProvider converts a plain-text litellm response correctly."""
    provider = LiteLLMProvider(ProviderConfig(model="openai/gpt-4o"))
    raw = _make_litellm_response(content="hello world", finish_reason="stop")

    with patch("litellm.acompletion", new=AsyncMock(return_value=raw)):
        resp = await provider.complete([{"role": "user", "content": "hi"}])

    assert resp.content == "hello world"
    assert resp.tool_calls == []
    assert resp.stop_reason == "end_turn"
    assert resp.input_tokens == 10
    assert resp.output_tokens == 5


async def test_litellm_provider_parses_cost_fields() -> None:
    provider = LiteLLMProvider(ProviderConfig(model="openai/gpt-4o"))
    raw = _make_litellm_response(content="hello world", finish_reason="stop")
    raw.model = "openai/gpt-4o"
    raw._hidden_params = {}

    with (
        patch("litellm.acompletion", new=AsyncMock(return_value=raw)),
        patch("litellm.cost_per_token", return_value=(0.001, 0.002)),
    ):
        resp = await provider.complete([{"role": "user", "content": "hi"}])

    assert resp.model == "openai/gpt-4o"
    assert resp.input_cost_usd == 0.001
    assert resp.output_cost_usd == 0.002
    assert resp.total_cost_usd == 0.003


async def test_litellm_provider_prefers_hidden_total_cost() -> None:
    provider = LiteLLMProvider(ProviderConfig(model="openai/gpt-4o"))
    raw = _make_litellm_response(content="hello world", finish_reason="stop")
    raw.model = "openai/gpt-4o"
    raw._hidden_params = {"response_cost": "0.123"}

    with (
        patch("litellm.acompletion", new=AsyncMock(return_value=raw)),
        patch("litellm.cost_per_token", return_value=(0.001, 0.002)),
    ):
        resp = await provider.complete([{"role": "user", "content": "hi"}])

    assert resp.input_cost_usd == 0.001
    assert resp.output_cost_usd == 0.002
    assert resp.total_cost_usd == 0.123


def test_litellm_provider_normalizes_claude_cost_lookup_aliases() -> None:
    candidates = LiteLLMProvider._cost_lookup_model_candidates(
        "anthropic/claude-4.6-opus-20260205"
    )

    assert candidates[0] == "anthropic/claude-4.6-opus-20260205"
    assert "anthropic/claude-opus-4-6-20260205" in candidates
    assert "anthropic/claude-opus-4-6" in candidates
    assert "claude-opus-4-6-20260205" in candidates
    assert "claude-opus-4-6" in candidates


async def test_litellm_provider_falls_back_to_normalized_cost_model() -> None:
    provider = LiteLLMProvider(ProviderConfig(model="anthropic/claude-4.6-opus-20260205"))
    raw = _make_litellm_response(content="hello world", finish_reason="stop")
    raw.model = "anthropic/claude-4.6-opus-20260205"
    raw._hidden_params = {}

    def _cost_side_effect(*args: Any, **kwargs: Any) -> tuple[float, float]:
        model_name = kwargs["model"]
        if model_name == "anthropic/claude-4.6-opus-20260205":
            raise Exception("unknown model alias")
        if model_name == "anthropic/claude-opus-4-6-20260205":
            return (0.005, 0.025)
        raise AssertionError(f"unexpected lookup model: {model_name}")

    with (
        patch("litellm.acompletion", new=AsyncMock(return_value=raw)),
        patch("litellm.cost_per_token", side_effect=_cost_side_effect) as mock_cost,
    ):
        resp = await provider.complete([{"role": "user", "content": "hi"}])

    assert resp.model == "anthropic/claude-4.6-opus-20260205"
    assert resp.input_cost_usd == 0.005
    assert resp.output_cost_usd == 0.025
    assert resp.total_cost_usd == pytest.approx(0.03)
    attempted_models = [kwargs["model"] for _, kwargs in mock_cost.call_args_list]
    assert attempted_models[:2] == [
        "anthropic/claude-4.6-opus-20260205",
        "anthropic/claude-opus-4-6-20260205",
    ]


async def test_litellm_provider_parses_tool_calls() -> None:
    """LiteLLMProvider converts tool_calls from litellm format to ToolCallRequest."""
    provider = LiteLLMProvider(ProviderConfig(model="openai/gpt-4o"))
    raw = _make_litellm_response(
        finish_reason="tool_calls",
        tool_calls=[{"id": "tc1", "name": "search", "arguments": {"query": "drclaw"}}],
    )

    with patch("litellm.acompletion", new=AsyncMock(return_value=raw)):
        resp = await provider.complete([{"role": "user", "content": "search"}])

    assert resp.stop_reason == "tool_use"
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.id == "tc1"
    assert tc.name == "search"
    assert tc.arguments == {"query": "drclaw"}


async def test_litellm_provider_maps_finish_reasons() -> None:
    """finish_reason values are mapped to normalized stop_reason strings."""
    provider = LiteLLMProvider(ProviderConfig(model="openai/gpt-4o"))

    cases = [
        ("stop", "end_turn"),
        ("tool_calls", "tool_use"),
        ("length", "max_tokens"),
        ("error", "error"),
    ]
    for finish_reason, expected in cases:
        raw = _make_litellm_response(content="x", finish_reason=finish_reason)
        with patch("litellm.acompletion", new=AsyncMock(return_value=raw)):
            resp = await provider.complete([])
        assert resp.stop_reason == expected, f"{finish_reason!r} → expected {expected!r}"


async def test_litellm_provider_recovers_from_malformed_tool_call_json() -> None:
    """Malformed tool-call JSON should not crash provider parsing."""
    provider = LiteLLMProvider(ProviderConfig(model="openai/gpt-4o"))
    bad_func = SimpleNamespace(
        name="write_file",
        arguments='{"path":"report.md" "content":"oops"}',
    )
    message = SimpleNamespace(
        content=None,
        tool_calls=[SimpleNamespace(id="tc1", function=bad_func)],
    )
    raw = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )

    with patch("litellm.acompletion", new=AsyncMock(return_value=raw)):
        resp = await provider.complete([{"role": "user", "content": "write"}])

    assert resp.stop_reason == "tool_use"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "write_file"
    assert "__parse_error__" in resp.tool_calls[0].arguments
    assert "__raw_arguments__" in resp.tool_calls[0].arguments


async def test_litellm_provider_coerces_non_object_tool_args() -> None:
    """Tool-call args parsed as non-object JSON are coerced to an object."""
    provider = LiteLLMProvider(ProviderConfig(model="openai/gpt-4o"))
    weird_func = SimpleNamespace(
        name="list_dir",
        arguments='["not","an","object"]',
    )
    message = SimpleNamespace(
        content=None,
        tool_calls=[SimpleNamespace(id="tc1", function=weird_func)],
    )
    raw = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )

    with patch("litellm.acompletion", new=AsyncMock(return_value=raw)):
        resp = await provider.complete([{"role": "user", "content": "list"}])

    assert resp.stop_reason == "tool_use"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "list_dir"
    assert resp.tool_calls[0].arguments == {"__raw_arguments__": ["not", "an", "object"]}
