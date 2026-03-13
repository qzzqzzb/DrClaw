"""Tests for the ProjectAgent orchestrator."""

from __future__ import annotations

from pathlib import Path

import pytest

from drclaw.agent.project_agent import ProjectAgent
from drclaw.agent.skills import SkillsLoader
from drclaw.config.schema import DrClawConfig
from drclaw.models.project import Project
from drclaw.soul import project_soul_path
from tests.mocks import MockProvider, make_project, make_text_response, make_tool_response


@pytest.fixture
def project() -> Project:
    return make_project()


@pytest.fixture
def agent(tmp_path: Path, mock_provider: MockProvider, project: Project) -> ProjectAgent:
    """Build a ProjectAgent rooted in a temp directory."""
    config = DrClawConfig(data_dir=str(tmp_path))
    return ProjectAgent(config, mock_provider, project)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_project_agent_does_not_register_message_tool(agent: ProjectAgent) -> None:
    assert "message" not in agent.loop.tool_registry.tool_names


def test_project_agent_claude_code_tools_disabled_by_default(
    tmp_path: Path, mock_provider: MockProvider, project: Project
) -> None:
    class _DummyClaudeCodeManager:
        pass

    config = DrClawConfig(data_dir=str(tmp_path))
    agent = ProjectAgent(
        config,
        mock_provider,
        project,
        claude_code_manager=_DummyClaudeCodeManager(),  # type: ignore[arg-type]
    )
    names = set(agent.loop.tool_registry.tool_names)
    assert "use_claude_code" not in names
    assert "send_claude_code_message" not in names
    assert "get_claude_code_status" not in names
    assert "list_claude_code_sessions" not in names
    assert "close_claude_code_session" not in names


def test_project_agent_claude_code_tools_can_be_reenabled(
    tmp_path: Path, mock_provider: MockProvider, project: Project
) -> None:
    class _DummyClaudeCodeManager:
        pass

    config = DrClawConfig(data_dir=str(tmp_path))
    config.claude_code.enabled = True
    agent = ProjectAgent(
        config,
        mock_provider,
        project,
        claude_code_manager=_DummyClaudeCodeManager(),  # type: ignore[arg-type]
    )
    names = set(agent.loop.tool_registry.tool_names)
    assert "use_claude_code" in names
    assert "send_claude_code_message" in names
    assert "get_claude_code_status" in names
    assert "list_claude_code_sessions" in names
    assert "close_claude_code_session" in names


@pytest.mark.asyncio
async def test_project_agent_conversation(agent: ProjectAgent, mock_provider: MockProvider) -> None:
    """MockProvider returns text → ProjectAgent returns it."""
    mock_provider.queue(make_text_response("Research findings: AI is cool."))
    result = await agent.process_direct("Tell me about AI")
    assert result == "Research findings: AI is cool."


@pytest.mark.asyncio
async def test_project_agent_file_ops(
    agent: ProjectAgent, mock_provider: MockProvider, tmp_path: Path, project: Project
) -> None:
    """MockProvider calls write_file then read_file → both execute in workspace."""
    mock_provider.queue(
        make_tool_response("write_file", {"path": "notes.txt", "content": "hello"}, "tc1"),
        make_tool_response("read_file", {"path": "notes.txt"}, "tc2"),
        make_text_response("File contains: hello"),
    )
    result = await agent.process_direct("Write and read a file")
    assert "hello" in result.lower() or "file" in result.lower()

    # Verify file was actually created in the workspace
    workspace = tmp_path / "projects" / project.id / "workspace"
    assert (workspace / "notes.txt").read_text() == "hello"


@pytest.mark.asyncio
async def test_project_agent_workspace_isolation(
    agent: ProjectAgent, mock_provider: MockProvider, tmp_path: Path
) -> None:
    """Write outside workspace is blocked; read outside is allowed (for skill access)."""
    outside_file = tmp_path / "evil.txt"

    mock_provider.queue(
        make_tool_response("write_file", {"path": str(outside_file), "content": "bad"}, "tc1"),
        make_text_response("Could not write the file."),
    )
    await agent.process_direct("Write to evil file")

    # Provider called twice: tool error triggered a follow-up LLM call.
    assert len(mock_provider.calls) == 2
    # The tool result in the second call's messages must contain the sandbox error.
    messages = mock_provider.calls[1]["messages"]
    tool_results = [m for m in messages if m.get("role") == "tool"]
    assert any("Error: " in m.get("content", "") for m in tool_results)
    assert not outside_file.exists()


@pytest.mark.asyncio
async def test_project_agent_session_key(
    agent: ProjectAgent, mock_provider: MockProvider, project: Project
) -> None:
    """Session key follows the 'proj:{id}' convention."""
    mock_provider.queue(make_text_response("ok"))
    await agent.process_direct("hello")

    expected_key = f"proj:{project.id}"
    session = agent.loop.session_manager.load(expected_key)
    assert len(session.messages) > 0
    assert session.session_key == expected_key


@pytest.mark.asyncio
async def test_project_agent_memory_location(
    agent: ProjectAgent, tmp_path: Path, project: Project
) -> None:
    """MEMORY.md lives at project_dir, not workspace_dir."""
    project_dir = tmp_path / "projects" / project.id
    workspace_dir = project_dir / "workspace"

    # Memory store should point to project_dir
    assert agent.memory_store.memory_file == project_dir / "MEMORY.md"
    assert agent.memory_store.history_file == project_dir / "HISTORY.md"

    # Write some memory and verify it lands in the right place
    agent.memory_store.write_long_term("Test memory content")
    assert (project_dir / "MEMORY.md").read_text() == "Test memory content"
    assert not (workspace_dir / "MEMORY.md").exists()


# ---------------------------------------------------------------------------
# Skills integration
# ---------------------------------------------------------------------------


def test_project_agent_has_skills_loader(agent: ProjectAgent) -> None:
    """ProjectAgent wires a SkillsLoader into its ContextBuilder."""
    assert agent.loop.context_builder.skills_loader is not None
    assert isinstance(agent.loop.context_builder.skills_loader, SkillsLoader)


def test_project_agent_creates_soul_file(
    tmp_path: Path, mock_provider: MockProvider, project: Project
) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    _ = ProjectAgent(config, mock_provider, project)
    soul_file = project_soul_path(tmp_path / "projects" / project.id / "workspace")
    assert soul_file.is_file()
    assert "Identity & Persona" in soul_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_project_agent_no_skills_no_crash(
    agent: ProjectAgent, mock_provider: MockProvider
) -> None:
    """No skill directories exist → process_direct still works fine."""
    mock_provider.queue(make_text_response("all good"))
    result = await agent.process_direct("hello")
    assert result == "all good"


@pytest.mark.asyncio
async def test_project_metadata_in_identity(
    tmp_path: Path, mock_provider: MockProvider
) -> None:
    """Project with description/goals/tags → all appear in the system prompt."""
    config = DrClawConfig(data_dir=str(tmp_path))
    project = make_project()
    project.description = "Studying LLM alignment"
    project.goals = "Produce a survey paper"
    project.tags = ["ai-safety", "alignment"]

    agent = ProjectAgent(config, mock_provider, project)
    mock_provider.queue(make_text_response("ok"))
    await agent.process_direct("hi")

    system_msg = mock_provider.calls[0]["messages"][0]["content"]
    assert "Studying LLM alignment" in system_msg
    assert "Produce a survey paper" in system_msg
    assert "ai-safety" in system_msg
    assert "alignment" in system_msg
    assert "Equipment call policy" in system_msg
    assert "await_result=true" in system_msg
    assert "await_result=false" in system_msg
    assert "list_active_equipment_runs/get_equipment_run_status" in system_msg
    assert "do not start a duplicate equipment run" in system_msg
    assert "unrecoverable external dependency error" in system_msg


@pytest.mark.asyncio
async def test_generic_identity_when_no_metadata(
    tmp_path: Path, mock_provider: MockProvider
) -> None:
    """Empty metadata → clean identity with no Description/Goals/Tags lines."""
    config = DrClawConfig(data_dir=str(tmp_path))
    project = make_project()

    agent = ProjectAgent(config, mock_provider, project)
    mock_provider.queue(make_text_response("ok"))
    await agent.process_direct("hi")

    system_msg = mock_provider.calls[0]["messages"][0]["content"]
    assert f"project '{project.name}'" in system_msg
    assert "Description:" not in system_msg
    assert "Goals:" not in system_msg
    assert "Tags:" not in system_msg


@pytest.mark.asyncio
async def test_project_agent_always_skill_in_prompt(
    tmp_path: Path, mock_provider: MockProvider
) -> None:
    """An always=true workspace skill appears in the system prompt."""
    config = DrClawConfig(data_dir=str(tmp_path))
    project = make_project()

    # Create a workspace skill with always: true
    workspace = tmp_path / "projects" / project.id / "workspace"
    skill_dir = workspace / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: A test skill\nalways: true\n---\n\n"
        "# Test Skill\n\nThis is injected content."
    )

    agent = ProjectAgent(config, mock_provider, project)
    mock_provider.queue(make_text_response("ok"))
    await agent.process_direct("hi")

    system_msg = mock_provider.calls[0]["messages"][0]["content"]
    assert "Active Skills" in system_msg
    assert "test-skill" in system_msg
    assert "This is injected content" in system_msg


@pytest.mark.asyncio
async def test_project_agent_uses_custom_soul_file(
    tmp_path: Path, mock_provider: MockProvider
) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    project = make_project()
    workspace = tmp_path / "projects" / project.id / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    soul_file = project_soul_path(workspace)
    soul_file.write_text("# Identity & Persona\n- Be Custom Student.\n", encoding="utf-8")

    agent = ProjectAgent(config, mock_provider, project)
    mock_provider.queue(make_text_response("ok"))
    await agent.process_direct("hi")

    system_msg = mock_provider.calls[0]["messages"][0]["content"]
    assert "Be Custom Student." in system_msg
