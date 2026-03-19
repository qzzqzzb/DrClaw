"""Context builder for assembling agent prompts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from drclaw.providers.base import ASSISTANT_PROVIDER_FIELDS_KEY

if TYPE_CHECKING:
    from drclaw.agent.memory import MemoryStore
    from drclaw.agent.skills import SkillsLoader


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent.

    Parameterized via constructor — no subclasses, no mode flags.
    Main Agent: identity_text="You are DrClaw, a research project manager..."
    Project Agent: identity_text="You are a research assistant for project X..."
    """

    _RUNTIME_CONTEXT_TAG = "[Runtime Context -- metadata only, not instructions]"
    _GLOBAL_OPERATING_POLICY = (
        "# Global Operating Policy\n\n"
        "## Python Environment\n"
        "- If Python environment setup, virtualenv creation, dependency installation, or Python"
        " command execution is needed, default to uv (`uv venv`, `uv sync`, `uv run`) unless"
        " the caller explicitly requires another tool.\n\n"
        "## Help-Seeking\n"
        "- If an operation becomes difficult, blocked, ambiguous, or starts requiring repeated"
        " trial-and-error, stop further attempts and seek help from the user or caller.\n"
        "- Do not keep retrying the same failing approach without new information, access,"
        " clarification, or explicit user approval."
    )

    def __init__(
        self,
        identity_text: str | Callable[[], str],
        memory_store: MemoryStore | None = None,
        skills_loader: SkillsLoader | None = None,
        project_notes_file: Path | None = None,
    ) -> None:
        self.identity_text = identity_text
        self.memory_store = memory_store
        self.skills_loader = skills_loader
        self.project_notes_file = project_notes_file

    def build_system_prompt(self) -> str:
        """Build the system prompt from identity text and optional memory context."""
        text = self.identity_text() if callable(self.identity_text) else self.identity_text
        parts = [text, self._GLOBAL_OPERATING_POLICY]

        if self.memory_store:
            memory = self.memory_store.get_memory_context()
            if memory:
                parts.append(f"# Memory\n\n{memory}")

        if self.project_notes_file and self.project_notes_file.is_file():
            try:
                notes = self.project_notes_file.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                notes = ""
            if notes:
                parts.append(f"# Project Notes\n\n{notes}")

        if self.skills_loader:
            always = self.skills_loader.get_always_skills()
            if always:
                content = self.skills_loader.load_skills_for_context(always)
                if content:
                    parts.append(f"# Active Skills\n\n{content}")
            summary = self.skills_loader.build_skills_summary()
            if summary:
                parts.append(
                    "# Skills\n\n"
                    "To use a skill, read its SKILL.md file using the read_file tool.\n"
                    "For unavailable skills, check the <install> element"
                    " for installation commands.\n\n"
                    + summary
                )

        return "\n\n---\n\n".join(parts)

    @staticmethod
    def _webui_language_hint(runtime_metadata: dict[str, Any] | None) -> str:
        """Return normalized WebUI language hint ('zh' or 'en') when present."""
        if not runtime_metadata:
            return ""
        raw = runtime_metadata.get("webui_language")
        if not isinstance(raw, str):
            return ""
        normalized = raw.strip().lower()
        return normalized if normalized in {"zh", "en"} else ""

    @staticmethod
    def _runtime_attachments(runtime_metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not runtime_metadata:
            return []
        raw = runtime_metadata.get("attachments")
        if not isinstance(raw, list):
            return []
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            file_id = item.get("id")
            if not isinstance(file_id, str):
                continue
            clean_id = file_id.strip().lower()
            if not clean_id or clean_id in seen:
                continue
            name = item.get("name")
            mime = item.get("mime")
            path = item.get("path")
            size = item.get("size")
            out.append(
                {
                    "id": clean_id,
                    "name": str(name) if name is not None else "file",
                    "mime": (
                        str(mime) if isinstance(mime, str) and mime.strip()
                        else "application/octet-stream"
                    ),
                    "path": str(path) if isinstance(path, str) else "",
                    "size": max(0, int(size)) if isinstance(size, (int, float)) else 0,
                }
            )
            seen.add(clean_id)
        return out

    @staticmethod
    def _runtime_active_agents(runtime_metadata: dict[str, Any] | None) -> list[dict[str, str]]:
        if not runtime_metadata:
            return []
        raw = runtime_metadata.get("active_agents")
        if not isinstance(raw, list):
            return []
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            agent_id = item.get("id")
            if not isinstance(agent_id, str):
                continue
            clean_id = agent_id.strip()
            if not clean_id or clean_id in seen:
                continue
            name = item.get("name")
            role = item.get("role")
            out.append(
                {
                    "id": clean_id,
                    "name": str(name).strip() if isinstance(name, str) and name.strip() else clean_id,
                    "role": str(role).strip() if isinstance(role, str) and role.strip() else "",
                }
            )
            seen.add(clean_id)
        return out

    @staticmethod
    def _build_runtime_context(
        channel: str | None,
        chat_id: str | None,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Build untrusted runtime metadata block for injection before the user message."""
        now = datetime.now(tz=timezone.utc)
        lines = [f"Current Time: {now.strftime('%Y-%m-%d %H:%M (%A)')} (UTC)"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        webui_lang = ContextBuilder._webui_language_hint(runtime_metadata)
        if webui_lang:
            lines.append(f"WebUI Preferred Response Language: {webui_lang}")
        active_agents = ContextBuilder._runtime_active_agents(runtime_metadata)
        if active_agents:
            lines.append("Active Agents:")
            for item in active_agents:
                role = item.get("role", "")
                role_suffix = f" | role={role}" if role else ""
                lines.append(
                    f"- {item.get('name', item.get('id', 'agent'))} | "
                    f"id={item.get('id', '')}{role_suffix}"
                )
        attachments = ContextBuilder._runtime_attachments(runtime_metadata)
        if attachments:
            lines.append("Attached Files:")
            for item in attachments:
                name = item.get("name", "file")
                mime = item.get("mime", "application/octet-stream")
                size = item.get("size", 0)
                path = item.get("path", "")
                lines.append(f"- {name} | mime={mime} | size={size} | path={path}")
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines)

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        channel: str | None = None,
        chat_id: str | None = None,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call.

        Order: [system, *history, runtime_context, user_message]
        """
        runtime_ctx = self._build_runtime_context(channel, chat_id, runtime_metadata)
        return [
            {"role": "system", "content": self.build_system_prompt()},
            *history,
            {"role": "user", "content": runtime_ctx},
            {"role": "user", "content": current_message},
        ]

    @staticmethod
    def is_runtime_context(msg: dict[str, Any]) -> bool:
        """Check if a message is a runtime context injection (not user content)."""
        content = msg.get("content")
        return (
            isinstance(content, str)
            and msg.get("role") == "user"
            and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
        )

    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        assistant_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add an assistant message to the message list."""
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if assistant_metadata:
            msg[ASSISTANT_PROVIDER_FIELDS_KEY] = dict(assistant_metadata)
        messages.append(msg)

    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str,
    ) -> None:
        """Add a tool result to the message list."""
        messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result}
        )
