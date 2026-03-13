"""Tool registry for dynamic tool management."""

from typing import Any

from drclaw.tools.base import Tool

_HINT = "\n\n[Analyze the error above and try a different approach.]"


class ToolRegistry:
    """Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """Execute a tool by name with given parameters."""
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"

        try:
            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + _HINT
            result = await tool.execute(params)
            # "Error: " (with colon-space) is the convention for tool error returns;
            # we append the hint so the LLM knows to retry with a different approach.
            # Using the colon-space prefix avoids false positives on content that
            # merely starts with the word "Error" (e.g. "Error handling in Python...").
            if isinstance(result, str) and result.startswith("Error: "):
                return result + _HINT
            return result
        except Exception as e:
            return f"Error: {str(e)}" + _HINT

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
