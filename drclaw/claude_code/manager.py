"""Session manager for Claude Code SDK integration."""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from drclaw.bus.queue import MessageBus
from drclaw.claude_code.models import TERMINAL_STATES, ClaudeCodeSession
from drclaw.models.messages import InboundMessage, OutboundMessage


class ClaudeCodeSessionManager:
    """Manages Claude Code SDK session lifecycle, concurrency, and bus integration."""

    def __init__(
        self,
        *,
        bus: MessageBus | None = None,
        max_concurrent: int = 4,
        idle_timeout_seconds: int = 300,
        default_max_budget_usd: float = 5.0,
        default_max_turns: int | None = None,
        default_permission_mode: str = "acceptEdits",
        default_allowed_tools: list[str] | None = None,
        default_env: dict[str, str] | None = None,
        client_factory: Any = None,
    ) -> None:
        self.bus = bus
        self.max_concurrent = max_concurrent
        self.idle_timeout_seconds = idle_timeout_seconds
        self.default_max_budget_usd = default_max_budget_usd
        self.default_max_turns = default_max_turns
        self.default_permission_mode = default_permission_mode
        self.default_allowed_tools = default_allowed_tools or [
            "Read", "Write", "Edit", "Bash",
        ]
        self.default_env = default_env or {}
        self._client_factory = client_factory
        self._sessions: dict[str, ClaudeCodeSession] = {}
        self._clients: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._idle_timers: dict[str, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_session(
        self,
        *,
        instruction: str,
        caller_agent_id: str,
        cwd: str,
        max_budget_usd: float | None = None,
        max_turns: int | None = None,
        close_on_complete: bool = False,
        notify_on_completion: bool = True,
        notify_channel: str = "system",
        notify_chat_id: str | None = None,
    ) -> ClaudeCodeSession:
        async with self._lock:
            active = sum(
                1 for s in self._sessions.values() if s.status not in TERMINAL_STATES
            )
            if active >= self.max_concurrent:
                raise RuntimeError(
                    f"Too many active Claude Code sessions ({active}/{self.max_concurrent})."
                )

            session_id = secrets.token_hex(6)
            session = ClaudeCodeSession(
                session_id=session_id,
                caller_agent_id=caller_agent_id,
                cwd=cwd,
                instruction=instruction,
                close_on_complete=close_on_complete,
            )
            self._sessions[session_id] = session

        session.task = asyncio.create_task(
            self._run_session(
                session_id=session_id,
                instruction=instruction,
                cwd=cwd,
                max_budget_usd=max_budget_usd or self.default_max_budget_usd,
                max_turns=max_turns or self.default_max_turns,
                notify_on_completion=notify_on_completion,
                notify_channel=notify_channel,
                notify_chat_id=notify_chat_id or caller_agent_id,
            )
        )
        session.task.add_done_callback(
            lambda t, sid=session_id: self._on_task_done(sid, t)
        )
        return session

    async def send_message(
        self,
        session_id: str,
        message: str,
        *,
        close_after: bool = False,
    ) -> ClaudeCodeSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Unknown session: {session_id}")
        if session.status != "idle":
            raise RuntimeError(
                f"Session {session_id} is {session.status!r}, not idle."
            )

        self._cancel_idle_timer(session_id)
        session.mark_running()

        client = self._clients.get(session_id)
        if client is None:
            raise RuntimeError(f"No client for session {session_id}")

        try:
            await client.query(message)
            await self._drain_response(session_id, client)
        except Exception as exc:
            logger.exception("Claude Code session {} send_message failed", session_id)
            session.mark_terminal("failed", f"send_message error: {exc}")
            await self._publish_outbound(session, f"Session failed: {exc}")
            await self._cleanup_client(session_id)
            return session

        if close_after or session.close_on_complete:
            session.mark_terminal("succeeded", "Completed")
            await self._publish_outbound(session, "Session completed")
            await self._cleanup_client(session_id)
        else:
            self._start_idle_timer(session_id)
        return session

    async def close_session(
        self,
        session_id: str,
        status: str = "succeeded",
    ) -> ClaudeCodeSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Unknown session: {session_id}")
        if session.status in TERMINAL_STATES:
            return session

        self._cancel_idle_timer(session_id)
        terminal = _normalize_terminal(status)
        session.mark_terminal(terminal, f"Closed with status: {terminal}")
        await self._publish_outbound(session, f"Session {terminal}")
        await self._cleanup_client(session_id)

        if session.task and not session.task.done():
            session.task.cancel()
            try:
                await session.task
            except asyncio.CancelledError:
                pass
        return session

    def get_session(self, session_id: str) -> ClaudeCodeSession | None:
        return self._sessions.get(session_id)

    def list_for_caller(
        self,
        caller_agent_id: str,
        *,
        include_terminal: bool = False,
    ) -> list[ClaudeCodeSession]:
        out: list[ClaudeCodeSession] = []
        for s in self._sessions.values():
            if s.caller_agent_id != caller_agent_id:
                continue
            if not include_terminal and s.status in TERMINAL_STATES:
                continue
            out.append(s)
        return sorted(out, key=lambda s: s.started_at)

    async def stop_all(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        for sid in list(self._idle_timers):
            self._cancel_idle_timer(sid)
        async with self._lock:
            for session in self._sessions.values():
                if session.task and not session.task.done():
                    session.task.cancel()
                    tasks.append(session.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for sid in list(self._clients):
            await self._cleanup_client(sid)

    # ------------------------------------------------------------------
    # Internal: session execution
    # ------------------------------------------------------------------

    async def _run_session(
        self,
        *,
        session_id: str,
        instruction: str,
        cwd: str,
        max_budget_usd: float,
        max_turns: int | None,
        notify_on_completion: bool,
        notify_channel: str,
        notify_chat_id: str,
    ) -> None:
        session = self._sessions[session_id]

        try:
            from claude_agent_sdk import ClaudeAgentOptions  # noqa: F401
        except ImportError:
            session.mark_terminal(
                "failed",
                "claude-agent-sdk not installed. Run: pip install claude-agent-sdk",
            )
            await self._publish_outbound(session, session.status_message)
            if notify_on_completion:
                await self._notify_caller(
                    session, notify_channel, notify_chat_id,
                )
            return

        options = ClaudeAgentOptions(
            cwd=cwd,
            permission_mode=self.default_permission_mode,
            allowed_tools=list(self.default_allowed_tools),
            max_budget_usd=max_budget_usd,
            max_turns=max_turns,
            **({"env": dict(self.default_env)} if self.default_env else {}),
        )

        try:
            client = self._create_client(options)
            await client.connect(instruction)
            self._clients[session_id] = client

            start_msg = f"Claude Code session started: {instruction[:100]}"
            await self._publish_outbound(session, start_msg)
            await self._drain_response(session_id, client)
        except asyncio.CancelledError:
            session.mark_terminal("cancelled", "Session cancelled")
            await self._publish_outbound(session, "Session cancelled")
            if notify_on_completion:
                await self._notify_caller(session, notify_channel, notify_chat_id)
            await self._cleanup_client(session_id)
            raise
        except Exception as exc:
            logger.exception("Claude Code session {} crashed", session_id)
            session.mark_terminal("failed", f"Session crashed: {exc}")
            await self._publish_outbound(session, session.status_message)
            if notify_on_completion:
                await self._notify_caller(session, notify_channel, notify_chat_id)
            await self._cleanup_client(session_id)
            return

        if session.close_on_complete:
            session.mark_terminal("succeeded", "Completed (close_on_complete)")
            await self._publish_outbound(session, "Session completed")
            if notify_on_completion:
                await self._notify_caller(session, notify_channel, notify_chat_id)
            await self._cleanup_client(session_id)
        else:
            self._start_idle_timer(session_id)

    async def _drain_response(self, session_id: str, client: Any) -> None:
        session = self._sessions[session_id]
        try:
            from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
        except ImportError:
            return

        async for msg in client.receive_response():
            entry = _message_to_log_entry(msg)
            session.execution_log.append(entry)
            session.last_activity_at = datetime.now(tz=timezone.utc)

            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        await self._publish_outbound(session, block.text)
                    elif isinstance(block, ToolUseBlock):
                        inp = json.dumps(
                            block.input, ensure_ascii=False, default=str,
                        )[:200]
                        await self._publish_outbound(
                            session, f"[tool] {block.name}({inp})",
                        )

            if isinstance(msg, ResultMessage):
                session.total_cost_usd = msg.total_cost_usd
                if not session.close_on_complete:
                    session.mark_idle(
                        cost=msg.total_cost_usd,
                        num_turns=msg.num_turns,
                    )
                else:
                    session.num_turns += msg.num_turns

    def _create_client(self, options: Any) -> Any:
        if self._client_factory is not None:
            return self._client_factory(options)
        from claude_agent_sdk import ClaudeSDKClient
        return ClaudeSDKClient(options=options)

    async def _cleanup_client(self, session_id: str) -> None:
        client = self._clients.pop(session_id, None)
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Idle timeout
    # ------------------------------------------------------------------

    def _start_idle_timer(self, session_id: str) -> None:
        self._cancel_idle_timer(session_id)
        self._idle_timers[session_id] = asyncio.create_task(
            self._idle_timeout_task(session_id)
        )

    def _cancel_idle_timer(self, session_id: str) -> None:
        timer = self._idle_timers.pop(session_id, None)
        if timer and not timer.done():
            timer.cancel()

    async def _idle_timeout_task(self, session_id: str) -> None:
        try:
            await asyncio.sleep(self.idle_timeout_seconds)
        except asyncio.CancelledError:
            return
        session = self._sessions.get(session_id)
        if session is None or session.status != "idle":
            return
        logger.info("Claude Code session {} idle timeout", session_id)
        session.mark_terminal("succeeded", "Idle timeout — auto-closed")
        await self._publish_outbound(session, "Session auto-closed (idle timeout)")
        await self._cleanup_client(session_id)

    # ------------------------------------------------------------------
    # Task done callback
    # ------------------------------------------------------------------

    def _on_task_done(self, session_id: str, task: asyncio.Task[None]) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        if task.cancelled() and session.status not in TERMINAL_STATES:
            session.mark_terminal("cancelled", "Task cancelled")
        elif (exc := task.exception()) is not None and session.status not in TERMINAL_STATES:
            logger.error("Claude Code session {} failed: {}", session_id, exc)
            session.mark_terminal("failed", str(exc))

    # ------------------------------------------------------------------
    # Bus notifications
    # ------------------------------------------------------------------

    async def _publish_outbound(self, session: ClaudeCodeSession, text: str) -> None:
        if self.bus is None:
            return
        await self.bus.publish_outbound(
            OutboundMessage(
                channel="system",
                chat_id=session.caller_agent_id,
                text=text,
                source=f"claude_code:{session.session_id}",
                topic=session.caller_agent_id,
                msg_type="claude_code_event",
                metadata={
                    "session_id": session.session_id,
                    "caller_agent_id": session.caller_agent_id,
                    "status": session.status,
                    "cwd": session.cwd,
                },
            )
        )

    async def _notify_caller(
        self,
        session: ClaudeCodeSession,
        channel: str,
        chat_id: str,
    ) -> None:
        if self.bus is None:
            return
        summary = _session_summary(session)
        await self.bus.publish_inbound(
            InboundMessage(
                channel=channel,
                chat_id=chat_id,
                text=summary,
                source=f"claude_code:{session.session_id}",
                metadata={
                    "session_id": session.session_id,
                    "caller_agent_id": session.caller_agent_id,
                    "status": session.status,
                    "total_cost_usd": session.total_cost_usd,
                    "num_turns": session.num_turns,
                },
            ),
            topic=session.caller_agent_id,
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _message_to_log_entry(msg: Any) -> dict[str, Any]:
    """Convert an SDK message to a JSON-serializable log entry."""
    entry: dict[str, Any] = {"type": type(msg).__name__}
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            TextBlock,
            ToolResultBlock,
            ToolUseBlock,
            UserMessage,
        )

        if isinstance(msg, AssistantMessage):
            blocks: list[dict[str, Any]] = []
            for b in msg.content:
                if isinstance(b, TextBlock):
                    blocks.append({"type": "text", "text": b.text})
                elif isinstance(b, ToolUseBlock):
                    blocks.append({"type": "tool_use", "name": b.name, "input": b.input})
                elif isinstance(b, ToolResultBlock):
                    blocks.append({
                        "type": "tool_result",
                        "tool_use_id": b.tool_use_id,
                        "content": str(b.content)[:500] if b.content else None,
                    })
                else:
                    blocks.append({"type": type(b).__name__})
            entry["content"] = blocks
        elif isinstance(msg, UserMessage):
            entry["content"] = str(msg.content)[:500]
        elif isinstance(msg, ResultMessage):
            entry["num_turns"] = msg.num_turns
            entry["total_cost_usd"] = msg.total_cost_usd
            entry["is_error"] = msg.is_error
            entry["result"] = msg.result[:500] if msg.result else None
        else:
            entry["raw"] = str(msg)[:500]
    except ImportError:
        entry["raw"] = str(msg)[:500]
    return entry


def _session_summary(session: ClaudeCodeSession) -> str:
    parts = [
        f"[claude_code:{session.session_id}] {session.status}: {session.status_message}",
    ]
    if session.total_cost_usd is not None:
        parts.append(f"cost=${session.total_cost_usd:.4f}")
    parts.append(f"turns={session.num_turns}")
    return " | ".join(parts)


def _normalize_terminal(status: str) -> str:
    s = status.strip().lower()
    if s in {"success", "succeeded", "done", "ok"}:
        return "succeeded"
    if s in {"fail", "failed", "error"}:
        return "failed"
    if s in {"cancel", "cancelled", "canceled"}:
        return "cancelled"
    return "failed"
