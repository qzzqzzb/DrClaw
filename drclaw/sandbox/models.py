"""Sandbox job models for Docker-backed task execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

SandboxPolicy = Literal["readonly", "workspace_write"]
SandboxWorkerKind = Literal["shell_task"]
SandboxJobState = Literal[
    "queued",
    "pending_approval",
    "starting",
    "running",
    "waiting_input",
    "suspended",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
]
TerminalState = Literal["succeeded", "failed", "cancelled", "timed_out"]

TERMINAL_STATES: set[str] = {"succeeded", "failed", "cancelled", "timed_out"}
ACTIVE_STATES: set[str] = {
    "queued",
    "pending_approval",
    "starting",
    "running",
    "waiting_input",
    "suspended",
}


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(frozen=True)
class ShellTaskSpec:
    """One shell command executed inside a sandbox container."""

    command: str
    workdir: str | None = None
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class SandboxJobResult:
    """Terminal outcome returned by one sandbox backend run."""

    status: TerminalState
    summary: str
    artifacts: dict[str, Any] = field(default_factory=dict)


@dataclass
class SandboxJob:
    """In-memory record for one sandbox-backed job."""

    job_id: str
    request_id: str
    caller_agent_id: str
    title: str
    description: str
    workspace_path: Path
    sandbox_policy: SandboxPolicy
    worker_kind: SandboxWorkerKind
    notify_on_completion: bool = True
    notify_channel: str = "system"
    notify_chat_id: str | None = None
    expects_heartbeat: bool = False
    status: SandboxJobState = "queued"
    status_message: str = "queued"
    progress_pct: float | None = None
    summary: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)
    container_id: str | None = None
    image: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    suspended_at: datetime | None = None
    resumed_at: datetime | None = None
    pause_reason: str = ""
    last_heartbeat_at: datetime = field(default_factory=utc_now)
    task: asyncio.Task[None] | None = None
    done_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def agent_id(self) -> str:
        return f"sjob:{self.job_id}"

    def mark_starting(self, message: str = "starting") -> None:
        now = utc_now()
        self.status = "starting"
        self.status_message = message
        self.started_at = now
        self.last_heartbeat_at = now

    def mark_running(self, message: str = "running") -> None:
        self.status = "running"
        self.status_message = message
        self.last_heartbeat_at = utc_now()

    def mark_heartbeat(
        self,
        message: str | None = None,
        progress_pct: float | None = None,
    ) -> None:
        self.last_heartbeat_at = utc_now()
        if message is not None:
            self.status_message = message
        if progress_pct is not None:
            self.progress_pct = progress_pct

