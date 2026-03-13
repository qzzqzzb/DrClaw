"""Message types for the DrClaw message bus."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InboundMessage:
    """A message arriving from a channel into the agent."""

    channel: str
    chat_id: str
    text: str
    source: str = "user"
    topic: str = ""
    session_key_override: str | None = field(default=None)
    hop_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def session_key(self) -> str:
        return self.session_key_override or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """A message leaving the agent to be delivered to a channel."""

    channel: str
    chat_id: str
    text: str
    media: list[str] = field(default_factory=list)
    source: str = "main"
    topic: str = ""
    msg_type: str = "chat"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def session_key(self) -> str:
        return f"{self.channel}:{self.chat_id}"
