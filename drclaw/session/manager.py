"""Session persistence using JSONL files.

Format: first line is a metadata record (_type: "metadata"), remaining lines
are individual message dicts, one per line.

Session keys are agent-scoped (e.g. "main", "proj:abc123").
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from loguru import logger

from drclaw.utils.helpers import ensure_dir, safe_filename


class Message(TypedDict, total=False):
    """Typed shape for an LLM conversation message.

    ``role`` is always present; ``content`` may be a string or a richer
    structure (list of content blocks) for tool-use turns.
    """

    role: str
    content: Any
    source: str
    attachments: list[dict[str, Any]]


@dataclass
class Session:
    """In-memory representation of an agent conversation session."""

    session_key: str
    messages: list[Message] = field(default_factory=list)
    last_consolidated: int = 0

    def get_history(self, max_messages: int | None = None) -> list[Message]:
        """Return unconsolidated messages aligned to a user turn.

        Starts from ``last_consolidated`` (messages before that are already
        summarized in MEMORY.md).  Drops any leading non-user messages so the
        slice always starts with a "user" role — required by most LLM APIs.

        When *max_messages* is set, only the last N messages are returned
        (re-aligned to a user turn after slicing).
        """
        unconsolidated = self.messages[self.last_consolidated:]
        i = 0
        while i < len(unconsolidated) and unconsolidated[i].get("role") != "user":
            i += 1
        history = unconsolidated[i:]
        if max_messages is not None and len(history) > max_messages:
            history = history[-max_messages:]
            j = 0
            while j < len(history) and history[j].get("role") != "user":
                j += 1
            history = history[j:]
        return history

    def clear(self) -> None:
        """Reset session to empty state (messages already archived to memory)."""
        self.messages.clear()
        self.last_consolidated = 0


class SessionManager:
    """Loads and persists sessions as JSONL files under a given directory."""

    def __init__(self, sessions_dir: Path) -> None:
        self._dir = ensure_dir(sessions_dir)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _path(self, session_key: str) -> Path:
        """Map session key to JSONL file path.

        "main" -> sessions_dir/main.jsonl

        safe_filename() strips filesystem-unsafe chars (including path
        separators), and we additionally verify the resolved path stays
        inside self._dir to guard against any residual traversal attempt.
        """
        filename = safe_filename(session_key) + ".jsonl"
        candidate = self._dir / filename
        if not candidate.resolve().is_relative_to(self._dir.resolve()):
            raise ValueError(f"Session key escapes sessions directory: {session_key!r}")
        return candidate

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, session_key: str) -> Session:
        """Load session from disk.  Returns a fresh Session if none exists.

        Corrupt lines are skipped with a warning rather than raising so that
        a partially-written file (e.g. from a mid-write crash) degrades
        gracefully instead of making the session permanently unloadable.
        """
        path = self._path(session_key)
        if not path.exists():
            return Session(session_key=session_key)

        raw_lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if not raw_lines:
            return Session(session_key=session_key)

        # --- metadata line ---
        try:
            meta = json.loads(raw_lines[0])
        except json.JSONDecodeError:
            logger.warning("Corrupt metadata in session file {}, returning fresh session", path)
            return Session(session_key=session_key)

        last_consolidated = int(meta.get("last_consolidated", 0))

        # --- message lines ---
        messages: list[Message] = []
        for lineno, ln in enumerate(raw_lines[1:], start=2):
            try:
                messages.append(json.loads(ln))
            except json.JSONDecodeError:
                logger.warning("Skipping corrupt message on line {} of {}", lineno, path)

        return Session(
            session_key=session_key,
            messages=messages,
            last_consolidated=last_consolidated,
        )

    def save(self, session: Session) -> None:
        """Persist full session state to disk via atomic temp-file rename."""
        path = self._path(session.session_key)
        meta: dict[str, Any] = {
            "_type": "metadata",
            "session_key": session.session_key,
            "last_consolidated": session.last_consolidated,
        }
        lines = [json.dumps(meta, ensure_ascii=False)]
        for msg in session.messages:
            lines.append(json.dumps(msg, ensure_ascii=False))
        content = "\n".join(lines) + "\n"

        tmp = path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.rename(path)  # atomic on POSIX

    def add_message(self, session: Session, message: Message) -> None:
        """Append message to the in-memory list and persist efficiently.

        If the session file already exists the new line is appended directly
        (O(1)); otherwise the full file is written (O(n), first write only).
        """
        session.messages.append(message)
        path = self._path(session.session_key)
        if path.exists():
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(message, ensure_ascii=False) + "\n")
        else:
            self.save(session)
