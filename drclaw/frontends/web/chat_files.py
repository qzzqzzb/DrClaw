"""Persistent chat file storage for the Web UI."""

from __future__ import annotations

import json
import mimetypes
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from drclaw.utils.helpers import ensure_dir


@dataclass(frozen=True)
class ChatFileRecord:
    """Metadata for a stored chat attachment."""

    file_id: str
    name: str
    mime: str
    size: int
    path: Path
    created_at: str
    source: str


class ChatFileStore:
    """Store uploaded and outbound files for web chat downloads."""

    def __init__(self, data_path: Path) -> None:
        self._data_path = data_path.resolve()
        self._root = ensure_dir(self._data_path / "web" / "chat_files")
        self._files_dir = ensure_dir(self._root / "files")
        self._meta_dir = ensure_dir(self._root / "meta")

    @staticmethod
    def _safe_name(filename: str) -> str:
        raw = Path(str(filename)).name.strip()
        if not raw or raw in {".", ".."}:
            return "file"
        return raw.replace("\x00", "")

    def _meta_path(self, file_id: str) -> Path:
        if len(file_id) != 32 or any(c not in "0123456789abcdef" for c in file_id):
            raise ValueError("Invalid file id.")
        return self._meta_dir / f"{file_id}.json"

    @staticmethod
    def _mime(filename: str, content_type: str | None) -> str:
        if isinstance(content_type, str):
            trimmed = content_type.strip()
            if trimmed and trimmed.lower() != "application/octet-stream":
                return trimmed
        guessed, _ = mimetypes.guess_type(filename)
        return guessed or "application/octet-stream"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(tz=timezone.utc).isoformat()

    @staticmethod
    def _size(path: Path) -> int:
        return int(path.stat().st_size)

    def _write_meta(self, rec: ChatFileRecord) -> None:
        payload = {
            "id": rec.file_id,
            "name": rec.name,
            "mime": rec.mime,
            "size": rec.size,
            "path": str(rec.path),
            "created_at": rec.created_at,
            "source": rec.source,
        }
        self._meta_path(rec.file_id).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def _parse_meta(payload: dict[str, Any]) -> ChatFileRecord:
        return ChatFileRecord(
            file_id=str(payload.get("id", "")),
            name=str(payload.get("name", "file")),
            mime=str(payload.get("mime", "application/octet-stream")),
            size=max(0, int(payload.get("size", 0))),
            path=Path(str(payload.get("path", ""))),
            created_at=str(payload.get("created_at", "")),
            source=str(payload.get("source", "unknown")),
        )

    def _persist_copy(
        self,
        *,
        source_path: Path,
        filename: str,
        content_type: str | None,
        source: str,
    ) -> ChatFileRecord:
        safe_name = self._safe_name(filename)
        file_id = uuid.uuid4().hex
        suffix = Path(safe_name).suffix
        stored_name = f"{file_id}{suffix}" if suffix else file_id
        stored_path = (self._files_dir / stored_name).resolve()
        if not stored_path.is_relative_to(self._files_dir.resolve()):
            raise ValueError("Stored file path escapes storage root.")
        shutil.copy2(source_path, stored_path)

        rec = ChatFileRecord(
            file_id=file_id,
            name=safe_name,
            mime=self._mime(safe_name, content_type),
            size=self._size(stored_path),
            path=stored_path,
            created_at=self._now_iso(),
            source=source,
        )
        self._write_meta(rec)
        return rec

    def save_upload(
        self,
        *,
        source_path: Path,
        filename: str,
        content_type: str | None,
    ) -> ChatFileRecord:
        """Persist a user-uploaded file."""
        return self._persist_copy(
            source_path=source_path,
            filename=filename,
            content_type=content_type,
            source="upload",
        )

    def ingest_outbound_media(self, source_path: Path) -> ChatFileRecord:
        """Copy an outbound media file into web chat storage and return metadata."""
        resolved = source_path.resolve()
        if not resolved.exists() or not resolved.is_file():
            raise ValueError(f"Media file not found: {source_path}")
        if not resolved.is_relative_to(self._data_path):
            raise ValueError(f"Media file outside data directory: {source_path}")
        return self._persist_copy(
            source_path=resolved,
            filename=resolved.name,
            content_type=None,
            source="outbound",
        )

    def get(self, file_id: str) -> ChatFileRecord | None:
        """Load a stored file record by id."""
        try:
            meta_path = self._meta_path(file_id)
        except ValueError:
            return None
        if not meta_path.is_file():
            return None
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        rec = self._parse_meta(payload)
        resolved_path = rec.path.resolve()
        if not resolved_path.is_relative_to(self._files_dir.resolve()):
            return None
        if not resolved_path.is_file():
            return None
        return ChatFileRecord(
            file_id=rec.file_id,
            name=rec.name,
            mime=rec.mime,
            size=rec.size,
            path=resolved_path,
            created_at=rec.created_at,
            source=rec.source,
        )

    def resolve_ids_for_agent(self, ids: list[str]) -> list[dict[str, Any]]:
        """Resolve upload IDs into metadata passed to the agent runtime."""
        out: list[dict[str, Any]] = []
        for raw_id in ids:
            rec = self.get(raw_id)
            if rec is None:
                raise ValueError(f"Unknown file id: {raw_id}")
            out.append(
                {
                    "id": rec.file_id,
                    "name": rec.name,
                    "mime": rec.mime,
                    "size": rec.size,
                    "path": str(rec.path),
                    "download_url": self.download_url(rec.file_id),
                }
            )
        return out

    @staticmethod
    def download_url(file_id: str) -> str:
        return f"/api/chat/files/{file_id}"

    def public_descriptor(self, rec: ChatFileRecord) -> dict[str, Any]:
        return {
            "id": rec.file_id,
            "name": rec.name,
            "mime": rec.mime,
            "size": rec.size,
            "download_url": self.download_url(rec.file_id),
        }
