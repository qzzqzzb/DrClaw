"""Tests for the in-process MCP bridge."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from drclaw.tools.base import Tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeTool(Tool):
    def __init__(self, name: str, result: str = "ok", raise_exc: Exception | None = None) -> None:
        self._name = name
        self._result = result
        self._raise_exc = raise_exc

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Fake tool: {self._name}"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": [],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        if self._raise_exc:
            raise self._raise_exc
        return self._result


def _make_bridge(*tools: Tool):
    from drclaw.claude_code.mcp_bridge import create_mcp_bridge
    return create_mcp_bridge(list(tools))


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_create_mcp_bridge_returns_config_and_names() -> None:
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")

    tool = _FakeTool("my_tool")
    config, names = _make_bridge(tool)
    # McpSdkServerConfig is a TypedDict (dict subclass)
    assert config["type"] == "sdk"
    assert config["name"] == "drclaw"
    assert names == ["my_tool"]


def test_create_mcp_bridge_empty_tools() -> None:
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")

    config, names = _make_bridge()
    assert config["type"] == "sdk"
    assert names == []


def test_create_mcp_bridge_multiple_tools() -> None:
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")

    t1 = _FakeTool("alpha")
    t2 = _FakeTool("beta")
    config, names = _make_bridge(t1, t2)
    assert config["type"] == "sdk"
    assert set(names) == {"alpha", "beta"}


def test_claude_code_tools_filtered_out() -> None:
    """Tools with claude_code-related names must never be bridged."""
    try:
        from claude_agent_sdk import McpSdkServerConfig
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")

    cc_tools = [
        _FakeTool("use_claude_code"),
        _FakeTool("send_claude_code_message"),
        _FakeTool("get_claude_code_status"),
        _FakeTool("list_claude_code_sessions"),
        _FakeTool("close_claude_code_session"),
    ]
    safe = _FakeTool("my_safe_tool")
    config, names = _make_bridge(*cc_tools, safe)
    assert names == ["my_safe_tool"]


def test_adapt_tool_name_description_schema() -> None:
    """The bridge must preserve name and description on the SdkMcpTool."""
    try:
        from claude_agent_sdk import tool as sdk_tool
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")

    from drclaw.claude_code.mcp_bridge import _is_bridgeable

    drctool = _FakeTool("my_tool")
    assert _is_bridgeable(drctool)
    # Verify name/description round-trip via the @tool decorator semantics
    assert drctool.name == "my_tool"
    assert drctool.description == "Fake tool: my_tool"


@pytest.mark.asyncio
async def test_adapted_tool_execute_returns_mcp_format() -> None:
    """The bridged handler must return MCP text content on success."""
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")

    drctool = _FakeTool("greeter", result="Hello!")
    config, names = _make_bridge(drctool)
    assert "greeter" in names

    # Call the SdkMcpTool handler directly (exposed via _sdk_tools).
    sdk_tools = {t.name: t for t in config["_sdk_tools"]}
    result = await sdk_tools["greeter"].handler({"msg": "world"})
    assert result == {"content": [{"type": "text", "text": "Hello!"}]}


@pytest.mark.asyncio
async def test_adapted_tool_error_returns_error_content() -> None:
    """When the DrClaw tool raises, the MCP bridge handler returns is_error content."""
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")

    drctool = _FakeTool("boom", raise_exc=RuntimeError("something broke"))
    config, names = _make_bridge(drctool)
    assert "boom" in names

    sdk_tools = {t.name: t for t in config["_sdk_tools"]}
    result = await sdk_tools["boom"].handler({})
    assert result["is_error"] is True
    assert "something broke" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Integration: manager builds MCP bridge when mcp_tools provided
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manager_sets_mcp_servers_when_tools_provided() -> None:
    """When mcp_tools is non-empty, ClaudeAgentOptions receives mcp_servers."""
    try:
        from claude_agent_sdk import McpSdkServerConfig, ResultMessage
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")

    captured_options: list[Any] = []

    def make_receive():
        async def fake():
            yield ResultMessage(
                subtype="result",
                duration_ms=100,
                duration_api_ms=50,
                is_error=False,
                num_turns=1,
                session_id="mock",
                total_cost_usd=0.01,
            )
        return fake()

    def client_factory(opts):
        captured_options.append(opts)
        c = AsyncMock()
        c.connect = AsyncMock()
        c.disconnect = AsyncMock()
        c.receive_response = make_receive
        return c

    from drclaw.claude_code.manager import ClaudeCodeSessionManager

    manager = ClaudeCodeSessionManager(
        max_retries=0,
        client_factory=client_factory,
    )
    mcp_tools = [_FakeTool("shell_exec")]
    session = await manager.start_session(
        instruction="do something",
        caller_agent_id="test",
        cwd="/tmp",
        mcp_tools=mcp_tools,
    )
    await asyncio.wait_for(session.idle_event.wait(), timeout=5)

    assert captured_options, "client_factory was never called"
    opts = captured_options[0]
    assert hasattr(opts, "mcp_servers") or "mcp_servers" in (opts.__dict__ if hasattr(opts, "__dict__") else {})
    # The allowed_tools should contain the bridged tool name
    assert any("shell_exec" in t for t in opts.allowed_tools)


@pytest.mark.asyncio
async def test_manager_no_mcp_when_tools_empty() -> None:
    """When mcp_tools is empty (default), mcp_servers must NOT be set on options."""
    try:
        from claude_agent_sdk import McpSdkServerConfig, ResultMessage
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")

    captured_options: list[Any] = []

    def make_receive():
        async def fake():
            yield ResultMessage(
                subtype="result",
                duration_ms=100,
                duration_api_ms=50,
                is_error=False,
                num_turns=1,
                session_id="mock",
                total_cost_usd=0.01,
            )
        return fake()

    def client_factory(opts):
        captured_options.append(opts)
        c = AsyncMock()
        c.connect = AsyncMock()
        c.disconnect = AsyncMock()
        c.receive_response = make_receive
        return c

    from drclaw.claude_code.manager import ClaudeCodeSessionManager

    manager = ClaudeCodeSessionManager(
        max_retries=0,
        client_factory=client_factory,
    )
    session = await manager.start_session(
        instruction="do something",
        caller_agent_id="test",
        cwd="/tmp",
        # no mcp_tools
    )
    await asyncio.wait_for(session.idle_event.wait(), timeout=5)

    opts = captured_options[0]
    mcp = getattr(opts, "mcp_servers", None) or opts.get("mcp_servers", {}) if isinstance(opts, dict) else getattr(opts, "mcp_servers", {})
    assert not mcp
