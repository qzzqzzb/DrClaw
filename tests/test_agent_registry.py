"""Tests for the AgentRegistry."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from drclaw.agent.registry import AgentRegistry, AgentStatus
from drclaw.bus.queue import MessageBus
from drclaw.config.schema import DrClawConfig
from drclaw.models.project import JsonProjectStore, StudentAgentConfig
from tests.mocks import MockProvider, make_project


@pytest.fixture
def config(tmp_path: Path) -> DrClawConfig:
    return DrClawConfig(data_dir=str(tmp_path / "drclaw_data"))


@pytest.fixture
def bus() -> MessageBus:
    return MessageBus()


def _patch_loop_run():
    """Patch AgentLoop.run to be a no-op coroutine so tasks complete immediately."""
    async def _fake_run(self):  # noqa: ANN001
        pass
    return patch("drclaw.agent.loop.AgentLoop.run", _fake_run)


# ---------------------------------------------------------------------------
# start_main
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_main(config: DrClawConfig, bus: MessageBus) -> None:
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)

    with _patch_loop_run():
        handle = registry.start_main()

    assert handle.agent_id == "main"
    assert handle.agent_type == "main"
    assert handle.label == "Assistant Agent"
    assert handle.project is None
    assert handle.loop is not None
    assert handle.task is not None
    assert registry.get("main") is handle


@pytest.mark.asyncio
async def test_start_main_status_done_after_completion(
    config: DrClawConfig, bus: MessageBus,
) -> None:
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)

    with _patch_loop_run():
        handle = registry.start_main()
        await handle.task  # type: ignore[misc]

    # Callback fires after await
    await asyncio.sleep(0)
    assert handle.status == AgentStatus.DONE


@pytest.mark.asyncio
async def test_start_main_idempotent(config: DrClawConfig, bus: MessageBus) -> None:
    """Calling start_main twice while RUNNING returns the same handle."""
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)

    with _patch_loop_run():
        h1 = registry.start_main()
        h2 = registry.start_main()

    assert h1 is h2


# ---------------------------------------------------------------------------
# spawn_project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_project(config: DrClawConfig, bus: MessageBus) -> None:
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    project = make_project()

    with _patch_loop_run():
        handle = registry.spawn_project(project)

    assert handle.agent_id == f"proj:{project.id}"
    assert handle.agent_type == "project_manager"
    assert handle.label == project.name
    assert handle.project is project
    assert registry.get(f"proj:{project.id}") is handle


@pytest.mark.asyncio
async def test_spawn_duplicate_running(config: DrClawConfig, bus: MessageBus) -> None:
    """Spawning the same project twice while RUNNING returns the existing handle."""
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    project = make_project()

    with _patch_loop_run():
        h1 = registry.spawn_project(project)
        h2 = registry.spawn_project(project)

    assert h1 is h2


@pytest.mark.asyncio
async def test_spawn_after_done(config: DrClawConfig, bus: MessageBus) -> None:
    """A DONE handle gets replaced with a fresh agent on re-spawn."""
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    project = make_project()

    with _patch_loop_run():
        h1 = registry.spawn_project(project)
        await h1.task  # type: ignore[misc]
        await asyncio.sleep(0)
        assert h1.status == AgentStatus.DONE

        h2 = registry.spawn_project(project)

    assert h2 is not h1
    assert h2.status == AgentStatus.RUNNING


# ---------------------------------------------------------------------------
# spawn_all_projects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_all_projects(config: DrClawConfig, bus: MessageBus) -> None:
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)

    store = JsonProjectStore(config.data_path)
    store.create_project("Alpha", "first")
    store.create_project("Beta", "second")

    with _patch_loop_run():
        handles = registry.spawn_all_projects(store)

    assert len(handles) == 2
    labels = {h.label for h in handles}
    assert labels == {"Alpha", "Beta"}
    assert len(registry.list_agents()) == 2


@pytest.mark.asyncio
async def test_spawn_activated_projects_no_state_starts_none(
    config: DrClawConfig, bus: MessageBus,
) -> None:
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)

    store = JsonProjectStore(config.data_path)
    store.create_project("Alpha", "first")
    store.create_project("Beta", "second")

    with _patch_loop_run():
        handles = registry.spawn_activated_projects(store)

    assert handles == []
    assert len(registry.list_agents()) == 0


@pytest.mark.asyncio
async def test_spawn_activated_projects_restores_persisted_state(
    config: DrClawConfig, bus: MessageBus,
) -> None:
    provider = MockProvider()
    store = JsonProjectStore(config.data_path)
    alpha = store.create_project("Alpha", "first")
    _ = store.create_project("Beta", "second")

    registry_1 = AgentRegistry(config, provider, bus)
    with _patch_loop_run():
        registry_1.spawn_project(alpha)

    registry_2 = AgentRegistry(config, provider, MessageBus())
    with _patch_loop_run():
        handles = registry_2.spawn_activated_projects(store)

    assert len(handles) == 1
    assert handles[0].agent_id == f"proj:{alpha.id}"


@pytest.mark.asyncio
async def test_stop_project_deactivates_persisted_state(
    config: DrClawConfig, bus: MessageBus,
) -> None:
    provider = MockProvider()
    store = JsonProjectStore(config.data_path)
    alpha = store.create_project("Alpha", "first")

    registry_1 = AgentRegistry(config, provider, bus)

    async def _hang(self):  # noqa: ANN001
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    with patch("drclaw.agent.loop.AgentLoop.run", _hang):
        handle = registry_1.spawn_project(alpha)
        assert handle.status == AgentStatus.RUNNING
        stopped = await registry_1.stop(handle.agent_id)
    assert stopped is True

    registry_2 = AgentRegistry(config, provider, MessageBus())
    with _patch_loop_run():
        handles = registry_2.spawn_activated_projects(store)

    assert handles == []


@pytest.mark.asyncio
async def test_stop_all_preserves_activation_state_by_default(
    config: DrClawConfig, bus: MessageBus,
) -> None:
    provider = MockProvider()
    store = JsonProjectStore(config.data_path)
    alpha = store.create_project("Alpha", "first")

    registry_1 = AgentRegistry(config, provider, bus)

    async def _hang(self):  # noqa: ANN001
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    with patch("drclaw.agent.loop.AgentLoop.run", _hang):
        _ = registry_1.spawn_project(alpha)
        await registry_1.stop_all()

    registry_2 = AgentRegistry(config, provider, MessageBus())
    with _patch_loop_run():
        handles = registry_2.spawn_activated_projects(store)

    assert len(handles) == 1
    assert handles[0].agent_id == f"proj:{alpha.id}"


# ---------------------------------------------------------------------------
# list_agents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_agents(config: DrClawConfig, bus: MessageBus) -> None:
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    project = make_project()

    with _patch_loop_run():
        registry.start_main()
        registry.spawn_project(project)

    agents = registry.list_agents()
    assert len(agents) == 2
    ids = {a.agent_id for a in agents}
    assert ids == {"main", f"proj:{project.id}"}


# ---------------------------------------------------------------------------
# bus subscription
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bus_topics_created(config: DrClawConfig, bus: MessageBus) -> None:
    """Registry subscribes bus topics for each agent it starts."""
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    project = make_project()

    with _patch_loop_run():
        registry.start_main()
        registry.spawn_project(project)

    assert "main" in bus._topics
    assert f"proj:{project.id}" in bus._topics


@pytest.mark.asyncio
async def test_spawn_project_also_starts_enabled_students(
    config: DrClawConfig, bus: MessageBus,
) -> None:
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    project = make_project()
    project.student_agents = [
        StudentAgentConfig(id="researcher", label="Researcher"),
        StudentAgentConfig(id="disabled", label="Disabled", enabled=False),
    ]

    with _patch_loop_run():
        registry.spawn_project(project)

    agents = {handle.agent_id: handle for handle in registry.list_agents()}
    assert f"proj:{project.id}" in agents
    assert f"student:{project.id}:researcher" in agents
    assert f"student:{project.id}:disabled" not in agents
    assert agents[f"student:{project.id}:researcher"].agent_type == "project_student"


# ---------------------------------------------------------------------------
# on_tool_call propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_tool_call_propagated(config: DrClawConfig, bus: MessageBus) -> None:
    provider = MockProvider()
    calls: list[tuple[str, dict]] = []

    def recorder(name: str, args: dict) -> None:
        calls.append((name, args))

    registry = AgentRegistry(config, provider, bus, on_tool_call=recorder)

    with _patch_loop_run():
        handle = registry.start_main()

    assert handle.loop.on_tool_call is recorder


# ---------------------------------------------------------------------------
# interactive vs background iteration cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interactive_vs_background_iteration_cap(
    config: DrClawConfig, bus: MessageBus,
) -> None:
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    project = make_project()

    with _patch_loop_run():
        bg = registry.spawn_project(project, interactive=False)

    assert bg.loop.max_iterations == 15

    # Force DONE so re-spawn works
    bg.status = AgentStatus.DONE

    with _patch_loop_run():
        fg = registry.spawn_project(project, interactive=True)

    assert fg.loop.max_iterations == config.agent.max_iterations


# ---------------------------------------------------------------------------
# task error → ERROR status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_error_updates_status(config: DrClawConfig, bus: MessageBus) -> None:
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)

    async def _explode(self):  # noqa: ANN001
        raise RuntimeError("boom")

    with patch("drclaw.agent.loop.AgentLoop.run", _explode):
        handle = registry.spawn_project(make_project())
        with pytest.raises(RuntimeError):
            await handle.task  # type: ignore[misc]

    await asyncio.sleep(0)
    assert handle.status == AgentStatus.ERROR


# ---------------------------------------------------------------------------
# stop / stop_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_running_agent(config: DrClawConfig, bus: MessageBus) -> None:
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)

    async def _hang(self):  # noqa: ANN001
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    with patch("drclaw.agent.loop.AgentLoop.run", _hang):
        handle = registry.spawn_project(make_project())
        assert handle.status == AgentStatus.RUNNING

        result = await registry.stop(handle.agent_id)

    assert result is True
    assert handle.status == AgentStatus.DONE


@pytest.mark.asyncio
async def test_stop_nonexistent_returns_false(
    config: DrClawConfig, bus: MessageBus,
) -> None:
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    assert await registry.stop("no-such-agent") is False


@pytest.mark.asyncio
async def test_stop_all(config: DrClawConfig, bus: MessageBus) -> None:
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)

    async def _hang(self):  # noqa: ANN001
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    with patch("drclaw.agent.loop.AgentLoop.run", _hang):
        registry.start_main()
        registry.spawn_project(make_project("p1", "Alpha"))
        registry.spawn_project(make_project("p2", "Beta"))

        await registry.stop_all()

    for h in registry.list_agents():
        assert h.status == AgentStatus.DONE


@pytest.mark.asyncio
async def test_remove_project_agent_unregisters_handle(
    config: DrClawConfig, bus: MessageBus,
) -> None:
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    project = make_project("p1", "Alpha")

    async def _hang(self):  # noqa: ANN001
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    with patch("drclaw.agent.loop.AgentLoop.run", _hang):
        registry.spawn_project(project, interactive=True)
        removed = await registry.remove_project_agent(project.id)

    assert removed is True
    assert registry.get(f"proj:{project.id}") is None


@pytest.mark.asyncio
async def test_main_remove_tool_uses_registry_callback(
    config: DrClawConfig, bus: MessageBus,
) -> None:
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    store = JsonProjectStore(config.data_path)
    project = store.create_project("DeleteMe")
    project_dir = config.data_path / "projects" / project.id

    with _patch_loop_run():
        main_handle = registry.start_main()
        registry.spawn_project(project, interactive=True)

    tool = main_handle.loop.tool_registry.get("remove_project")
    assert tool is not None

    result = await tool.execute({"project_id": project.id})

    assert "Removed project" in result
    assert registry.get(f"proj:{project.id}") is None
    assert store.get_project(project.id) is None
    assert not project_dir.exists()


# ---------------------------------------------------------------------------
# find_by_project_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_by_project_name(config: DrClawConfig, bus: MessageBus) -> None:
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    project = make_project("abc123", "My Report")

    with _patch_loop_run():
        handle = registry.spawn_project(project)

    found = registry.find_by_project_name("my report")
    assert found is handle


@pytest.mark.asyncio
async def test_find_by_project_name_not_found(
    config: DrClawConfig, bus: MessageBus,
) -> None:
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    assert registry.find_by_project_name("nope") is None
