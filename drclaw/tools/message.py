"""Message tool for sending proactive outbound chat messages."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from drclaw.models.messages import OutboundMessage
from drclaw.tools.base import Tool
from drclaw.tools.filesystem import _resolve_path


class MessageTool(Tool):
    """Send a message to a chat destination with optional media attachments."""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        *,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
    ) -> None:
        self._send_callback = send_callback
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._default_channel = ""
        self._default_chat_id = ""
        self._sent_targets: set[tuple[str, str]] = set()

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return (
            "Send a message to the user. "
            "Supports optional file attachments via local paths."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The message text to send",
                },
                "channel": {
                    "type": "string",
                    "description": (
                        "Optional target channel. "
                        "Defaults to current conversation channel."
                    ),
                },
                "chat_id": {
                    "type": "string",
                    "description": (
                        "Optional target chat ID. "
                        "Defaults to current conversation chat ID."
                    ),
                },
                "media": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of local file paths to attach.",
                },
            },
            "required": ["content"],
        }

    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the per-turn default routing destination."""
        self._default_channel = channel
        self._default_chat_id = chat_id

    def start_turn(self) -> None:
        """Reset per-turn delivery tracking."""
        self._sent_targets.clear()

    def sent_in_turn_for(self, channel: str, chat_id: str) -> bool:
        """Return True if this tool already sent to (channel, chat_id) in current turn."""
        return (channel, chat_id) in self._sent_targets

    async def execute(self, params: dict[str, Any]) -> str:
        content = str(params["content"])
        channel = str(params.get("channel") or self._default_channel).strip()
        chat_id = str(params.get("chat_id") or self._default_chat_id).strip()
        media = params.get("media") or []

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"
        if self._send_callback is None:
            return "Error: Message sending not configured"

        try:
            media_paths = self._resolve_media(media)
        except (PermissionError, ValueError) as exc:
            return f"Error: {exc}"

        await self._send_callback(
            OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                text=content,
                media=media_paths,
            )
        )
        self._sent_targets.add((channel, chat_id))

        suffix = f" with {len(media_paths)} attachments" if media_paths else ""
        return f"Message sent to {channel}:{chat_id}{suffix}"

    def _resolve_media(self, media: list[Any]) -> list[str]:
        resolved_paths: list[str] = []
        for raw_path in media:
            path = str(raw_path)
            resolved = _resolve_path(path, self._workspace, self._allowed_dir)
            if not resolved.exists():
                raise ValueError(f"Media file not found: {path}")
            if not resolved.is_file():
                raise ValueError(f"Media path is not a file: {path}")
            resolved_paths.append(str(resolved))
        return resolved_paths
