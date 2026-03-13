"""Data models for Claude Code sessions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

SessionState = Literal["running", "idle", "succeeded", "failed", "cancelled"]

TERMINAL_STATES: set[str] = {"succeeded", "failed", "cancelled"}


@dataclass
class ClaudeCodeSession:
    """In-memory record for a Claude Code SDK session."""

    session_id: str
    caller_agent_id: str
    cwd: str
    instruction: str
    close_on_complete: bool = False
    status: SessionState = "running"
    status_message: str = "running"
    execution_log: list[dict[str, Any]] = field(default_factory=list)
    total_cost_usd: float | None = None
    num_turns: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    ended_at: datetime | None = None
    last_activity_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    done_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    idle_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    task: asyncio.Task[None] | None = None

    def mark_idle(self, *, cost: float | None = None, num_turns: int = 0) -> None:
        now = datetime.now(tz=timezone.utc)
        self.status = "idle"
        self.status_message = "idle — waiting for follow-up"
        self.last_activity_at = now
        if cost is not None:
            self.total_cost_usd = cost
        self.num_turns += num_turns
        self.idle_event.set()

    def mark_running(self) -> None:
        self.status = "running"
        self.status_message = "running"
        self.last_activity_at = datetime.now(tz=timezone.utc)
        self.idle_event.clear()

    def mark_terminal(self, status: SessionState, message: str = "") -> None:
        if status not in TERMINAL_STATES:
            raise ValueError(f"Not a terminal state: {status}")
        now = datetime.now(tz=timezone.utc)
        self.status = status
        self.status_message = message or status
        self.ended_at = now
        self.last_activity_at = now
        self.idle_event.set()
        self.done_event.set()
