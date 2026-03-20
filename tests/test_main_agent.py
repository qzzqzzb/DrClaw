"""Tests for the MainAgent orchestrator."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from drclaw.agent.main_agent import MainAgent
from drclaw.agent.skills import SkillsLoader
from drclaw.bus.queue import MessageBus
from drclaw.config.schema import DrClawConfig
from drclaw.models.messages import InboundMessage
from drclaw.soul import main_soul_path
from tests.mocks import MockProvider, make_text_response, make_tool_response


@pytest.fixture
def config(tmp_path: Path) -> DrClawConfig:
    return DrClawConfig(data_dir=str(tmp_path / "drclaw_data"))


@pytest.fixture
def main_agent(config: DrClawConfig, mock_provider: MockProvider) -> MainAgent:
    return MainAgent(config, mock_provider)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_main_agent_direct_response(
    main_agent: MainAgent, mock_provider: MockProvider
) -> None:
    """MockProvider returns text only — relayed as-is."""
    mock_provider.queue(make_text_response("Hello from main agent!"))
    result = await main_agent.process_direct("hi")
    assert result == "Hello from main agent!"


def test_main_agent_does_not_register_remote_tmux_tools(main_agent: MainAgent) -> None:
    names = set(main_agent.loop.tool_registry.tool_names)
    assert "start_remote_tmux_session" not in names
    assert "get_remote_tmux_session_status" not in names
    assert "list_remote_tmux_sessions" not in names
    assert "terminate_remote_tmux_session" not in names


def test_main_agent_registers_project_student_management_tools(main_agent: MainAgent) -> None:
    names = set(main_agent.loop.tool_registry.tool_names)
    assert "list_project_students" in names
    assert "create_project_student" in names
    assert "update_project_student" in names
    assert "remove_project_student" in names
    assert "add_local_hub_skills_to_project_student" in names


@pytest.mark.asyncio
async def test_main_agent_web_dispatch_applies_zh_language_directive(
    main_agent: MainAgent, mock_provider: MockProvider
) -> None:
    bus = MessageBus()
    main_agent.loop.bus = bus
    mock_provider.queue(make_text_response("ok"))

    await main_agent.loop._dispatch(
        InboundMessage(
            channel="web",
            chat_id="web-1",
            text="hello",
            metadata={"webui_language": "zh"},
        )
    )

    system_prompt = str(mock_provider.calls[0]["messages"][0]["content"])
    assert "Organize your response in Chinese." in system_prompt
    assert "professional terms in their original language" in system_prompt


@pytest.mark.asyncio
async def test_main_agent_non_web_dispatch_does_not_apply_language_directive(
    main_agent: MainAgent, mock_provider: MockProvider
) -> None:
    bus = MessageBus()
    main_agent.loop.bus = bus
    mock_provider.queue(make_text_response("ok"))

    await main_agent.loop._dispatch(
        InboundMessage(
            channel="cli",
            chat_id="cli-1",
            text="hello",
            metadata={"webui_language": "zh"},
        )
    )

    system_prompt = str(mock_provider.calls[0]["messages"][0]["content"])
    assert "## Response Language" not in system_prompt


@pytest.mark.asyncio
async def test_main_agent_list_projects(main_agent: MainAgent, mock_provider: MockProvider) -> None:
    """Tool call sequence: list_projects → text summary."""
    main_agent.project_store.create_project("Alpha", "first")
    main_agent.project_store.create_project("Beta", "second")

    mock_provider.queue(
        make_tool_response("list_projects", {}),
        make_text_response("You have 2 projects: Alpha and Beta."),
    )
    result = await main_agent.process_direct("show my projects")
    assert "Alpha" in result
    assert "Beta" in result
    assert len(mock_provider.calls) == 2


@pytest.mark.asyncio
async def test_main_agent_create_project(
    main_agent: MainAgent, mock_provider: MockProvider
) -> None:
    """Tool call creates project in store, then text confirms."""
    mock_provider.queue(
        make_tool_response(
            "create_project", {"name": "Quantum", "description": "quantum research"}
        ),
        make_text_response("Created project Quantum."),
    )
    result = await main_agent.process_direct("create a project called Quantum")
    assert "Quantum" in result

    projects = main_agent.project_store.list_projects()
    assert len(projects) == 1
    assert projects[0].name == "Quantum"


@pytest.mark.asyncio
async def test_main_agent_create_project_with_soul_append(
    main_agent: MainAgent, mock_provider: MockProvider
) -> None:
    mock_provider.queue(
        make_tool_response(
            "create_project",
            {
                "name": "PersonaQuantum",
                "description": "quantum research",
                "soul_append": "## Project Direction\n- Prioritize reproducibility.",
            },
        ),
        make_text_response("Created project PersonaQuantum."),
    )
    result = await main_agent.process_direct("create a project called PersonaQuantum")
    assert "PersonaQuantum" in result

    projects = main_agent.project_store.list_projects()
    assert len(projects) == 1
    project = projects[0]
    soul_file = main_agent.config.data_path / "projects" / project.id / "workspace" / "SOUL.md"
    soul = soul_file.read_text(encoding="utf-8")
    assert "## Project Direction" in soul
    assert "Prioritize reproducibility." in soul


@pytest.mark.asyncio
async def test_main_agent_create_project_calls_on_create_callback(
    config: DrClawConfig, mock_provider: MockProvider,
) -> None:
    created = []
    agent = MainAgent(
        config,
        mock_provider,
        on_project_create=lambda project: created.append(project),
    )
    mock_provider.queue(
        make_tool_response(
            "create_project", {"name": "Nova", "description": "callback test"}
        ),
        make_text_response("Created project Nova."),
    )
    result = await agent.process_direct("create project Nova")
    assert "Nova" in result
    assert len(created) == 1
    assert created[0].name == "Nova"


@pytest.mark.asyncio
async def test_main_agent_cron_add_job(
    main_agent: MainAgent, mock_provider: MockProvider
) -> None:
    """Main agent can create cron jobs via the native cron tool."""
    mock_provider.queue(
        make_tool_response(
            "cron",
            {
                "action": "add",
                "message": "Report yesterday's progress",
                "cron_expr": "0 8 * * *",
                "tz": "Asia/Shanghai",
            },
        ),
        make_text_response("Scheduled."),
    )
    result = await main_agent.process_direct("Schedule daily report at 8:00")
    assert "Scheduled" in result

    jobs = main_agent.cron_service.list_jobs(include_disabled=True)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.payload.target == "main"
    assert job.payload.message == "Report yesterday's progress"
    assert job.schedule.kind == "cron"
    assert job.schedule.expr == "0 8 * * *"


@pytest.mark.asyncio
async def test_main_agent_cron_add_job_for_project_target(
    main_agent: MainAgent, mock_provider: MockProvider
) -> None:
    project = main_agent.project_store.create_project("Alpha")
    mock_provider.queue(
        make_tool_response(
            "cron",
            {
                "action": "add",
                "target": "Alpha",
                "message": "Summarize blockers",
                "every_seconds": 600,
            },
        ),
        make_text_response("Scheduled for project."),
    )
    result = await main_agent.process_direct("Schedule Alpha updates every 10 minutes")
    assert "Scheduled for project" in result
    jobs = main_agent.cron_service.list_jobs(include_disabled=True)
    assert len(jobs) == 1
    assert jobs[0].payload.target == f"proj:{project.id}"


@pytest.mark.asyncio
async def test_main_agent_remove_project(
    main_agent: MainAgent, mock_provider: MockProvider
) -> None:
    """Tool call removes project from registry and deletes its workspace directory."""
    project = main_agent.project_store.create_project("TrashMe")
    project_dir = main_agent.config.data_path / "projects" / project.id
    assert project_dir.is_dir()

    mock_provider.queue(
        make_tool_response("remove_project", {"project_id": project.id}),
        make_text_response("Project removed."),
    )
    result = await main_agent.process_direct("remove TrashMe")
    assert "Project removed" in result
    assert main_agent.project_store.get_project(project.id) is None
    assert not project_dir.exists()


@pytest.mark.asyncio
async def test_main_agent_route_to_project(
    main_agent: MainAgent, mock_provider: MockProvider
) -> None:
    """Routing creates ProjectAgent, response relayed back."""
    project = main_agent.project_store.create_project("DeepSea")

    # Mock ProjectAgent since it lives in a parallel branch (DrClaw-f7h).
    # _route_to_project does `from drclaw.agent.project_agent import ProjectAgent`
    # at call time, so we inject a fake module into sys.modules.
    fake_instance = AsyncMock()
    fake_instance.process_direct.return_value = "DeepSea agent says hello"
    mock_cls = MagicMock(return_value=fake_instance)

    fake_module = types.ModuleType("drclaw.agent.project_agent")
    fake_module.ProjectAgent = mock_cls  # type: ignore[attr-defined]

    mock_provider.queue(
        make_tool_response(
            "route_to_project",
            {"project_id": project.id, "message": "research ocean depths"},
        ),
        make_text_response("The DeepSea project agent responded."),
    )

    with patch.dict(sys.modules, {"drclaw.agent.project_agent": fake_module}):
        result = await main_agent.process_direct("ask DeepSea about ocean depths")

    assert "DeepSea project agent responded" in result
    mock_cls.assert_called_once_with(
        main_agent.config, main_agent.provider, project,
        debug_logger=main_agent.loop.debug_logger,
        equipment_manager=main_agent.equipment_manager,
        sandbox_job_manager=main_agent.sandbox_job_manager,
        env_store=main_agent.env_store,
    )
    fake_instance.process_direct.assert_called_once_with("research ocean depths")


# ---------------------------------------------------------------------------
# Filesystem tool integration
# ---------------------------------------------------------------------------


def test_main_agent_has_filesystem_tools(
    config: DrClawConfig, mock_provider: MockProvider
) -> None:
    agent = MainAgent(config, mock_provider)
    names = agent.loop.tool_registry.tool_names
    expected = {
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "exec",
        "web_search",
        "web_fetch",
        "message",
        "list_projects",
        "list_project_students",
        "create_project",
        "create_project_student",
        "update_project_student",
        "remove_project",
        "remove_project_student",
        "route_to_project",
        "set_env_var",
        "unset_env_var",
        "list_env_vars",
        "list_env_scopes",
        "add_equipment",
        "list_equipments",
        "remove_equipment",
        "list_local_skill_hub_skills",
        "import_skill_to_local_hub",
        "add_local_hub_skills_to_equipment",
        "add_local_hub_skills_to_project",
        "add_local_hub_skills_to_project_student",
        "list_local_skill_hub_categories",
        "set_local_skill_hub_category_metadata",
        "use_equipment",
        "list_active_equipment_runs",
        "get_equipment_run_status",
        "list_background_tool_tasks",
        "get_background_tool_task_status",
        "cancel_background_tool_task",
        "cron",
    }
    assert set(names) == expected


@pytest.mark.asyncio
async def test_main_agent_write_sandboxed_to_data_dir(
    config: DrClawConfig, mock_provider: MockProvider, tmp_path: Path
) -> None:
    agent = MainAgent(config, mock_provider)
    tool = agent.loop.tool_registry.get("write_file")
    assert tool is not None
    outside = tmp_path / "evil.txt"
    result = await tool.execute({"path": str(outside), "content": "bad"})
    assert result.startswith("Error: ")
    assert not outside.exists()


@pytest.mark.asyncio
async def test_main_agent_read_unrestricted(
    config: DrClawConfig, mock_provider: MockProvider, tmp_path: Path
) -> None:
    agent = MainAgent(config, mock_provider)
    outside = tmp_path / "readable.txt"
    outside.write_text("hello", encoding="utf-8")
    tool = agent.loop.tool_registry.get("read_file")
    assert tool is not None
    result = await tool.execute({"path": str(outside)})
    assert result == "hello"


# ---------------------------------------------------------------------------
# Skills integration
# ---------------------------------------------------------------------------


def test_identity_includes_project_summary(
    config: DrClawConfig, mock_provider: MockProvider
) -> None:
    agent = MainAgent(config, mock_provider)
    agent.project_store.create_project("Alpha", "first project")
    agent.project_store.create_project("Beta", "second project")

    prompt = agent.loop.context_builder.build_system_prompt()
    assert "Active Students" in prompt
    assert "Alpha" in prompt
    assert "first project" in prompt
    assert "Beta" in prompt
    assert "second project" in prompt


def test_identity_no_projects(
    config: DrClawConfig, mock_provider: MockProvider
) -> None:
    agent = MainAgent(config, mock_provider)
    prompt = agent.loop.context_builder.build_system_prompt()
    assert "(No projects yet.)" in prompt


def test_main_agent_has_skills_loader(
    config: DrClawConfig, mock_provider: MockProvider
) -> None:
    """MainAgent wires a SkillsLoader into its ContextBuilder."""
    agent = MainAgent(config, mock_provider)
    assert agent.loop.context_builder.skills_loader is not None
    assert isinstance(agent.loop.context_builder.skills_loader, SkillsLoader)


def test_main_agent_creates_default_skill_dirs(
    config: DrClawConfig, mock_provider: MockProvider
) -> None:
    MainAgent(config, mock_provider)
    assert (config.data_path / "skills").is_dir()
    assert (config.data_path / "local-skill-hub").is_dir()


def test_main_agent_creates_soul_file(
    config: DrClawConfig, mock_provider: MockProvider
) -> None:
    agent = MainAgent(config, mock_provider)
    prompt = agent.loop.context_builder.build_system_prompt()
    soul_file = main_soul_path(config.data_path)
    assert soul_file.is_file()
    assert "Identity & Persona" in soul_file.read_text(encoding="utf-8")
    assert "Be 虾秘, the main DrClaw assistant." in prompt
    assert "default to uv" in prompt
    assert "seek help from the user or caller" in prompt


@pytest.mark.asyncio
async def test_main_agent_uses_custom_soul_file(
    config: DrClawConfig, mock_provider: MockProvider
) -> None:
    soul_file = main_soul_path(config.data_path)
    soul_file.parent.mkdir(parents=True, exist_ok=True)
    soul_file.write_text("# Identity & Persona\n- Be Custom Alice.\n", encoding="utf-8")

    agent = MainAgent(config, mock_provider)
    mock_provider.queue(make_text_response("ok"))
    await agent.process_direct("hello")

    system_msg = mock_provider.calls[0]["messages"][0]["content"]
    assert "Be Custom Alice." in system_msg


@pytest.mark.asyncio
async def test_main_agent_always_skill_in_prompt(
    config: DrClawConfig, mock_provider: MockProvider
) -> None:
    """Seeded global memory skill (always=true) appears in the system prompt."""
    agent = MainAgent(config, mock_provider)
    mock_provider.queue(make_text_response("ok"))
    await agent.process_direct("hi")

    system_msg = mock_provider.calls[0]["messages"][0]["content"]
    assert "Active Skills" in system_msg
    assert "memory" in system_msg.lower()


# ---------------------------------------------------------------------------
# Bus-based routing (Phase 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_with_bus_publishes_to_topic(
    config: DrClawConfig, mock_provider: MockProvider
) -> None:
    """When bus is set, route_to_project publishes to the project's topic."""
    agent = MainAgent(config, mock_provider)
    bus = MessageBus()
    agent.loop.bus = bus

    project = agent.project_store.create_project("DeepSea")
    topic = f"proj:{project.id}"
    bus.subscribe(topic)

    result = await agent._route_to_project(project, "research ocean depths")

    assert "Routed to DeepSea" in result
    # Message should be on the topic queue
    msg = await asyncio.wait_for(bus.consume_inbound(topic), timeout=1.0)
    assert msg.text == "research ocean depths"
    assert msg.source == "main"
    assert msg.channel == "cli"
    assert msg.chat_id == f"proj-{project.id}"
    assert msg.metadata.get("delegated") is True
    assert msg.metadata.get("delegated_by") == "main"
    assert msg.metadata.get("reply_to_topic") == "main"
    assert msg.metadata.get("reply_channel") == "cli"
    assert msg.metadata.get("reply_chat_id") == f"proj-{project.id}"


@pytest.mark.asyncio
async def test_route_with_bus_ensures_project_is_active_before_publish(
    config: DrClawConfig, mock_provider: MockProvider
) -> None:
    ensure_project_active = AsyncMock()
    agent = MainAgent(
        config,
        mock_provider,
        ensure_project_active=ensure_project_active,
    )
    bus = MessageBus()
    agent.loop.bus = bus

    project = agent.project_store.create_project("Cat")
    topic = f"proj:{project.id}"

    result = await agent._route_to_project(project, "hello cat")

    assert "Routed to Cat" in result
    ensure_project_active.assert_awaited_once_with(project)
    msg = await asyncio.wait_for(bus.consume_inbound(topic), timeout=1.0)
    assert msg.text == "hello cat"
    assert msg.source == "main"


@pytest.mark.asyncio
async def test_route_with_bus_forwards_and_stages_attachments_for_project(
    config: DrClawConfig,
    mock_provider: MockProvider,
) -> None:
    agent = MainAgent(config, mock_provider)
    bus = MessageBus()
    agent.loop.bus = bus

    project = agent.project_store.create_project("DeepSea")
    topic = f"proj:{project.id}"
    bus.subscribe(topic)

    source = config.data_path / "web" / "chat_files" / "files" / "sample.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"%PDF-test")

    agent.loop._current_inbound = InboundMessage(
        channel="web",
        chat_id="chat-1",
        text="delegate",
        metadata={
            "webui_language": "en",
            "attachments": [
                {
                    "id": "abc123",
                    "name": "sample.pdf",
                    "mime": "application/pdf",
                    "size": 9,
                    "path": str(source),
                    "download_url": "/api/chat/files/abc123",
                }
            ],
        },
    )
    try:
        result = await agent._route_to_project(project, "process attachment")
    finally:
        agent.loop._current_inbound = None

    assert "Routed to DeepSea" in result
    msg = await asyncio.wait_for(bus.consume_inbound(topic), timeout=1.0)
    assert msg.channel == "web"
    assert msg.chat_id == "chat-1"
    assert msg.metadata.get("webui_language") == "en"
    attachments = msg.metadata.get("attachments")
    assert isinstance(attachments, list)
    assert len(attachments) == 1
    staged = attachments[0]
    assert staged["id"] == "abc123"
    assert staged["name"] == "sample.pdf"
    staged_path = Path(staged["path"])
    assert staged_path.is_file()
    assert staged_path.read_bytes() == b"%PDF-test"
    assert staged_path.is_relative_to(
        (config.data_path / "projects" / project.id / "workspace" / "incoming_files").resolve()
    )


@pytest.mark.asyncio
async def test_route_without_bus_falls_back(
    main_agent: MainAgent, mock_provider: MockProvider
) -> None:
    """When bus is None, route_to_project falls back to synchronous execution."""
    assert main_agent.loop.bus is None

    project = main_agent.project_store.create_project("Sync")

    fake_instance = AsyncMock()
    fake_instance.process_direct.return_value = "sync response"
    mock_cls = MagicMock(return_value=fake_instance)

    fake_module = types.ModuleType("drclaw.agent.project_agent")
    fake_module.ProjectAgent = mock_cls  # type: ignore[attr-defined]

    with patch.dict(sys.modules, {"drclaw.agent.project_agent": fake_module}):
        result = await main_agent._route_to_project(project, "hello sync")

    assert result == "sync response"
    fake_instance.process_direct.assert_called_once_with("hello sync")


@pytest.mark.asyncio
async def test_route_via_tool_publishes_to_bus(
    config: DrClawConfig, mock_provider: MockProvider
) -> None:
    """End-to-end: LLM calls route_to_project tool, message lands on bus topic."""
    agent = MainAgent(config, mock_provider)
    bus = MessageBus()
    agent.loop.bus = bus

    project = agent.project_store.create_project("BusTest")
    topic = f"proj:{project.id}"
    bus.subscribe(topic)

    mock_provider.queue(
        make_tool_response(
            "route_to_project",
            {"project_id": project.id, "message": "do research"},
        ),
        make_text_response("Routed successfully."),
    )

    result = await agent.process_direct("ask BusTest to do research")
    assert "Routed" in result

    msg = await asyncio.wait_for(bus.consume_inbound(topic), timeout=1.0)
    assert msg.text == "do research"
    assert msg.metadata.get("delegated") is True
    assert msg.metadata.get("reply_to_topic") == "main"
