"""Tests for Claude Code tools."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from drclaw.claude_code.manager import ClaudeCodeSessionManager
from drclaw.tools.claude_code_tools import (
    CloseClaudeCodeSessionTool,
    GetClaudeCodeStatusTool,
    ListClaudeCodeSessionsTool,
    SendClaudeCodeMessageTool,
    UseClaudeCodeTool,
)


def _make_mock_client() -> MagicMock:
    from claude_agent_sdk import ResultMessage

    client = AsyncMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()

    def make_receive():
        async def fake_receive_response():
            yield ResultMessage(
                subtype="result",
                duration_ms=100,
                duration_api_ms=50,
                is_error=False,
                num_turns=1,
                session_id="mock-session",
                total_cost_usd=0.01,
            )
        return fake_receive_response()

    client.receive_response = make_receive
    return client


def _make_manager(client_factory=None) -> ClaudeCodeSessionManager:
    return ClaudeCodeSessionManager(
        max_concurrent=4,
        idle_timeout_seconds=300,
        client_factory=client_factory,
    )


@pytest.mark.asyncio
async def test_use_claude_code_fire_and_forget() -> None:
    mock_client = _make_mock_client()
    manager = _make_manager(client_factory=lambda opts: mock_client)
    tool = UseClaudeCodeTool(
        manager, caller_agent_id="proj:p1", default_cwd="/tmp",
    )

    result = await tool.execute({"instruction": "fix tests"})
    assert "session_id=" in result
    assert "Started Claude Code session" in result

    # Let background task finish
    await asyncio.sleep(0.1)
    await manager.stop_all()


@pytest.mark.asyncio
async def test_use_claude_code_await_result() -> None:
    mock_client = _make_mock_client()
    manager = _make_manager(client_factory=lambda opts: mock_client)
    tool = UseClaudeCodeTool(
        manager, caller_agent_id="proj:p1", default_cwd="/tmp",
    )

    result = await tool.execute({
        "instruction": "fix tests",
        "await_result": True,
        "close_on_complete": True,
    })
    assert "status=succeeded" in result
    await manager.stop_all()


@pytest.mark.asyncio
async def test_send_message_tool() -> None:
    mock_client = _make_mock_client()
    manager = _make_manager(client_factory=lambda opts: mock_client)

    session = await manager.start_session(
        instruction="start",
        caller_agent_id="proj:p1",
        cwd="/tmp",
    )
    await asyncio.wait_for(session.idle_event.wait(), timeout=5)

    tool = SendClaudeCodeMessageTool(manager, caller_agent_id="proj:p1")
    result = await tool.execute({
        "session_id": session.session_id,
        "message": "do more",
    })
    assert "status=" in result
    await manager.stop_all()


@pytest.mark.asyncio
async def test_send_message_wrong_caller() -> None:
    mock_client = _make_mock_client()
    manager = _make_manager(client_factory=lambda opts: mock_client)

    session = await manager.start_session(
        instruction="start",
        caller_agent_id="proj:p1",
        cwd="/tmp",
    )
    await asyncio.wait_for(session.idle_event.wait(), timeout=5)

    tool = SendClaudeCodeMessageTool(manager, caller_agent_id="proj:other")
    result = await tool.execute({
        "session_id": session.session_id,
        "message": "hijack",
    })
    assert "Error:" in result
    assert "belongs to another agent" in result
    await manager.stop_all()


@pytest.mark.asyncio
async def test_get_status_tool() -> None:
    mock_client = _make_mock_client()
    manager = _make_manager(client_factory=lambda opts: mock_client)

    session = await manager.start_session(
        instruction="analyze code",
        caller_agent_id="proj:p1",
        cwd="/tmp",
    )
    await asyncio.wait_for(session.idle_event.wait(), timeout=5)

    tool = GetClaudeCodeStatusTool(manager)
    result = await tool.execute({"session_id": session.session_id})
    assert "status=idle" in result
    assert "proj:p1" in result
    await manager.stop_all()


@pytest.mark.asyncio
async def test_get_status_unknown_session() -> None:
    manager = _make_manager()
    tool = GetClaudeCodeStatusTool(manager)
    result = await tool.execute({"session_id": "nonexistent"})
    assert "Error:" in result


@pytest.mark.asyncio
async def test_list_sessions_tool() -> None:
    mock_client = _make_mock_client()
    manager = _make_manager(client_factory=lambda opts: mock_client)

    s = await manager.start_session(
        instruction="task 1",
        caller_agent_id="proj:p1",
        cwd="/tmp",
    )
    await asyncio.wait_for(s.idle_event.wait(), timeout=5)

    tool = ListClaudeCodeSessionsTool(manager, caller_agent_id="proj:p1")
    result = await tool.execute({})
    assert s.session_id in result
    assert "Active Claude Code sessions:" in result

    # Different caller sees nothing
    tool2 = ListClaudeCodeSessionsTool(manager, caller_agent_id="proj:other")
    result2 = await tool2.execute({})
    assert "No active" in result2
    await manager.stop_all()


@pytest.mark.asyncio
async def test_close_session_tool() -> None:
    mock_client = _make_mock_client()
    manager = _make_manager(client_factory=lambda opts: mock_client)

    session = await manager.start_session(
        instruction="task",
        caller_agent_id="proj:p1",
        cwd="/tmp",
    )
    await asyncio.wait_for(session.idle_event.wait(), timeout=5)

    tool = CloseClaudeCodeSessionTool(manager, caller_agent_id="proj:p1")
    result = await tool.execute({"session_id": session.session_id})
    assert "closed: succeeded" in result
    await manager.stop_all()


@pytest.mark.asyncio
async def test_close_session_wrong_caller() -> None:
    mock_client = _make_mock_client()
    manager = _make_manager(client_factory=lambda opts: mock_client)

    session = await manager.start_session(
        instruction="task",
        caller_agent_id="proj:p1",
        cwd="/tmp",
    )
    await asyncio.wait_for(session.idle_event.wait(), timeout=5)

    tool = CloseClaudeCodeSessionTool(manager, caller_agent_id="proj:other")
    result = await tool.execute({"session_id": session.session_id})
    assert "Error:" in result
    assert "belongs to another agent" in result
    await manager.stop_all()
