"""Background task manager + tools for detached long-running tool calls."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

from drclaw.models.messages import InboundMessage, OutboundMessage
from drclaw.tools.base import Tool

if TYPE_CHECKING:
    from drclaw.bus.queue import MessageBus

logger = logging.getLogger(__name__)

BackgroundTaskState = Literal["running", "succeeded", "failed", "cancelled"]
TERMINAL_STATES: set[str] = {"succeeded", "failed", "cancelled"}
_MAX_RESULT_CHARS = 10_000
_RESULT_PREVIEW_CHARS = 500
_MAX_TERMINAL_RETAINED = 200


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass
class BackgroundToolTask:
    """A detached tool execution tracked by the caller agent."""

    task_id: str
    caller_agent_id: str
    tool_name: str
    tool_args: dict[str, Any]
    channel: str
    chat_id: str
    status: BackgroundTaskState = "running"
    status_message: str = "running"
    created_at: datetime = field(default_factory=_utc_now)
    started_at: datetime = field(default_factory=_utc_now)
    ended_at: datetime | None = None
    result: str | None = None
    error: str | None = None
    task: asyncio.Task[str] | None = None

    def mark_terminal(
        self,
        *,
        status: Literal["succeeded", "failed", "cancelled"],
        status_message: str,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        self.status = status
        self.status_message = status_message
        self.result = result
        self.error = error
        self.ended_at = _utc_now()


class BackgroundToolTaskManager:
    """Tracks and controls detached tool executions for one agent loop."""

    def __init__(
        self,
        *,
        caller_agent_id: str,
        max_result_chars: int = _MAX_RESULT_CHARS,
        max_terminal_retained: int = _MAX_TERMINAL_RETAINED,
    ) -> None:
        self._caller_agent_id = caller_agent_id
        self._max_result_chars = max_result_chars
        self._max_terminal_retained = max_terminal_retained
        self._tasks: dict[str, BackgroundToolTask] = {}

    @property
    def caller_agent_id(self) -> str:
        return self._caller_agent_id

    async def register_detached(
        self,
        *,
        task: asyncio.Task[str],
        tool_name: str,
        tool_args: dict[str, Any],
        channel: str,
        chat_id: str,
        bus: MessageBus | None = None,
        publish_inbound_callback: bool = True,
    ) -> BackgroundToolTask:
        """Register a running asyncio task as a detached backend task."""
        task_id = secrets.token_hex(6)
        rec = BackgroundToolTask(
            task_id=task_id,
            caller_agent_id=self._caller_agent_id,
            tool_name=tool_name,
            tool_args=dict(tool_args),
            channel=channel,
            chat_id=chat_id,
            task=task,
        )
        self._tasks[task_id] = rec

        # If the task already finished (race between shield timeout and here),
        # finalize immediately instead of relying on the done callback.
        if task.done():
            await self._finalize_task(task_id, task)
            await self.publish_started(rec, bus)
            if rec.status in TERMINAL_STATES:
                await self.publish_terminal(
                    rec, bus=bus, include_inbound_callback=publish_inbound_callback
                )
            return rec

        # Task still running — attach done callback, then publish started event.
        # The callback is guaranteed to fire after publish_started because
        # we don't yield between add_done_callback and publish_started, and
        # the callback itself is scheduled via create_task (runs on next iteration).
        task.add_done_callback(
            lambda done, rid=task_id, pb=publish_inbound_callback: self._schedule_done(
                rid, done, bus=bus, publish_inbound_callback=pb
            )
        )
        await self.publish_started(rec, bus)
        return rec

    def _schedule_done(
        self,
        task_id: str,
        task: asyncio.Task[str],
        *,
        bus: MessageBus | None,
        publish_inbound_callback: bool,
    ) -> None:
        """Schedule _on_task_done as a task, suppressing unhandled exceptions."""
        t = asyncio.create_task(
            self._on_task_done(
                task_id, task, bus=bus, publish_inbound_callback=publish_inbound_callback
            )
        )
        t.add_done_callback(_suppress_task_exception)

    def get(self, task_id: str) -> BackgroundToolTask | None:
        return self._tasks.get(task_id)

    def list_for_caller(self, *, include_terminal: bool = False) -> list[BackgroundToolTask]:
        out: list[BackgroundToolTask] = []
        for rec in self._tasks.values():
            if not include_terminal and rec.status in TERMINAL_STATES:
                continue
            out.append(rec)
        return sorted(out, key=lambda r: r.created_at)

    def cancel(self, task_id: str) -> BackgroundToolTask:
        rec = self._tasks.get(task_id)
        if rec is None:
            raise ValueError(f"Unknown task id: {task_id}")
        if rec.status in TERMINAL_STATES:
            return rec
        if rec.task and not rec.task.done():
            rec.task.cancel()
            rec.status_message = "cancel requested"
        return rec

    async def publish_started(self, rec: BackgroundToolTask, bus: MessageBus | None) -> None:
        if bus is None:
            return
        await bus.publish_outbound(
            OutboundMessage(
                channel=rec.channel,
                chat_id=rec.chat_id,
                text=(
                    f"[tool_task_started] task_id={rec.task_id} tool={rec.tool_name} "
                    "status=running"
                ),
                source=self._caller_agent_id,
                topic=self._caller_agent_id,
                msg_type="tool_task_started",
                metadata=self._event_metadata(rec),
            )
        )

    async def _finalize_task(self, task_id: str, task: asyncio.Task[str]) -> None:
        rec = self._tasks.get(task_id)
        if rec is None or rec.status in TERMINAL_STATES:
            return
        if task.cancelled():
            rec.mark_terminal(status="cancelled", status_message="cancelled")
            return
        exc = task.exception()
        if exc is not None:
            msg = str(exc) or exc.__class__.__name__
            rec.mark_terminal(
                status="failed",
                status_message=f"failed: {msg}",
                error=msg[: self._max_result_chars],
            )
            return
        raw = task.result()
        result = raw if len(raw) <= self._max_result_chars else raw[: self._max_result_chars] + (
            "\n... (truncated)"
        )
        rec.mark_terminal(
            status="succeeded",
            status_message="completed",
            result=result,
        )

    async def _on_task_done(
        self,
        task_id: str,
        task: asyncio.Task[str],
        *,
        bus: MessageBus | None,
        publish_inbound_callback: bool,
    ) -> None:
        await self._finalize_task(task_id, task)
        rec = self._tasks.get(task_id)
        if rec is None or rec.status not in TERMINAL_STATES:
            return
        await self.publish_terminal(
            rec,
            bus=bus,
            include_inbound_callback=publish_inbound_callback,
        )
        self._prune_terminal()

    async def publish_terminal(
        self,
        rec: BackgroundToolTask,
        *,
        bus: MessageBus | None,
        include_inbound_callback: bool = True,
    ) -> None:
        if bus is None:
            return

        msg_type = {
            "succeeded": "tool_task_completed",
            "failed": "tool_task_failed",
            "cancelled": "tool_task_cancelled",
        }[rec.status]
        preview = self._result_preview(rec)
        await bus.publish_outbound(
            OutboundMessage(
                channel=rec.channel,
                chat_id=rec.chat_id,
                text=(
                    f"[{msg_type}] task_id={rec.task_id} tool={rec.tool_name} "
                    f"status={rec.status}"
                    + (f"\n{preview}" if preview else "")
                ),
                source=self._caller_agent_id,
                topic=self._caller_agent_id,
                msg_type=msg_type,
                metadata=self._event_metadata(rec),
            )
        )
        if not include_inbound_callback:
            return
        callback_text = (
            f"Background tool task finished: task_id={rec.task_id} "
            f"tool={rec.tool_name} status={rec.status}."
        )
        if preview:
            callback_text += f"\npreview: {preview}"
        callback_text += "\nUse get_background_tool_task_status(task_id=...) for full output."
        await bus.publish_inbound(
            InboundMessage(
                channel=rec.channel,
                chat_id=rec.chat_id,
                text=callback_text,
                source=f"tooltask:{rec.task_id}",
                metadata={
                    "background_task_callback": True,
                    "task_id": rec.task_id,
                    "tool_name": rec.tool_name,
                    "status": rec.status,
                },
            ),
            topic=self._caller_agent_id,
        )

    def build_detached_tool_result(self, rec: BackgroundToolTask) -> str:
        """String returned into the model's tool-result channel on detach."""
        return (
            f"Tool call is running in background (task_id={rec.task_id}, tool={rec.tool_name}). "
            "IMPORTANT: Do NOT poll for status. You will receive an automatic notification "
            "when the task completes. End your current response now and wait for the "
            "completion callback. Use cancel_background_tool_task only if you need to abort."
        )

    def _prune_terminal(self) -> None:
        """Remove oldest terminal tasks when exceeding retention limit."""
        terminal = [
            (tid, rec) for tid, rec in self._tasks.items() if rec.status in TERMINAL_STATES
        ]
        if len(terminal) <= self._max_terminal_retained:
            return
        terminal.sort(key=lambda pair: pair[1].ended_at or pair[1].created_at)
        to_remove = len(terminal) - self._max_terminal_retained
        for tid, _ in terminal[:to_remove]:
            del self._tasks[tid]

    @staticmethod
    def _event_metadata(rec: BackgroundToolTask) -> dict[str, Any]:
        return {
            "task_id": rec.task_id,
            "tool_name": rec.tool_name,
            "caller_agent_id": rec.caller_agent_id,
            "status": rec.status,
            "status_message": rec.status_message,
            "created_at": rec.created_at.isoformat(),
            "started_at": rec.started_at.isoformat(),
            "ended_at": rec.ended_at.isoformat() if rec.ended_at else None,
        }

    @staticmethod
    def _result_preview(rec: BackgroundToolTask) -> str:
        text = rec.result if rec.result is not None else rec.error
        if not text:
            return ""
        if len(text) <= _RESULT_PREVIEW_CHARS:
            return text
        return text[:_RESULT_PREVIEW_CHARS] + "... (truncated)"


def _suppress_task_exception(task: asyncio.Task[Any]) -> None:
    """Prevent 'Task exception was never retrieved' warnings."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background task done-callback failed: %s", exc)


class ListBackgroundToolTasksTool(Tool):
    """List detached background tool tasks for the caller agent."""

    def __init__(self, manager: BackgroundToolTaskManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "list_background_tool_tasks"

    @property
    def description(self) -> str:
        return "List detached background tool tasks for this agent."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "include_terminal": {
                    "type": "boolean",
                    "description": "Include completed/failed/cancelled tasks.",
                },
            },
        }

    async def execute(self, params: dict[str, Any]) -> str:
        include_terminal = bool(params.get("include_terminal", False))
        rows = self._manager.list_for_caller(include_terminal=include_terminal)
        if not rows:
            return "No background tool tasks."
        lines = ["Background tool tasks:"]
        for rec in rows:
            lines.append(
                f"- task_id={rec.task_id} tool={rec.tool_name} status={rec.status} "
                f"msg={rec.status_message}"
            )
        return "\n".join(lines)


class GetBackgroundToolTaskStatusTool(Tool):
    """Get status/result for one detached background tool task."""

    def __init__(self, manager: BackgroundToolTaskManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "get_background_tool_task_status"

    @property
    def description(self) -> str:
        return "Get status for one background tool task by task_id."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "include_result": {
                    "type": "boolean",
                    "description": "Include full result/error body when terminal.",
                },
            },
            "required": ["task_id"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        task_id = params["task_id"]
        include_result = bool(params.get("include_result", False))
        rec = self._manager.get(task_id)
        if rec is None:
            return f"No background task with id {task_id!r}."
        lines = [
            f"task_id={rec.task_id}",
            f"tool={rec.tool_name}",
            f"status={rec.status}",
            f"status_message={rec.status_message}",
            f"created_at={rec.created_at.isoformat()}",
            f"started_at={rec.started_at.isoformat()}",
            f"ended_at={rec.ended_at.isoformat()}" if rec.ended_at else "ended_at=n/a",
            "args=" + json.dumps(rec.tool_args, ensure_ascii=False, default=str),
        ]
        if include_result and rec.status in TERMINAL_STATES:
            if rec.result:
                lines.append("result:\n" + rec.result)
            if rec.error:
                lines.append("error:\n" + rec.error)
        return "\n".join(lines)


class CancelBackgroundToolTaskTool(Tool):
    """Cancel a detached background tool task."""

    def __init__(self, manager: BackgroundToolTaskManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "cancel_background_tool_task"

    @property
    def description(self) -> str:
        return "Cancel a running background tool task."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        task_id = params["task_id"]
        rec = self._manager.get(task_id)
        if rec is None:
            return f"No background task with id {task_id!r}."
        rec = self._manager.cancel(task_id)
        return (
            f"Background task {task_id} status={rec.status} "
            f"msg={rec.status_message}."
        )
