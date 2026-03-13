"""End-to-end integration tests — real agents, real filesystem, MockProvider only."""

from __future__ import annotations

from pathlib import Path

import pytest

from drclaw.agent.main_agent import MainAgent
from drclaw.agent.project_agent import ProjectAgent
from drclaw.config.schema import AgentConfig, DrClawConfig
from tests.mocks import MockProvider, make_project, make_text_response, make_tool_response


@pytest.fixture
def config(tmp_path: Path) -> DrClawConfig:
    return DrClawConfig(data_dir=str(tmp_path / "data"))


@pytest.mark.asyncio
async def test_e2e_main_agent_chat(config: DrClawConfig, mock_provider: MockProvider) -> None:
    """MainAgent returns text from provider without tool calls."""
    agent = MainAgent(config, mock_provider)
    mock_provider.queue(make_text_response("Hello, I'm DrClaw!"))
    result = await agent.process_direct("hello")
    assert result == "Hello, I'm DrClaw!"


@pytest.mark.asyncio
async def test_e2e_project_creation(config: DrClawConfig, mock_provider: MockProvider) -> None:
    """create_project tool call → project in store, dirs on disk."""
    agent = MainAgent(config, mock_provider)

    mock_provider.queue(
        make_tool_response("create_project", {"name": "ML Research", "description": "ML work"}),
        make_text_response("Created project ML Research."),
    )
    result = await agent.process_direct("create project ML Research")
    assert "ML Research" in result

    projects = agent.project_store.list_projects()
    assert len(projects) == 1
    assert projects[0].name == "ML Research"

    project_dir = config.data_path / "projects" / projects[0].id
    assert (project_dir / "workspace").is_dir()
    assert (project_dir / "sessions").is_dir()


@pytest.mark.asyncio
async def test_e2e_routing(config: DrClawConfig, mock_provider: MockProvider) -> None:
    """route_to_project → real ProjectAgent → 3 provider calls total."""
    agent = MainAgent(config, mock_provider)
    project = agent.project_store.create_project("ML Research")

    mock_provider.queue(
        make_tool_response(
            "route_to_project",
            {"project_id": project.id, "message": "analyze the dataset"},
        ),
        make_text_response("Dataset has 1000 rows."),
        make_text_response("The ML project reports 1000 rows."),
    )

    result = await agent.process_direct("ask ML Research to analyze the dataset")
    assert result == "The ML project reports 1000 rows."
    assert len(mock_provider.calls) == 3


@pytest.mark.asyncio
async def test_e2e_direct_project_chat(config: DrClawConfig, mock_provider: MockProvider) -> None:
    """ProjectAgent constructed directly, returns text."""
    agent = ProjectAgent(config, mock_provider, make_project())
    mock_provider.queue(make_text_response("Analysis complete: 42 insights found."))
    result = await agent.process_direct("analyze data")
    assert result == "Analysis complete: 42 insights found."


@pytest.mark.asyncio
async def test_e2e_workspace_isolation(
    config: DrClawConfig, mock_provider: MockProvider, tmp_path: Path
) -> None:
    """write_file with absolute path outside workspace yields Error in tool result."""
    agent = ProjectAgent(config, mock_provider, make_project())

    outside = tmp_path / "evil.txt"

    mock_provider.queue(
        make_tool_response("write_file", {"path": str(outside), "content": "bad"}),
        make_text_response("Could not write the file."),
    )
    await agent.process_direct("write to evil file")

    assert len(mock_provider.calls) == 2
    messages = mock_provider.calls[1]["messages"]
    tool_results = [m for m in messages if m.get("role") == "tool"]
    assert any("Error: " in m.get("content", "") for m in tool_results)
    assert not outside.exists()


@pytest.mark.asyncio
async def test_e2e_session_persistence(config: DrClawConfig, mock_provider: MockProvider) -> None:
    """Two turns → 4 messages persisted in session JSONL."""
    agent = MainAgent(config, mock_provider)

    mock_provider.queue(make_text_response("First response."))
    await agent.process_direct("first message")

    mock_provider.queue(make_text_response("Second response."))
    await agent.process_direct("second message")

    session = agent.session_manager.load("main")
    assert len(session.messages) == 4
    assert session.messages[0]["role"] == "user"
    assert session.messages[0]["content"] == "first message"
    assert session.messages[1]["role"] == "assistant"
    assert session.messages[1]["content"] == "First response."
    assert session.messages[2]["role"] == "user"
    assert session.messages[2]["content"] == "second message"
    assert session.messages[3]["role"] == "assistant"
    assert session.messages[3]["content"] == "Second response."

    session_file = config.data_path / "sessions" / "main.jsonl"
    assert session_file.exists()
    lines = [ln for ln in session_file.read_text().splitlines() if ln.strip()]
    assert len(lines) == 5  # 1 metadata + 4 messages


@pytest.mark.asyncio
async def test_e2e_memory_consolidation(tmp_path: Path, mock_provider: MockProvider) -> None:
    """memory_window=2 triggers consolidation after 1 turn (user+assistant=2 msgs)."""
    config = DrClawConfig(
        data_dir=str(tmp_path / "data"),
        agent=AgentConfig(memory_window=2),
    )
    project = make_project()
    agent = ProjectAgent(config, mock_provider, project)

    mock_provider.queue(
        make_text_response("Analyzing your dataset now."),
        make_tool_response(
            "save_memory",
            {
                "history_entry": "[2026-03-04] Discussed dataset analysis with user.",
                "memory_update": "# Project Memory\n\nUser requested dataset analysis.",
            },
        ),
    )

    await agent.process_direct("analyze my dataset")

    project_dir = config.data_path / "projects" / project.id
    memory = (project_dir / "MEMORY.md").read_text()
    assert "dataset analysis" in memory.lower()

    history = (project_dir / "HISTORY.md").read_text()
    assert "2026-03-04" in history
    assert "dataset analysis" in history.lower()


@pytest.mark.asyncio
async def test_e2e_skill_injected(config: DrClawConfig, mock_provider: MockProvider) -> None:
    """Create project → build ProjectAgent → send message → seeded memory skill in prompt."""
    agent = MainAgent(config, mock_provider)
    project = agent.project_store.create_project("SkillTest")

    proj_agent = ProjectAgent(config, mock_provider, project)
    mock_provider.queue(make_text_response("ok"))
    await proj_agent.process_direct("hello")

    system_msg = mock_provider.calls[0]["messages"][0]["content"]
    assert "Active Skills" in system_msg
    assert "MEMORY.md" in system_msg
    assert "HISTORY.md" in system_msg
