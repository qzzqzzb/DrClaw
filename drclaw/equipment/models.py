"""Equipment prototype/runtime models for DrClaw."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

RuntimeState = Literal[
    "queued",
    "running",
    "waiting_input",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
]

TerminalState = Literal["succeeded", "failed", "cancelled", "timed_out"]

TERMINAL_STATES: set[str] = {"succeeded", "failed", "cancelled", "timed_out"}


@dataclass(frozen=True)
class EquipmentPrototype:
    """Persistent equipment definition."""

    name: str
    root_dir: Path
    skills_dir: Path


@dataclass
class EquipmentRuntime:
    """In-memory runtime record for an equipment execution."""

    runtime_id: str
    request_id: str
    prototype_name: str
    caller_agent_id: str
    workspace_path: Path
    notify_on_completion: bool = True
    status: RuntimeState = "queued"
    status_message: str = "queued"
    progress_pct: float | None = None
    summary: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    last_heartbeat_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    task: asyncio.Task[None] | None = None
    done_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def agent_id(self) -> str:
        return f"equip:{self.prototype_name}:{self.runtime_id}"

    def mark_started(self) -> None:
        now = datetime.now(tz=timezone.utc)
        self.status = "running"
        self.status_message = "running"
        self.started_at = now
        self.last_heartbeat_at = now

    def mark_heartbeat(self, message: str | None = None, progress_pct: float | None = None) -> None:
        self.last_heartbeat_at = datetime.now(tz=timezone.utc)
        if message is not None:
            self.status_message = message
        if progress_pct is not None:
            self.progress_pct = progress_pct
