"""Tests for OpenAI Codex provider."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from drclaw.config.schema import ProviderConfig
from drclaw.providers.openai_codex_provider import (
    DEFAULT_CODEX_TIMEOUT_SECONDS,
    OpenAICodexProvider,
    _convert_messages,
    _convert_tools,
    _convert_user_message,
    _format_exception,
    _friendly_error,
    _map_finish_reason,
    _split_tool_call_id,
    _strip_model_prefix,
)


# ---------------------------------------------------------------------------
# _strip_model_prefix
# ---------------------------------------------------------------------------

class TestStripModelPrefix:
    def test_dash_prefix(self):
        assert _strip_model_prefix("openai-codex/gpt-5.1-codex") == "gpt-5.1-codex"

    def test_underscore_prefix(self):
        assert _strip_model_prefix("openai_codex/gpt-5.1-codex") == "gpt-5.1-codex"

    def test_no_prefix(self):
        assert _strip_model_prefix("gpt-5.1-codex") == "gpt-5.1-codex"

    def test_other_prefix_unchanged(self):
        assert _strip_model_prefix("anthropic/claude-4") == "anthropic/claude-4"


# ---------------------------------------------------------------------------
# _convert_tools
# ---------------------------------------------------------------------------

class TestConvertTools:
    def test_function_type(self):
        tools = [{
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }]
        result = _convert_tools(tools)
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["name"] == "read_file"
        assert result[0]["description"] == "Read a file"
        assert "properties" in result[0]["parameters"]

    def test_skips_nameless(self):
        tools = [{"type": "function", "function": {}}]
        assert _convert_tools(tools) == []

    def test_flat_format(self):
        tools = [{"name": "foo", "description": "bar", "parameters": {}}]
        result = _convert_tools(tools)
        assert result[0]["name"] == "foo"


# ---------------------------------------------------------------------------
# _convert_messages
# ---------------------------------------------------------------------------

class TestConvertMessages:
    def test_user_text(self):
        msgs = [{"role": "user", "content": "hello"}]
        items = _convert_messages(msgs)
        assert len(items) == 1
        assert items[0]["role"] == "user"
        assert items[0]["content"][0]["type"] == "input_text"
        assert items[0]["content"][0]["text"] == "hello"

    def test_system_skipped(self):
        msgs = [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "hi"},
        ]
        items = _convert_messages(msgs)
        assert len(items) == 1
        assert items[0]["role"] == "user"

    def test_assistant_text(self):
        msgs = [{"role": "assistant", "content": "sure"}]
        items = _convert_messages(msgs)
        assert len(items) == 1
        assert items[0]["type"] == "message"
        assert items[0]["role"] == "assistant"
        assert items[0]["content"][0]["text"] == "sure"

    def test_assistant_tool_calls(self):
        msgs = [{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1|fc_1",
                "function": {"name": "read", "arguments": '{"p":"a"}'},
            }],
        }]
        items = _convert_messages(msgs)
        assert len(items) == 1
        assert items[0]["type"] == "function_call"
        assert items[0]["call_id"] == "call_1"
        assert items[0]["id"] == "fc_1"

    def test_tool_result(self):
        msgs = [{"role": "tool", "tool_call_id": "call_1", "content": "result"}]
        items = _convert_messages(msgs)
        assert len(items) == 1
        assert items[0]["type"] == "function_call_output"
        assert items[0]["call_id"] == "call_1"
        assert items[0]["output"] == "result"


# ---------------------------------------------------------------------------
# _convert_user_message
# ---------------------------------------------------------------------------

class TestConvertUserMessage:
    def test_string(self):
        result = _convert_user_message("hello")
        assert result["content"][0]["text"] == "hello"

    def test_multimodal(self):
        content = [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]
        result = _convert_user_message(content)
        assert len(result["content"]) == 2
        assert result["content"][0]["type"] == "input_text"
        assert result["content"][1]["type"] == "input_image"

    def test_empty_list_fallback(self):
        result = _convert_user_message([])
        assert result["content"][0]["text"] == ""


# ---------------------------------------------------------------------------
# _split_tool_call_id
# ---------------------------------------------------------------------------

class TestSplitToolCallId:
    def test_pipe(self):
        assert _split_tool_call_id("call_1|fc_1") == ("call_1", "fc_1")

    def test_no_pipe(self):
        assert _split_tool_call_id("call_1") == ("call_1", None)

    def test_empty(self):
        assert _split_tool_call_id("") == ("call_0", None)

    def test_none(self):
        assert _split_tool_call_id(None) == ("call_0", None)


# ---------------------------------------------------------------------------
# _map_finish_reason
# ---------------------------------------------------------------------------

class TestMapFinishReason:
    def test_completed(self):
        assert _map_finish_reason("completed") == "end_turn"

    def test_incomplete(self):
        assert _map_finish_reason("incomplete") == "max_tokens"

    def test_failed(self):
        assert _map_finish_reason("failed") == "error"

    def test_cancelled(self):
        assert _map_finish_reason("cancelled") == "error"

    def test_none(self):
        assert _map_finish_reason(None) == "end_turn"

    def test_unknown(self):
        assert _map_finish_reason("xyz") == "end_turn"


# ---------------------------------------------------------------------------
# _friendly_error
# ---------------------------------------------------------------------------

class TestFriendlyError:
    def test_429(self):
        msg = _friendly_error(429, "too many")
        assert "quota" in msg.lower() or "rate limit" in msg.lower()

    def test_other(self):
        msg = _friendly_error(500, "internal")
        assert "500" in msg
        assert "internal" in msg


class TestFormatException:
    def test_prefers_non_empty_str(self):
        exc = RuntimeError("boom")
        assert _format_exception(exc) == "boom"

    def test_falls_back_to_repr_for_empty_str(self):
        exc = httpx.ReadTimeout("")
        formatted = _format_exception(exc)
        assert "ReadTimeout" in formatted


# ---------------------------------------------------------------------------
# OpenAICodexProvider.complete — mocked SSE
# ---------------------------------------------------------------------------

def _make_sse_lines(events: list[dict[str, Any]]) -> list[str]:
    """Build raw SSE line sequence from event dicts."""
    lines: list[str] = []
    for event in events:
        lines.append(f"data: {json.dumps(event)}")
        lines.append("")  # blank line terminates event
    return lines


class _FakeResponse:
    """Minimal stand-in for httpx.Response in streaming mode."""

    def __init__(self, lines: list[str], status_code: int = 200):
        self.status_code = status_code
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b"error body"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _FakeClient:
    def __init__(self, response: _FakeResponse):
        self._response = response

    def stream(self, *_args, **_kwargs):
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class TestCompleteTextResponse:
    async def test_basic_text(self):
        events = [
            {"type": "response.output_text.delta", "delta": "Hello "},
            {"type": "response.output_text.delta", "delta": "world"},
            {"type": "response.completed", "response": {"status": "completed"}},
        ]
        fake_resp = _FakeResponse(_make_sse_lines(events))
        fake_client = _FakeClient(fake_resp)

        config = ProviderConfig(model="openai-codex/gpt-5.1-codex")
        provider = OpenAICodexProvider(config)

        fake_token = MagicMock()
        fake_token.account_id = "acct_123"
        fake_token.access = "tok_abc"

        with (
            patch("drclaw.providers.openai_codex_provider.httpx.AsyncClient", return_value=fake_client),
            patch("oauth_cli_kit.get_token", return_value=fake_token),
        ):
            resp = await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                system="be helpful",
            )

        assert resp.content == "Hello world"
        assert resp.stop_reason == "end_turn"
        assert resp.tool_calls == []
        assert resp.input_tokens == 0
        assert resp.output_tokens == 0
        assert resp.model == "openai-codex/gpt-5.1-codex"


class TestCompleteToolCallResponse:
    async def test_tool_call(self):
        events = [
            {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "call_id": "call_abc",
                    "id": "fc_1",
                    "name": "read_file",
                    "arguments": "",
                },
            },
            {
                "type": "response.function_call_arguments.delta",
                "call_id": "call_abc",
                "delta": '{"path":',
            },
            {
                "type": "response.function_call_arguments.done",
                "call_id": "call_abc",
                "arguments": '{"path": "/tmp/x"}',
            },
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "call_id": "call_abc",
                    "id": "fc_1",
                    "name": "read_file",
                    "arguments": '{"path": "/tmp/x"}',
                },
            },
            {"type": "response.completed", "response": {"status": "completed"}},
        ]
        fake_resp = _FakeResponse(_make_sse_lines(events))
        fake_client = _FakeClient(fake_resp)

        config = ProviderConfig(model="openai-codex/gpt-5.1-codex")
        provider = OpenAICodexProvider(config)

        fake_token = MagicMock()
        fake_token.account_id = "acct_123"
        fake_token.access = "tok_abc"

        with (
            patch("drclaw.providers.openai_codex_provider.httpx.AsyncClient", return_value=fake_client),
            patch("oauth_cli_kit.get_token", return_value=fake_token),
        ):
            resp = await provider.complete(
                messages=[{"role": "user", "content": "read /tmp/x"}],
            )

        assert resp.stop_reason == "end_turn"
        assert len(resp.tool_calls) == 1
        tc = resp.tool_calls[0]
        assert tc.name == "read_file"
        assert tc.arguments == {"path": "/tmp/x"}
        assert tc.id == "call_abc|fc_1"


class TestCompleteError:
    async def test_http_error(self):
        fake_resp = _FakeResponse([], status_code=500)
        fake_client = _FakeClient(fake_resp)

        config = ProviderConfig(model="openai-codex/gpt-5.1-codex")
        provider = OpenAICodexProvider(config)

        fake_token = MagicMock()
        fake_token.account_id = "acct_123"
        fake_token.access = "tok_abc"

        with (
            patch("drclaw.providers.openai_codex_provider.httpx.AsyncClient", return_value=fake_client),
            patch("oauth_cli_kit.get_token", return_value=fake_token),
        ):
            resp = await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
            )

        assert resp.stop_reason == "error"
        assert "Error calling Codex" in (resp.content or "")
        assert "RuntimeError" in (resp.content or "")

    async def test_empty_exception_still_has_type_and_message(self):
        config = ProviderConfig(model="openai-codex/gpt-5.1-codex")
        provider = OpenAICodexProvider(config)

        fake_token = MagicMock()
        fake_token.account_id = "acct_123"
        fake_token.access = "tok_abc"

        with (
            patch(
                "drclaw.providers.openai_codex_provider._request_codex",
                new=AsyncMock(side_effect=httpx.ReadTimeout("")),
            ),
            patch("oauth_cli_kit.get_token", return_value=fake_token),
        ):
            resp = await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
            )

        assert resp.stop_reason == "error"
        assert resp.content is not None
        assert "ReadTimeout" in resp.content
        assert "Error calling Codex" in resp.content

    def test_request_timeout_constant(self):
        assert DEFAULT_CODEX_TIMEOUT_SECONDS == 180.0
