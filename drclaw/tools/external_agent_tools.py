"""Tool for delegating a request to an external agent provider."""

from __future__ import annotations

from typing import Any

from drclaw.external_agents.bridge import ExternalAgentBridge
from drclaw.tools.base import Tool


class CallExternalAgentTool(Tool):
    """Queue an async request to an external agent and return request_id."""

    def __init__(self, bridge: ExternalAgentBridge, *, caller_agent_id: str) -> None:
        self._bridge = bridge
        self._caller_agent_id = caller_agent_id

    @property
    def name(self) -> str:
        return "call_external_agent"

    @property
    def description(self) -> str:
        return (
            "Send a message to a configured external agent provider. "
            "Use this when external capability is required. "
            "Response arrives asynchronously as a follow-up message."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "External agent ID, e.g. ext:chem-assistant",
                },
                "message": {
                    "type": "string",
                    "description": "Text message to send to external agent.",
                },
                "metadata": {
                    "type": "object",
                    "description": "Optional JSON metadata forwarded to provider.",
                },
            },
            "required": ["agent_id", "message"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        agent_id = str(params.get("agent_id", "")).strip()
        message = str(params.get("message", "")).strip()
        metadata = params.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        if not agent_id:
            return "Error: agent_id is required"
        if not message:
            return "Error: message is required"
        if not self._bridge.is_external_agent(agent_id):
            return f"Error: Unknown external agent: {agent_id}"

        try:
            request_id = await self._bridge.submit_from_agent(
                agent_id=agent_id,
                text=message,
                metadata=metadata,
                origin_topic=self._caller_agent_id,
            )
        except Exception as exc:
            return f"Error: External agent request failed: {exc}"

        return f"Queued external request {request_id} to {agent_id}"
