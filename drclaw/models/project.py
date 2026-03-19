"""Project data model, store protocol, and JSON-backed implementation."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from drclaw.soul import load_project_soul
from drclaw.utils.helpers import ensure_dir, slugify


class AmbiguousProjectNameError(Exception):
    """Raised when a partial name matches more than one project."""


class CorruptProjectStoreError(Exception):
    """Raised when projects.json is unreadable or structurally malformed."""


@dataclass
class StudentAgentConfig:
    id: str
    label: str
    enabled: bool = True
    soul: str = ""


@dataclass
class Project:
    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    description: str = ""
    status: str = "active"
    goals: str = ""
    tags: list[str] = field(default_factory=list)
    hub_template: str | None = None
    student_agents: list[StudentAgentConfig] = field(default_factory=list)


@runtime_checkable
class ProjectStore(Protocol):
    def list_projects(self) -> list[Project]: ...
    def get_project(self, project_id: str) -> Project | None: ...
    def create_project(self, name: str, description: str = "") -> Project: ...
    def update_project(self, project: Project) -> None: ...
    def delete_project(self, project_id: str) -> bool: ...
    def find_by_name(self, name: str) -> Project | None: ...


def _make_id(name: str) -> str:
    slug = slugify(name)[:20].rstrip("-")
    return f"{slug}-{secrets.token_hex(4)}"


def _to_dict(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
        "status": project.status,
        "goals": project.goals,
        "tags": project.tags,
        "hub_template": project.hub_template,
        "student_agents": [
            {
                "id": student.id,
                "label": student.label,
                "enabled": student.enabled,
                "soul": student.soul,
            }
            for student in project.student_agents
        ],
    }


def _student_from_dict(d: dict[str, Any]) -> StudentAgentConfig:
    raw_id = d.get("id")
    student_id = str(raw_id).strip() if raw_id is not None else ""
    if not student_id:
        raise ValueError("student agent is missing id")

    raw_label = d.get("label")
    label = str(raw_label).strip() if raw_label is not None else ""
    if not label:
        label = student_id

    raw_soul = d.get("soul")
    soul = str(raw_soul) if raw_soul is not None else ""

    return StudentAgentConfig(
        id=student_id,
        label=label,
        enabled=bool(d.get("enabled", True)),
        soul=soul,
    )


def _from_dict(d: dict[str, Any]) -> Project:
    raw_students = d.get("student_agents", [])
    if raw_students is None:
        raw_students = []
    if not isinstance(raw_students, list):
        raise ValueError("student_agents must be a list")
    return Project(
        id=d["id"],
        name=d["name"],
        description=d.get("description", ""),
        created_at=datetime.fromisoformat(d["created_at"]),
        updated_at=datetime.fromisoformat(d["updated_at"]),
        status=d.get("status", "active"),
        goals=d.get("goals", ""),
        tags=d.get("tags", []),
        hub_template=d.get("hub_template"),
        student_agents=[
            _student_from_dict(item)
            for item in raw_students
            if isinstance(item, dict)
        ],
    )


class JsonProjectStore:
    """ProjectStore backed by a JSON file at data_dir/projects.json."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._file = data_dir / "projects.json"

    def _load(self) -> list[Project]:
        if not self._file.exists():
            return []
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            return [_from_dict(d) for d in data]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise CorruptProjectStoreError(
                f"Corrupted projects.json at {self._file}: {exc}"
            ) from exc

    def _save(self, projects: list[Project]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._file.write_text(
            json.dumps([_to_dict(p) for p in projects], indent=2) + "\n",
            encoding="utf-8",
        )

    def list_projects(self) -> list[Project]:
        return self._load()

    def get_project(self, project_id: str) -> Project | None:
        for p in self._load():
            if p.id == project_id:
                return p
        return None

    def create_project(self, name: str, description: str = "") -> Project:
        if not name or not name.strip():
            raise ValueError("Project name cannot be empty")
        slug = slugify(name)
        if not slug:
            raise ValueError(f"Project name {name!r} produces an empty slug")

        projects = self._load()
        if any(p.name == name for p in projects):
            raise ValueError(f"A project named {name!r} already exists")

        existing_ids = {p.id for p in projects}
        for _ in range(5):
            project_id = _make_id(name)
            if project_id not in existing_ids:
                break

        now = datetime.now(tz=timezone.utc)
        project = Project(
            id=project_id,
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
        )
        projects.append(project)
        self._save(projects)

        project_dir = self._data_dir / "projects" / project.id
        workspace_dir = ensure_dir(project_dir / "workspace")
        ensure_dir(project_dir / "workspace" / "skills")
        ensure_dir(project_dir / "sessions")
        load_project_soul(workspace_dir)

        return project

    def update_project(self, project: Project) -> None:
        projects = self._load()
        for i, p in enumerate(projects):
            if p.id == project.id:
                projects[i] = project
                self._save(projects)
                return
        raise KeyError(f"No project with ID {project.id!r}")

    def delete_project(self, project_id: str) -> bool:
        projects = self._load()
        filtered = [p for p in projects if p.id != project_id]
        if len(filtered) == len(projects):
            return False
        self._save(filtered)
        # Project directories are intentionally preserved on disk for data safety.
        # Callers that need to remove workspace files must do so explicitly.
        return True

    def find_by_name(self, name: str) -> Project | None:
        projects = self._load()
        lower = name.lower()

        for p in projects:
            if p.name == name:
                return p

        matches = [p for p in projects if lower in p.name.lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousProjectNameError(
                f"{name!r} matches {len(matches)} projects: " + ", ".join(p.name for p in matches)
            )
        return None
