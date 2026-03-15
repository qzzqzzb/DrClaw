"""Project management tools for the Main Agent."""

from __future__ import annotations

import inspect
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable

from drclaw.models.project import Project, ProjectStore
from drclaw.tools.base import Tool


def _project_not_found_message(project_store: ProjectStore, project_id: str) -> str:
    available = [p.id for p in project_store.list_projects()]
    if available:
        return f"Error: project {project_id!r} not found. Available: {', '.join(available)}"
    return f"Error: project {project_id!r} not found. No projects exist yet."


class RouteToProjectTool(Tool):
    """Route a message to a specific Project Agent."""

    def __init__(
        self,
        project_store: ProjectStore,
        agent_factory: Callable[[Project, str], Awaitable[str]],
    ) -> None:
        self._store = project_store
        self._factory = agent_factory

    @property
    def name(self) -> str:
        return "route_to_project"

    @property
    def description(self) -> str:
        return (
            "Route a message to the Project Agent for the specified project. "
            "IMPORTANT: After calling this tool, you MUST NOT perform the delegated "
            "work yourself. Do not run searches, write files, or execute commands "
            "related to the routed task. Wait for the project agent to report back."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ID of the target project"},
                "message": {
                    "type": "string",
                    "description": "Message to send to the project agent",
                },
            },
            "required": ["project_id", "message"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        project_id: str = params["project_id"]
        message: str = params["message"]
        project = self._store.get_project(project_id)
        if project is None:
            return _project_not_found_message(self._store, project_id)
        try:
            return await self._factory(project, message)
        except Exception as exc:
            return f"Error: routing to project {project_id!r} failed: {exc}"


class CreateProjectTool(Tool):
    """Create a new research project."""

    def __init__(
        self,
        project_store: ProjectStore,
        projects_dir: Path | None = None,
        on_create: Callable[[Project], Any] | None = None,
    ) -> None:
        self._store = project_store
        self._projects_dir = projects_dir
        self._on_create = on_create

    @property
    def name(self) -> str:
        return "create_project"

    @property
    def description(self) -> str:
        return "Create a new research project."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Project name"},
                "description": {"type": "string", "description": "Optional project description"},
                "soul_append": {
                    "type": "string",
                    "description": (
                        "Optional content to append to the new student agent SOUL.md "
                        "immediately after project creation."
                    ),
                },
            },
            "required": ["name"],
        }

    def _resolve_projects_dir(self) -> Path | None:
        if self._projects_dir is not None:
            return self._projects_dir
        data_dir = getattr(self._store, "_data_dir", None)
        if isinstance(data_dir, Path):
            return data_dir / "projects"
        return None

    def _append_to_project_soul(self, project_id: str, soul_append: str) -> None:
        projects_dir = self._resolve_projects_dir()
        if projects_dir is None:
            raise RuntimeError("projects_dir is not configured")

        soul_path = projects_dir / project_id / "workspace" / "SOUL.md"
        soul_path.parent.mkdir(parents=True, exist_ok=True)

        snippet = soul_append.strip("\n")
        if not snippet.strip():
            return

        existing = ""
        if soul_path.exists():
            existing = soul_path.read_text(encoding="utf-8")
        base = existing.rstrip("\n")
        separator = "\n\n" if base else ""
        soul_path.write_text(base + separator + snippet + "\n", encoding="utf-8")

    async def execute(self, params: dict[str, Any]) -> str:
        name: str = params["name"]
        description: str = params.get("description", "")
        soul_append_raw = params.get("soul_append")
        soul_append = str(soul_append_raw) if soul_append_raw is not None else ""
        try:
            project = self._store.create_project(name, description)
        except Exception as exc:
            return f"Error: {exc}"

        if soul_append.strip():
            try:
                self._append_to_project_soul(project.id, soul_append)
            except Exception as exc:
                return (
                    f"Created project {name!r} with ID {project.id!r}, "
                    f"but failed to append SOUL.md content: {exc}"
                )

        if self._on_create is not None:
            try:
                self._on_create(project)
            except Exception as exc:
                from loguru import logger
                logger.warning("on_create callback failed for {!r}: {}", name, exc)
        return f"Created project {name!r} with ID {project.id!r}."


class ListProjectsTool(Tool):
    """List all research projects."""

    def __init__(self, project_store: ProjectStore) -> None:
        self._store = project_store

    @property
    def name(self) -> str:
        return "list_projects"

    @property
    def description(self) -> str:
        return "List all research projects."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, params: dict[str, Any]) -> str:
        projects = self._store.list_projects()
        if not projects:
            return "No projects yet."
        lines = ["Projects:"]
        for p in projects:
            desc = f" — {p.description}" if p.description else ""
            lines.append(f"  [{p.id}] {p.name}{desc}")
        return "\n".join(lines)


class RemoveProjectTool(Tool):
    """Remove a project and delete its on-disk workspace."""

    def __init__(
        self,
        project_store: ProjectStore,
        projects_dir: Path,
        on_remove: Callable[[str], Awaitable[Any] | Any] | None = None,
    ) -> None:
        self._store = project_store
        self._projects_dir = projects_dir
        self._on_remove = on_remove

    @property
    def name(self) -> str:
        return "remove_project"

    @property
    def description(self) -> str:
        return "Permanently remove a project and delete its project workspace and agent state."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ID of the project to remove"},
            },
            "required": ["project_id"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        project_id: str = params["project_id"]
        project = self._store.get_project(project_id)
        if project is None:
            return _project_not_found_message(self._store, project_id)

        if self._on_remove is not None:
            try:
                maybe_awaitable = self._on_remove(project_id)
                if inspect.isawaitable(maybe_awaitable):
                    await maybe_awaitable
            except Exception as exc:
                return f"Error: failed to stop/remove project agent for {project_id!r}: {exc}"

        if not self._store.delete_project(project_id):
            return _project_not_found_message(self._store, project_id)

        project_dir = self._projects_dir / project_id
        try:
            if project_dir.exists():
                shutil.rmtree(project_dir)
        except Exception as exc:
            return (
                f"Error: project {project_id!r} was removed from the registry, "
                f"but deleting files failed: {exc}"
            )

        return (
            f"Removed project {project.name!r} ({project_id}) "
            "and deleted its workspace, sessions, and memory files."
        )
