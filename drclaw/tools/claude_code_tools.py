"""Tools for ProjectAgent to delegate coding tasks to Claude Code."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from drclaw.tools.base import Tool

if TYPE_CHECKING:
    from drclaw.claude_code.manager import ClaudeCodeSessionManager


class UseClaudeCodeTool(Tool):
    """Start a Claude Code session to handle a coding task."""

    def __init__(
        self,
        manager: ClaudeCodeSessionManager,
        *,
        caller_agent_id: str,
        default_cwd: str,
        mcp_tools_provider: Callable[[], list[Tool]] | None = None,
    ) -> None:
        self._manager = manager
        self._caller_agent_id = caller_agent_id
        self._default_cwd = default_cwd
        self._mcp_tools_provider = mcp_tools_provider

    @property
    def name(self) -> str:
        return "use_claude_code"

    @property
    def description(self) -> str:
        return (
            "Delegate a complex coding task to Claude Code for autonomous execution. "
            "Use this only for complex coding work such as substantial code generation, "
            "difficult bug fixes, or non-trivial refactoring. Returns immediately by "
            "default (fire-and-forget); set await_result=true to wait for completion."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "Detailed coding task instruction for Claude Code.",
                },
                "cwd": {
                    "type": "string",
                    "description": (
                        "Working directory for Claude Code. "
                        "Defaults to project workspace."
                    ),
                },
                "await_result": {
                    "type": "boolean",
                    "description": (
                        "If true, wait until the first turn completes and return output. "
                        "If false (default), return session_id immediately."
                    ),
                },
                "max_budget_usd": {
                    "type": "number",
                    "description": "Max spend in USD for this session.",
                },
                "close_on_complete": {
                    "type": "boolean",
                    "description": (
                        "If true, auto-close session after first turn (one-shot mode). "
                        "If false (default), session stays idle for follow-ups."
                    ),
                },
            },
            "required": ["instruction"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        instruction: str = params["instruction"]
        cwd: str = params.get("cwd", self._default_cwd)
        await_result: bool = params.get("await_result", False)
        max_budget_usd: float | None = params.get("max_budget_usd")
        close_on_complete: bool = params.get("close_on_complete", False)

        mcp_tools = self._mcp_tools_provider() if self._mcp_tools_provider else None
        try:
            session = await self._manager.start_session(
                instruction=instruction,
                caller_agent_id=self._caller_agent_id,
                cwd=cwd,
                max_budget_usd=max_budget_usd,
                close_on_complete=close_on_complete,
                # Always enable notifications: even with await_result=true, the
                # tool call may be detached to background by the agent loop's
                # timeout.  Without notifications the agent has no way to learn
                # the session finished except by polling.
                notify_on_completion=True,
                mcp_tools=mcp_tools,
            )
        except Exception as exc:
            return f"Error: failed to start Claude Code session: {exc}"

        if not await_result:
            return (
                f"Started Claude Code session (session_id={session.session_id}). "
                f"Use get_claude_code_status to check progress, or "
                f"send_claude_code_message for follow-ups."
            )

        try:
            await asyncio.wait_for(session.idle_event.wait(), timeout=600)
        except asyncio.TimeoutError:
            return (
                f"Timed out waiting for Claude Code session {session.session_id}. "
                f"Session is still {session.status}. "
                f"Use get_claude_code_status to check progress."
            )

        return _format_session_result(session)


class SendClaudeCodeMessageTool(Tool):
    """Send a follow-up message to an idle Claude Code session."""

    def __init__(
        self,
        manager: ClaudeCodeSessionManager,
        *,
        caller_agent_id: str,
    ) -> None:
        self._manager = manager
        self._caller_agent_id = caller_agent_id

    @property
    def name(self) -> str:
        return "send_claude_code_message"

    @property
    def description(self) -> str:
        return (
            "Send a follow-up message to an idle Claude Code session. "
            "The session must be in 'idle' state (first turn completed)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Target Claude Code session ID.",
                },
                "message": {
                    "type": "string",
                    "description": "Follow-up instruction or question.",
                },
                "await_result": {
                    "type": "boolean",
                    "description": "If true (default), wait for response.",
                },
                "close_after": {
                    "type": "boolean",
                    "description": "If true, close session after this message.",
                },
            },
            "required": ["session_id", "message"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        session_id: str = params["session_id"]
        message: str = params["message"]
        await_result: bool = params.get("await_result", True)
        close_after: bool = params.get("close_after", False)

        session = self._manager.get_session(session_id)
        if session is None:
            return f"Error: no session with id {session_id!r}."
        if session.caller_agent_id != self._caller_agent_id:
            return f"Error: session {session_id!r} belongs to another agent."

        try:
            session = await self._manager.send_message(
                session_id, message, close_after=close_after,
            )
        except Exception as exc:
            return f"Error: {exc}"

        if not await_result:
            return f"Message sent to session {session_id}. Status: {session.status}."

        if session.status not in ("idle", "succeeded", "failed", "cancelled"):
            try:
                await asyncio.wait_for(session.idle_event.wait(), timeout=600)
            except asyncio.TimeoutError:
                return (
                    f"Timed out waiting for response from session {session_id}. "
                    f"Status: {session.status}."
                )

        return _format_session_result(session)


class GetClaudeCodeStatusTool(Tool):
    """Check status of a Claude Code session."""

    def __init__(self, manager: ClaudeCodeSessionManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "get_claude_code_status"

    @property
    def description(self) -> str:
        return (
            "Get current status and optionally the full execution log "
            "of a Claude Code session."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The Claude Code session ID.",
                },
                "include_log": {
                    "type": "boolean",
                    "description": (
                        "If true, include the full execution log. "
                        "Default: false (summary only)."
                    ),
                },
            },
            "required": ["session_id"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        session_id: str = params["session_id"]
        include_log: bool = params.get("include_log", False)

        session = self._manager.get_session(session_id)
        if session is None:
            return f"Error: no session with id {session_id!r}."

        lines = [
            f"session_id={session.session_id}",
            f"status={session.status}",
            f"status_message={session.status_message}",
            f"caller={session.caller_agent_id}",
            f"cwd={session.cwd}",
            f"num_turns={session.num_turns}",
            f"cost=${session.total_cost_usd:.4f}" if session.total_cost_usd else "cost=n/a",
            f"started_at={session.started_at.isoformat()}",
        ]
        if session.ended_at:
            lines.append(f"ended_at={session.ended_at.isoformat()}")

        if include_log and session.execution_log:
            lines.append("\n--- Execution Log ---")
            log_text = json.dumps(session.execution_log, ensure_ascii=False, default=str)
            if len(log_text) > 10000:
                log_text = log_text[:10000] + "... (truncated)"
            lines.append(log_text)

        return "\n".join(lines)


class ListClaudeCodeSessionsTool(Tool):
    """List active Claude Code sessions for this agent."""

    def __init__(
        self,
        manager: ClaudeCodeSessionManager,
        *,
        caller_agent_id: str,
    ) -> None:
        self._manager = manager
        self._caller_agent_id = caller_agent_id

    @property
    def name(self) -> str:
        return "list_claude_code_sessions"

    @property
    def description(self) -> str:
        return "List active (non-terminal) Claude Code sessions for this agent."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, params: dict[str, Any]) -> str:
        sessions = self._manager.list_for_caller(self._caller_agent_id)
        if not sessions:
            return "No active Claude Code sessions."
        lines = ["Active Claude Code sessions:"]
        for s in sessions:
            lines.append(
                f"- session_id={s.session_id} status={s.status} "
                f"turns={s.num_turns} instruction={s.instruction[:80]}"
            )
        return "\n".join(lines)


class CloseClaudeCodeSessionTool(Tool):
    """Explicitly close a Claude Code session."""

    def __init__(
        self,
        manager: ClaudeCodeSessionManager,
        *,
        caller_agent_id: str,
    ) -> None:
        self._manager = manager
        self._caller_agent_id = caller_agent_id

    @property
    def name(self) -> str:
        return "close_claude_code_session"

    @property
    def description(self) -> str:
        return "Close/cancel a Claude Code session. Use when done with multi-turn interaction."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The Claude Code session ID to close.",
                },
                "status": {
                    "type": "string",
                    "description": "Terminal status: succeeded (default) or cancelled.",
                },
            },
            "required": ["session_id"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        session_id: str = params["session_id"]
        status: str = params.get("status", "succeeded")

        session = self._manager.get_session(session_id)
        if session is None:
            return f"Error: no session with id {session_id!r}."
        if session.caller_agent_id != self._caller_agent_id:
            return f"Error: session {session_id!r} belongs to another agent."

        try:
            session = await self._manager.close_session(session_id, status=status)
        except Exception as exc:
            return f"Error: {exc}"

        return f"Session {session_id} closed: {session.status}."


def _format_session_result(session: Any) -> str:
    lines = [
        f"session_id={session.session_id}",
        f"status={session.status}",
        f"num_turns={session.num_turns}",
    ]
    if session.total_cost_usd is not None:
        lines.append(f"cost=${session.total_cost_usd:.4f}")

    text_blocks: list[str] = []
    for entry in session.execution_log[-20:]:
        if entry.get("type") == "AssistantMessage":
            for block in entry.get("content", []):
                if block.get("type") == "text":
                    text_blocks.append(block["text"])

    if text_blocks:
        combined = "\n".join(text_blocks)
        if len(combined) > 3000:
            combined = combined[-3000:]
        lines.append(f"\n--- Output ---\n{combined}")

    return "\n".join(lines)
