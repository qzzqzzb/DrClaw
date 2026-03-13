"""Persistent store for project-agent activation preferences."""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger


class ProjectActivationStateStore:
    """Loads/saves IDs of project agents that should auto-start on daemon boot."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load project activation state from {}: {}", self.path, exc)
            return set()

        if not isinstance(raw, dict):
            return set()
        values = raw.get("activated_projects")
        if not isinstance(values, list):
            return set()
        out: set[str] = set()
        for item in values:
            if not isinstance(item, str):
                continue
            pid = item.strip()
            if pid:
                out.add(pid)
        return out

    def save(self, activated_project_ids: set[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "activated_projects": sorted(activated_project_ids),
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
