"""Tests for project management tools."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from drclaw.models.project import JsonProjectStore, Project
from drclaw.tools.project_tools import (
    CreateProjectTool,
    ListProjectsTool,
    RemoveProjectTool,
    RouteToProjectTool,
)

# ---------------------------------------------------------------------------
# RouteToProjectTool
# ---------------------------------------------------------------------------


class TestRouteToProjectTool:
    async def test_route_valid_project(self, tmp_path: Path) -> None:
        store = JsonProjectStore(tmp_path)
        project = store.create_project("Alpha", "test project")

        calls: list[tuple[Project, str]] = []

        async def factory(p: Project, msg: str) -> str:
            calls.append((p, msg))
            return f"response from {p.name}"

        tool = RouteToProjectTool(store, factory)
        result = await tool.execute({"project_id": project.id, "message": "hello"})

        assert result == f"response from {project.name}"
        assert len(calls) == 1
        assert calls[0][0].id == project.id
        assert calls[0][1] == "hello"

    async def test_route_invalid_project(self, tmp_path: Path) -> None:
        store = JsonProjectStore(tmp_path)
        store.create_project("Beta")
        store.create_project("Gamma")

        async def factory(p: Project, msg: str) -> str:
            return "should not be called"

        tool = RouteToProjectTool(store, factory)
        result = await tool.execute({"project_id": "nonexistent-id", "message": "hi"})

        assert "Error" in result
        # Both existing project IDs must appear in the error message
        projects = store.list_projects()
        for p in projects:
            assert p.id in result

    async def test_route_factory_exception_returns_error(self, tmp_path: Path) -> None:
        store = JsonProjectStore(tmp_path)
        project = store.create_project("Crash")

        async def factory(p: Project, msg: str) -> str:
            raise RuntimeError("LLM unavailable")

        tool = RouteToProjectTool(store, factory)
        result = await tool.execute({"project_id": project.id, "message": "hi"})

        assert result.startswith("Error:")
        assert "LLM unavailable" in result

    async def test_route_invalid_project_no_projects(self, tmp_path: Path) -> None:
        store = JsonProjectStore(tmp_path)

        async def factory(p: Project, msg: str) -> str:
            return "nope"

        tool = RouteToProjectTool(store, factory)
        result = await tool.execute({"project_id": "ghost", "message": "yo"})

        assert "Error" in result
        assert "ghost" in result


# ---------------------------------------------------------------------------
# CreateProjectTool
# ---------------------------------------------------------------------------


class TestCreateProjectTool:
    async def test_create_project_tool(self, tmp_path: Path) -> None:
        store = JsonProjectStore(tmp_path)
        tool = CreateProjectTool(store)

        result = await tool.execute({"name": "My Research", "description": "A test project"})

        assert "My Research" in result
        projects = store.list_projects()
        assert len(projects) == 1
        assert projects[0].name == "My Research"
        assert projects[0].id in result

    async def test_create_project_no_description(self, tmp_path: Path) -> None:
        store = JsonProjectStore(tmp_path)
        tool = CreateProjectTool(store)

        result = await tool.execute({"name": "Minimal"})

        assert "Minimal" in result
        assert len(store.list_projects()) == 1

    async def test_create_project_duplicate_name_returns_error(self, tmp_path: Path) -> None:
        store = JsonProjectStore(tmp_path)
        store.create_project("Dupe")
        tool = CreateProjectTool(store)

        result = await tool.execute({"name": "Dupe"})

        assert result.startswith("Error:")
        assert len(store.list_projects()) == 1

    async def test_on_create_callback_fires(self, tmp_path: Path) -> None:
        store = JsonProjectStore(tmp_path)
        created: list[Project] = []
        tool = CreateProjectTool(store, on_create=lambda p: created.append(p))

        await tool.execute({"name": "Callback"})

        assert len(created) == 1
        assert created[0].name == "Callback"

    async def test_on_create_callback_exception_non_fatal(self, tmp_path: Path) -> None:
        store = JsonProjectStore(tmp_path)

        def exploding_callback(p: Project) -> None:
            raise RuntimeError("spawn failed")

        tool = CreateProjectTool(store, on_create=exploding_callback)

        result = await tool.execute({"name": "Boom"})

        assert "Boom" in result
        assert not result.startswith("Error:")
        assert len(store.list_projects()) == 1

    async def test_on_create_callback_not_called_on_error(self, tmp_path: Path) -> None:
        store = JsonProjectStore(tmp_path)
        store.create_project("Dupe")
        created: list[Project] = []
        tool = CreateProjectTool(store, on_create=lambda p: created.append(p))

        result = await tool.execute({"name": "Dupe"})

        assert result.startswith("Error:")
        assert len(created) == 0


# ---------------------------------------------------------------------------
# ListProjectsTool
# ---------------------------------------------------------------------------


class TestListProjectsTool:
    async def test_list_projects_populated(self, tmp_path: Path) -> None:
        store = JsonProjectStore(tmp_path)
        store.create_project("Alpha", "first project")
        store.create_project("Beta", "second project")

        tool = ListProjectsTool(store)
        result = await tool.execute({})

        assert "Alpha" in result
        assert "Beta" in result
        for p in store.list_projects():
            assert p.id in result

    async def test_list_projects_empty(self, tmp_path: Path) -> None:
        store = JsonProjectStore(tmp_path)
        tool = ListProjectsTool(store)

        result = await tool.execute({})

        assert "No projects" in result


# ---------------------------------------------------------------------------
# RemoveProjectTool
# ---------------------------------------------------------------------------


class TestRemoveProjectTool:
    async def test_remove_project_deletes_store_and_workspace(self, tmp_path: Path) -> None:
        store = JsonProjectStore(tmp_path)
        project = store.create_project("ToDelete", "tmp")
        project_dir = tmp_path / "projects" / project.id
        assert project_dir.is_dir()

        tool = RemoveProjectTool(store, projects_dir=tmp_path / "projects")
        result = await tool.execute({"project_id": project.id})

        assert "Removed project" in result
        assert store.get_project(project.id) is None
        assert not project_dir.exists()

    async def test_remove_project_calls_async_callback(self, tmp_path: Path) -> None:
        store = JsonProjectStore(tmp_path)
        project = store.create_project("WithAgent")
        on_remove = AsyncMock(return_value=True)

        tool = RemoveProjectTool(store, projects_dir=tmp_path / "projects", on_remove=on_remove)
        await tool.execute({"project_id": project.id})

        on_remove.assert_awaited_once_with(project.id)

    async def test_remove_project_missing_returns_error(self, tmp_path: Path) -> None:
        store = JsonProjectStore(tmp_path)
        store.create_project("Alpha")
        store.create_project("Beta")

        tool = RemoveProjectTool(store, projects_dir=tmp_path / "projects")
        result = await tool.execute({"project_id": "nope"})

        assert result.startswith("Error:")
        for p in store.list_projects():
            assert p.id in result

    async def test_remove_project_callback_error_is_fatal(self, tmp_path: Path) -> None:
        store = JsonProjectStore(tmp_path)
        project = store.create_project("FailStop")
        project_dir = tmp_path / "projects" / project.id

        async def bad_callback(project_id: str) -> None:
            raise RuntimeError(f"failed stop {project_id}")

        tool = RemoveProjectTool(
            store, projects_dir=tmp_path / "projects", on_remove=bad_callback
        )
        result = await tool.execute({"project_id": project.id})

        assert result.startswith("Error:")
        assert store.get_project(project.id) is not None
        assert project_dir.exists()
