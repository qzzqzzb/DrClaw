"""In-process MCP bridge: expose DrClaw Tool instances inside Claude Code sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from drclaw.tools.base import Tool

# Tool name prefixes that must never be bridged — they manage Claude Code sessions
# themselves and allowing them inside a session causes recursive session spawning.
_BLOCKED_PREFIXES = (
    "use_claude_code",
    "send_claude_code",
    "get_claude_code",
    "list_claude_code",
    "close_claude_code",
)


def _is_bridgeable(tool: Tool) -> bool:
    return not any(tool.name.startswith(p) for p in _BLOCKED_PREFIXES)


def create_mcp_bridge(
    tools: list[Tool],
    server_name: str = "drclaw",
) -> tuple[Any, list[str]]:
    """Adapt DrClaw *tools* into an in-process MCP server.

    Args:
        tools: DrClaw Tool instances to expose.
        server_name: MCP server name (used in tool qualified names
            ``mcp__<server_name>__<tool_name>``).

    Returns:
        A tuple of (McpSdkServerConfig, list_of_bridged_tool_names).
        The config is ready to pass as ``mcp_servers[server_name]`` in
        ``ClaudeAgentOptions``.

    Raises:
        ImportError: if claude_agent_sdk is not installed.
    """
    from claude_agent_sdk import create_sdk_mcp_server, tool as sdk_tool

    bridgeable = [t for t in tools if _is_bridgeable(t)]
    if not bridgeable:
        logger.debug("MCP bridge: no bridgeable tools, skipping")

    sdk_tools = []
    bridged_names: list[str] = []

    for drctool in bridgeable:
        # Capture loop variable explicitly to avoid closure-over-loop issues.
        _t = drctool

        @sdk_tool(_t.name, _t.description, _t.parameters)
        async def _handler(args: dict[str, Any], bound_tool: Tool = _t) -> dict[str, Any]:
            try:
                result = await bound_tool.execute(args)
            except Exception as exc:
                logger.warning("MCP bridge tool {} raised: {}", bound_tool.name, exc)
                return {
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "is_error": True,
                }
            return {"content": [{"type": "text", "text": result}]}

        sdk_tools.append(_handler)
        bridged_names.append(_t.name)
        logger.debug("MCP bridge: registered tool {}", _t.name)

    config = create_sdk_mcp_server(server_name, sdk_tools)
    logger.info(
        "MCP bridge: server '{}' with {} tools: {}",
        server_name, len(bridged_names), bridged_names,
    )
    # Attach sdk_tools to the config for testing/introspection purposes.
    config["_sdk_tools"] = sdk_tools  # type: ignore[literal-required]
    return config, bridged_names
