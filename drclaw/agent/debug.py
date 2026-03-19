"""Debug logger for LLM interaction tracing."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from drclaw.utils.helpers import timestamp

if TYPE_CHECKING:
    from drclaw.providers.base import LLMResponse

_SYSTEM_PROMPT_PREVIEW_LEN = 500


def make_debug_logger(
    debug_dir: Path, agent_id: str, *, full: bool = False
) -> tuple[Path, DebugLogger]:
    """Create a DebugLogger with a unique path for the given agent.

    Returns (log_path, logger).
    File: debug/{ts}_{agent_id}.jsonl (agent_id sanitized for filesystem).
    Also creates a human-readable .log file alongside it.
    """
    debug_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_id = agent_id.replace(":", "_").replace("/", "_")
    log_path = debug_dir / f"{ts}_{safe_id}.jsonl"
    return log_path, DebugLogger(log_path, full=full)


class DebugLogger:
    """JSONL debug logger for LLM request/response cycles.

    Two modes:
    - ``full=False`` (summary): message counts, roles, truncated system prompt, last user message
    - ``full=True``: complete messages and tools arrays
    """

    def __init__(self, path: Path, *, full: bool = False) -> None:
        self._file = open(path, "a", encoding="utf-8")  # noqa: SIM115
        self._full = full
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        # Human-readable log alongside the JSONL
        self._human_path = path.with_suffix(".log")
        self._human_file = open(self._human_path, "a", encoding="utf-8")  # noqa: SIM115

    def add_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        self._listeners = [item for item in self._listeners if item is not listener]

    @staticmethod
    def _attach_context(
        entry: dict[str, Any],
        *,
        agent_id: str | None = None,
        session_key: str | None = None,
    ) -> dict[str, Any]:
        payload = dict(entry)
        if agent_id:
            payload["agent_id"] = agent_id
        if session_key:
            payload["session_key"] = session_key
        return payload

    def _write(self, entry: dict[str, Any]) -> None:
        if self._file is None:
            return
        payload = dict(entry)
        payload.setdefault("ts", timestamp())
        self._file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._file.flush()
        for listener in list(self._listeners):
            try:
                listener(dict(payload))
            except Exception:
                continue

    def _write_human(self, text: str) -> None:
        if self._human_file is None:
            return
        self._human_file.write(text)
        self._human_file.flush()

    def log_session_start(
        self,
        session_key: str,
        model: str,
        *,
        agent_id: str | None = None,
    ) -> None:
        self._write(
            self._attach_context(
                {"type": "session_start", "session_key": session_key, "model": model},
                agent_id=agent_id,
                session_key=session_key,
            )
        )
        self._write_human(
            f"{'='*60}\nSession: {session_key}  Model: {model}\n{timestamp()}\n{'='*60}\n\n"
        )

    def log_request(
        self,
        iteration: int,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        agent_id: str | None = None,
        session_key: str | None = None,
    ) -> None:
        if self._full:
            self._write(
                self._attach_context(
                    {
                        "type": "llm_request",
                        "iteration": iteration,
                        "messages": messages,
                        "tools": tools,
                    },
                    agent_id=agent_id,
                    session_key=session_key,
                )
            )
        else:
            roles = [m["role"] for m in messages if "role" in m]
            system_prompt = ""
            last_user = ""
            for m in messages:
                if m.get("role") == "system":
                    content = m.get("content", "")
                    system_prompt = content[:_SYSTEM_PROMPT_PREVIEW_LEN]
                    break
            for m in reversed(messages):
                if m.get("role") == "user":
                    last_user = m.get("content", "")
                    if isinstance(last_user, list):
                        last_user = str(last_user)
                    break
            self._write(
                self._attach_context(
                    {
                        "type": "llm_request",
                        "iteration": iteration,
                        "message_count": len(messages),
                        "roles": roles,
                        "system_prompt_preview": system_prompt,
                        "last_user_message": last_user,
                        "tool_count": len(tools) if tools else 0,
                    },
                    agent_id=agent_id,
                    session_key=session_key,
                )
            )

        # Human log: only the latest user message (not the full context)
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                if isinstance(last_user, list):
                    last_user = str(last_user)
                break
        if iteration == 0 and last_user:
            self._write_human(f"--- Turn (iteration {iteration}) ---\n")
            self._write_human(f"[USER] {last_user}\n\n")

    def log_response(
        self,
        iteration: int,
        response: LLMResponse,
        *,
        agent_id: str | None = None,
        session_key: str | None = None,
    ) -> None:
        self._total_input_tokens += response.input_tokens
        self._total_output_tokens += response.output_tokens
        tc = [{"name": t.name, "arguments": t.arguments} for t in response.tool_calls]
        self._write(
            self._attach_context(
                {
                    "type": "llm_response",
                    "iteration": iteration,
                    "content": response.content,
                    "tool_calls": tc,
                    "stop_reason": response.stop_reason,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                },
                agent_id=agent_id,
                session_key=session_key,
            )
        )

        if response.content:
            self._write_human(f"[LLM] {response.content}\n\n")
        for t in response.tool_calls:
            args_str = json.dumps(t.arguments, ensure_ascii=False, indent=2)
            self._write_human(f"[TOOL CALL] {t.name}({args_str})\n")

    def log_tool_exec(
        self,
        iteration: int,
        tool_name: str,
        arguments: dict[str, Any],
        result: str,
        *,
        agent_id: str | None = None,
        session_key: str | None = None,
    ) -> None:
        self._write(
            self._attach_context(
                {
                    "type": "tool_exec",
                    "iteration": iteration,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "result": result,
                },
                agent_id=agent_id,
                session_key=session_key,
            )
        )

        self._write_human(f"[TOOL RESULT] {tool_name} → {result}\n\n")

    def log_consolidation_request(self, messages: list[dict[str, Any]]) -> None:
        entry: dict[str, Any] = {
            "type": "consolidation_request",
            "message_count": len(messages),
        }
        if self._full:
            entry["messages"] = messages
        self._write(entry)

    def log_consolidation_response(self, response: LLMResponse) -> None:
        self._total_input_tokens += response.input_tokens
        self._total_output_tokens += response.output_tokens
        tc = [{"name": t.name, "arguments": t.arguments} for t in response.tool_calls]
        self._write(
            {
                "type": "consolidation_response",
                "content": response.content,
                "tool_calls": tc,
                "stop_reason": response.stop_reason,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
            }
        )

    def close(self) -> None:
        if self._file is None:
            return
        self._write(
            {
                "type": "session_end",
                "total_input_tokens": self._total_input_tokens,
                "total_output_tokens": self._total_output_tokens,
            }
        )
        self._write_human(
            f"\n{'='*60}\n"
            f"Tokens: {self._total_input_tokens} in / {self._total_output_tokens} out\n"
            f"{'='*60}\n"
        )
        self._file.close()
        self._file = None  # type: ignore[assignment]
        if self._human_file is not None:
            self._human_file.close()
            self._human_file = None  # type: ignore[assignment]
