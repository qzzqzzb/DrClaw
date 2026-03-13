"""Memory system for persistent agent memory."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from drclaw.utils.helpers import ensure_dir

if TYPE_CHECKING:
    from drclaw.agent.debug import DebugLogger
    from drclaw.providers.base import LLMProvider, LLMResponse
    from drclaw.session.manager import Session


_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        "description": (
                            "A paragraph (2-5 sentences) summarizing key events/decisions/topics. "
                            "Start with [YYYY-MM-DD HH:MM]. Include detail useful for grep search."
                        ),
                    },
                    "memory_update": {
                        "type": "string",
                        "description": (
                            "Full updated long-term memory as markdown. Include all existing "
                            "facts plus new ones. Return unchanged if nothing new."
                        ),
                    },
                },
                "required": ["history_entry", "memory_update"],
            },
        },
    }
]


class MemoryStore:
    """Two-layer memory: MEMORY.md (long-term facts) + HISTORY.md (grep-searchable log).

    Both files live directly in base_dir — not under any memory/ subdirectory.
    base_dir is created on construction if it does not already exist.
    """

    def __init__(self, base_dir: Path) -> None:
        ensure_dir(base_dir)
        self.memory_file = base_dir / "MEMORY.md"
        self.history_file = base_dir / "HISTORY.md"

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def get_memory_context(self) -> str:
        long_term = self.read_long_term()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    async def consolidate(
        self,
        session: Session,
        provider: LLMProvider,
        *,
        archive_all: bool = False,
        memory_window: int = 50,
        debug_logger: DebugLogger | None = None,
        on_response: Callable[[LLMResponse], None] | None = None,
    ) -> bool:
        """Consolidate old messages into MEMORY.md + HISTORY.md via LLM tool call.

        Returns True on success (including no-op), False on failure or when the LLM
        does not call save_memory.
        """
        if archive_all:
            old_messages = session.messages
            keep_count = 0
            logger.info("Memory consolidation (archive_all): {} messages", len(session.messages))
        else:
            keep_count = memory_window // 2
            if len(session.messages) <= keep_count:
                return True
            # No new messages outside the keep window since last consolidation.
            if session.last_consolidated >= len(session.messages) - keep_count:
                return True
            old_messages = session.messages[session.last_consolidated : -keep_count]
            if not old_messages:
                return True
            logger.info(
                "Memory consolidation: {} to consolidate, {} keep",
                len(old_messages),
                keep_count,
            )

        lines = []
        for m in old_messages:
            content = m.get("content")
            if not content or not isinstance(content, str):
                continue
            lines.append(f"{m['role'].upper()}: {content}")

        current_memory = self.read_long_term()
        conversation = "\n".join(lines)
        prompt = (
            "Process this conversation and call the save_memory tool with your consolidation.\n\n"
            f"## Current Long-term Memory\n{current_memory or '(empty)'}\n\n"
            f"## Conversation to Process\n{conversation}"
        )
        system = (
            "You are a memory consolidation agent. "
            "Call the save_memory tool with your consolidation of the conversation."
        )

        consolidation_messages = [{"role": "user", "content": prompt}]
        if debug_logger:
            debug_logger.log_consolidation_request(consolidation_messages)
        try:
            response = await provider.complete(
                messages=consolidation_messages,
                tools=_SAVE_MEMORY_TOOL,
                system=system,
            )
        except Exception:
            logger.exception("Memory consolidation: provider call failed")
            return False
        if debug_logger:
            debug_logger.log_consolidation_response(response)
        if on_response is not None:
            try:
                on_response(response)
            except Exception:
                logger.exception("Memory consolidation: usage callback failed")

        if not response.tool_calls:
            logger.warning("Memory consolidation: LLM did not call save_memory, skipping")
            return False

        # ToolCallRequest.arguments is typed dict[str, Any]; the provider layer
        # guarantees it is parsed before reaching us.
        call = response.tool_calls[0]
        if call.name != "save_memory":
            logger.warning("Memory consolidation: unexpected tool call {!r}", call.name)
            return False

        args = call.arguments
        if not isinstance(args, dict):
            logger.warning(
                "Memory consolidation: unexpected arguments type {}",
                type(args).__name__,
            )
            return False

        entry = args.get("history_entry") or ""
        update = args.get("memory_update") or ""
        if not entry and not update:
            logger.warning("Memory consolidation: save_memory called with empty arguments")
            return False

        if entry:
            if not isinstance(entry, str):
                entry = json.dumps(entry, ensure_ascii=False)
            self.append_history(entry)
        if update:
            if not isinstance(update, str):
                update = json.dumps(update, ensure_ascii=False)
            if update != current_memory:
                self.write_long_term(update)

        if archive_all:
            session.last_consolidated = 0
        else:
            # Trim consolidated messages to prevent unbounded session growth.
            session.messages = session.messages[-keep_count:]
            session.last_consolidated = 0
        logger.info(
            "Memory consolidation done: {} messages, last_consolidated={}",
            len(session.messages),
            session.last_consolidated,
        )
        return True
