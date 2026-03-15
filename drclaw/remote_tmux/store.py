"""Persistent store for project-scoped remote tmux sessions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass
class RemoteTmuxSessionRecord:
    """Persisted metadata for one managed remote tmux session."""

    session_id: str
    host: str
    tmux_session_name: str
    command: str
    remote_cwd: str
    remote_log_path: str
    remote_exit_code_path: str
    status: str
    created_at: datetime
    updated_at: datetime
    last_checked_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["created_at"] = self.created_at.isoformat()
        raw["updated_at"] = self.updated_at.isoformat()
        raw["last_checked_at"] = (
            self.last_checked_at.isoformat() if self.last_checked_at is not None else None
        )
        return raw

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RemoteTmuxSessionRecord:
        return cls(
            session_id=str(raw["session_id"]),
            host=str(raw["host"]),
            tmux_session_name=str(raw["tmux_session_name"]),
            command=str(raw["command"]),
            remote_cwd=str(raw.get("remote_cwd", "")),
            remote_log_path=str(raw["remote_log_path"]),
            remote_exit_code_path=str(raw["remote_exit_code_path"]),
            status=str(raw.get("status", "running")),
            created_at=datetime.fromisoformat(str(raw["created_at"])),
            updated_at=datetime.fromisoformat(str(raw["updated_at"])),
            last_checked_at=(
                datetime.fromisoformat(str(raw["last_checked_at"]))
                if raw.get("last_checked_at")
                else None
            ),
        )


class RemoteTmuxSessionStore:
    """JSON-backed store for remote tmux session records."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def list_sessions(self, *, include_terminal: bool = True) -> list[RemoteTmuxSessionRecord]:
        records = list(self._load().values())
        if not include_terminal:
            records = [r for r in records if r.status not in {"succeeded", "failed", "cancelled"}]
        return sorted(records, key=lambda item: item.created_at)

    def get(self, session_id: str) -> RemoteTmuxSessionRecord | None:
        return self._load().get(session_id)

    def upsert(self, record: RemoteTmuxSessionRecord) -> None:
        records = self._load()
        records[record.session_id] = record
        self._save(records)

    def touch_status(
        self,
        session_id: str,
        *,
        status: str,
        checked: bool = False,
    ) -> RemoteTmuxSessionRecord:
        records = self._load()
        rec = records.get(session_id)
        if rec is None:
            raise KeyError(f"Unknown remote tmux session: {session_id}")
        now = _utc_now()
        rec.status = status
        rec.updated_at = now
        if checked:
            rec.last_checked_at = now
        records[session_id] = rec
        self._save(records)
        return rec

    def find_active_by_host_and_tmux(
        self,
        *,
        host: str,
        tmux_session_name: str,
    ) -> RemoteTmuxSessionRecord | None:
        for record in self._load().values():
            if record.status in {"succeeded", "failed", "cancelled"}:
                continue
            if record.host == host and record.tmux_session_name == tmux_session_name:
                return record
        return None

    def _load(self) -> dict[str, RemoteTmuxSessionRecord]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        raw_records = payload.get("sessions")
        if not isinstance(raw_records, list):
            return {}

        records: dict[str, RemoteTmuxSessionRecord] = {}
        for item in raw_records:
            if not isinstance(item, dict):
                continue
            try:
                rec = RemoteTmuxSessionRecord.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            records[rec.session_id] = rec
        return records

    def _save(self, records: dict[str, RemoteTmuxSessionRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "sessions": [rec.to_dict() for rec in sorted(records.values(), key=lambda item: item.created_at)],
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
