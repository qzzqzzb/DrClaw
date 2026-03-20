"""Project management tools for the Main Agent."""

from __future__ import annotations

import inspect
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from drclaw.models.project import Project, ProjectStore, StudentAgentConfig
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


def _student_not_found_message(
    project_store: ProjectStore,
    project_id: str,
    student_id: str,
) -> str:
    project = project_store.get_project(project_id)
    if project is None:
        return _project_not_found_message(project_store, project_id)
    available = [student.id for student in project.student_agents]
    if available:
        return (
            f"Error: student {student_id!r} not found in project {project_id!r}. "
            f"Available: {', '.join(available)}"
        )
    return f"Error: project {project_id!r} has no configured student agents."


def _project_student(project: Project, student_id: str) -> StudentAgentConfig | None:
    return next((student for student in project.student_agents if student.id == student_id), None)


def _project_student_skills_dir(projects_dir: Path, project_id: str, student_id: str) -> Path:
    return projects_dir / project_id / "agents" / student_id / "skills"


async def _run_project_update_callback(
    callback: Callable[[Project], Awaitable[Any] | Any] | None,
    project: Project,
) -> str | None:
    if callback is None:
        return None
    try:
        maybe_awaitable = callback(project)
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable
    except Exception as exc:
        return f"Error: failed to refresh project agents for {project.id!r}: {exc}"
    return None


class ListProjectStudentsTool(Tool):
    """List configured student agents for a project."""

    def __init__(self, project_store: ProjectStore) -> None:
        self._store = project_store

    @property
    def name(self) -> str:
        return "list_project_students"

    @property
    def description(self) -> str:
        return "List configured student agents for a project."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ID of the target project"},
            },
            "required": ["project_id"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        project_id: str = params["project_id"]
        project = self._store.get_project(project_id)
        if project is None:
            return _project_not_found_message(self._store, project_id)
        if not project.student_agents:
            return f"Project {project.name!r} ({project.id}) has no configured student agents."
        lines = [f"Student agents for project {project.name!r} ({project.id}):"]
        for student in project.student_agents:
            status = "enabled" if student.enabled else "disabled"
            lines.append(f"  [{student.id}] {student.label} ({status})")
        return "\n".join(lines)


class CreateProjectStudentTool(Tool):
    """Create a new student agent under a project."""

    def __init__(
        self,
        project_store: ProjectStore,
        projects_dir: Path,
        on_update: Callable[[Project], Awaitable[Any] | Any] | None = None,
    ) -> None:
        self._store = project_store
        self._projects_dir = projects_dir
        self._on_update = on_update

    @property
    def name(self) -> str:
        return "create_project_student"

    @property
    def description(self) -> str:
        return "Create a student agent for the specified project."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ID of the target project"},
                "id": {"type": "string", "description": "Stable student agent ID within the project"},
                "label": {"type": "string", "description": "Display label for the student agent"},
                "enabled": {
                    "type": "boolean",
                    "description": "Whether the student agent should be enabled immediately",
                },
                "soul": {
                    "type": "string",
                    "description": "Optional student-specific SOUL override content",
                },
            },
            "required": ["project_id", "id"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        project_id: str = params["project_id"]
        project = self._store.get_project(project_id)
        if project is None:
            return _project_not_found_message(self._store, project_id)

        student_id = str(params["id"]).strip()
        if not student_id:
            return "Error: student id cannot be empty."
        if _project_student(project, student_id) is not None:
            return (
                f"Error: student {student_id!r} already exists in project {project.name!r} "
                f"({project.id})."
            )

        raw_label = params.get("label")
        label = str(raw_label).strip() if raw_label is not None else ""
        soul_raw = params.get("soul")
        soul = str(soul_raw) if soul_raw is not None else ""
        student = StudentAgentConfig(
            id=student_id,
            label=label or student_id,
            enabled=bool(params.get("enabled", True)),
            soul=soul,
        )
        project.student_agents.append(student)
        project.updated_at = datetime.now(tz=timezone.utc)
        self._store.update_project(project)

        student_skills_dir = _project_student_skills_dir(
            self._projects_dir,
            project.id,
            student.id,
        )
        student_skills_dir.mkdir(parents=True, exist_ok=True)

        callback_error = await _run_project_update_callback(self._on_update, project)
        if callback_error is not None:
            return callback_error
        status = "enabled" if student.enabled else "disabled"
        return (
            f"Created student {student.label!r} ({student.id}) in project "
            f"{project.name!r} ({project.id}) as {status}."
        )


class UpdateProjectStudentTool(Tool):
    """Update an existing project student agent."""

    def __init__(
        self,
        project_store: ProjectStore,
        on_update: Callable[[Project], Awaitable[Any] | Any] | None = None,
    ) -> None:
        self._store = project_store
        self._on_update = on_update

    @property
    def name(self) -> str:
        return "update_project_student"

    @property
    def description(self) -> str:
        return (
            "Update an existing student agent's label, enabled state, or soul. "
            "The student id itself is immutable."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ID of the target project"},
                "student_id": {"type": "string", "description": "Existing student agent ID"},
                "label": {"type": "string", "description": "Updated display label"},
                "enabled": {
                    "type": "boolean",
                    "description": "Updated enabled state for the student agent",
                },
                "soul": {
                    "type": "string",
                    "description": "Updated student-specific SOUL override content",
                },
            },
            "required": ["project_id", "student_id"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        project_id: str = params["project_id"]
        project = self._store.get_project(project_id)
        if project is None:
            return _project_not_found_message(self._store, project_id)

        student_id = str(params["student_id"]).strip()
        student = _project_student(project, student_id)
        if student is None:
            return _student_not_found_message(self._store, project_id, student_id)

        if "id" in params:
            requested_id = str(params["id"]).strip()
            if requested_id and requested_id != student.id:
                return "Error: student id is immutable once created."

        if "label" in params:
            raw_label = str(params["label"]).strip()
            student.label = raw_label or student.id
        if "enabled" in params:
            student.enabled = bool(params["enabled"])
        if "soul" in params:
            student.soul = str(params["soul"])
        project.updated_at = datetime.now(tz=timezone.utc)
        self._store.update_project(project)

        callback_error = await _run_project_update_callback(self._on_update, project)
        if callback_error is not None:
            return callback_error
        status = "enabled" if student.enabled else "disabled"
        return (
            f"Updated student {student.label!r} ({student.id}) in project "
            f"{project.name!r} ({project.id}); status is now {status}."
        )


class RemoveProjectStudentTool(Tool):
    """Remove a student agent from a project configuration."""

    def __init__(
        self,
        project_store: ProjectStore,
        on_update: Callable[[Project], Awaitable[Any] | Any] | None = None,
    ) -> None:
        self._store = project_store
        self._on_update = on_update

    @property
    def name(self) -> str:
        return "remove_project_student"

    @property
    def description(self) -> str:
        return (
            "Remove a configured student agent from a project. "
            "This does not delete the student's on-disk history or private skills."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ID of the target project"},
                "student_id": {"type": "string", "description": "Existing student agent ID"},
            },
            "required": ["project_id", "student_id"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        project_id: str = params["project_id"]
        project = self._store.get_project(project_id)
        if project is None:
            return _project_not_found_message(self._store, project_id)

        student_id = str(params["student_id"]).strip()
        student = _project_student(project, student_id)
        if student is None:
            return _student_not_found_message(self._store, project_id, student_id)

        project.student_agents = [
            candidate for candidate in project.student_agents if candidate.id != student_id
        ]
        project.updated_at = datetime.now(tz=timezone.utc)
        self._store.update_project(project)

        callback_error = await _run_project_update_callback(self._on_update, project)
        if callback_error is not None:
            return callback_error
        return (
            f"Removed student {student.label!r} ({student.id}) from project "
            f"{project.name!r} ({project.id}). Existing history and private skills were preserved."
        )


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
