"""Tests for Claude Code session models and manager."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from drclaw.bus.queue import MessageBus
from drclaw.claude_code.manager import ClaudeCodeSessionManager
from drclaw.claude_code.models import TERMINAL_STATES, ClaudeCodeSession

# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestClaudeCodeSession:
    def test_initial_state(self) -> None:
        s = ClaudeCodeSession(
            session_id="abc",
            caller_agent_id="proj:p1",
            cwd="/tmp",
            instruction="fix bug",
        )
        assert s.status == "running"
        assert s.num_turns == 0
        assert s.total_cost_usd is None
        assert not s.done_event.is_set()
        assert not s.idle_event.is_set()

    def test_mark_idle(self) -> None:
        s = ClaudeCodeSession(
            session_id="abc",
            caller_agent_id="proj:p1",
            cwd="/tmp",
            instruction="fix bug",
        )
        s.mark_idle(cost=0.05, num_turns=3)
        assert s.status == "idle"
        assert s.total_cost_usd == 0.05
        assert s.num_turns == 3
        assert s.idle_event.is_set()
        assert not s.done_event.is_set()

    def test_mark_running_clears_idle(self) -> None:
        s = ClaudeCodeSession(
            session_id="abc",
            caller_agent_id="proj:p1",
            cwd="/tmp",
            instruction="fix bug",
        )
        s.mark_idle()
        s.mark_running()
        assert s.status == "running"
        assert not s.idle_event.is_set()

    def test_mark_terminal_sets_both_events(self) -> None:
        s = ClaudeCodeSession(
            session_id="abc",
            caller_agent_id="proj:p1",
            cwd="/tmp",
            instruction="fix bug",
        )
        s.mark_terminal("succeeded", "done")
        assert s.status == "succeeded"
        assert s.idle_event.is_set()
        assert s.done_event.is_set()
        assert s.ended_at is not None

    def test_mark_terminal_rejects_non_terminal(self) -> None:
        s = ClaudeCodeSession(
            session_id="abc",
            caller_agent_id="proj:p1",
            cwd="/tmp",
            instruction="fix bug",
        )
        with pytest.raises(ValueError, match="Not a terminal state"):
            s.mark_terminal("running")

    def test_terminal_states_set(self) -> None:
        assert TERMINAL_STATES == {"succeeded", "failed", "cancelled"}

    def test_num_turns_accumulates(self) -> None:
        s = ClaudeCodeSession(
            session_id="abc",
            caller_agent_id="proj:p1",
            cwd="/tmp",
            instruction="fix bug",
        )
        s.mark_idle(num_turns=2)
        s.mark_running()
        s.mark_idle(num_turns=3)
        assert s.num_turns == 5


# ---------------------------------------------------------------------------
# Manager tests
# ---------------------------------------------------------------------------


def _make_mock_client() -> MagicMock:
    """Create a mock ClaudeSDKClient that yields a real ResultMessage."""
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
                total_cost_usd=0.02,
            )
        return fake_receive_response()

    client.receive_response = make_receive
    return client


def _make_manager(
    bus: MessageBus | None = None,
    max_concurrent: int = 4,
    client_factory: Any = None,
) -> ClaudeCodeSessionManager:
    return ClaudeCodeSessionManager(
        bus=bus,
        max_concurrent=max_concurrent,
        idle_timeout_seconds=300,
        client_factory=client_factory,
    )


@pytest.mark.asyncio
async def test_start_session_creates_session() -> None:
    mock_client = _make_mock_client()
    manager = _make_manager(client_factory=lambda opts: mock_client)

    session = await manager.start_session(
        instruction="fix the tests",
        caller_agent_id="proj:p1",
        cwd="/tmp/workspace",
    )
    assert session.session_id
    assert session.status == "running"
    assert session.caller_agent_id == "proj:p1"

    # Wait for the background task to complete
    await asyncio.wait_for(session.idle_event.wait(), timeout=5)
    assert session.status == "idle"
    assert manager.get_session(session.session_id) is session


@pytest.mark.asyncio
async def test_concurrency_limit() -> None:
    # Create a client that blocks forever
    never_done = asyncio.Event()

    async def fake_connect(prompt=None):
        await never_done.wait()

    blocking_client = AsyncMock()
    blocking_client.connect = fake_connect
    blocking_client.disconnect = AsyncMock()

    manager = _make_manager(
        max_concurrent=1,
        client_factory=lambda opts: blocking_client,
    )

    await manager.start_session(
        instruction="task 1",
        caller_agent_id="proj:p1",
        cwd="/tmp",
    )

    with pytest.raises(RuntimeError, match="Too many active"):
        await manager.start_session(
            instruction="task 2",
            caller_agent_id="proj:p1",
            cwd="/tmp",
        )

    await manager.stop_all()


@pytest.mark.asyncio
async def test_close_on_complete() -> None:
    mock_client = _make_mock_client()
    manager = _make_manager(client_factory=lambda opts: mock_client)

    session = await manager.start_session(
        instruction="one-shot task",
        caller_agent_id="proj:p1",
        cwd="/tmp",
        close_on_complete=True,
    )

    await asyncio.wait_for(session.done_event.wait(), timeout=5)
    assert session.status == "succeeded"


@pytest.mark.asyncio
async def test_send_message_to_idle_session() -> None:
    mock_client = _make_mock_client()
    manager = _make_manager(client_factory=lambda opts: mock_client)

    session = await manager.start_session(
        instruction="start work",
        caller_agent_id="proj:p1",
        cwd="/tmp",
    )
    await asyncio.wait_for(session.idle_event.wait(), timeout=5)
    assert session.status == "idle"

    session = await manager.send_message(session.session_id, "do more work")
    assert session.status == "idle"
    assert session.num_turns == 2


@pytest.mark.asyncio
async def test_send_message_rejects_non_idle() -> None:
    mock_client = _make_mock_client()
    manager = _make_manager(client_factory=lambda opts: mock_client)

    session = await manager.start_session(
        instruction="task",
        caller_agent_id="proj:p1",
        cwd="/tmp",
        close_on_complete=True,
    )
    await asyncio.wait_for(session.done_event.wait(), timeout=5)

    with pytest.raises(RuntimeError, match="not idle"):
        await manager.send_message(session.session_id, "more")


@pytest.mark.asyncio
async def test_close_session() -> None:
    mock_client = _make_mock_client()
    manager = _make_manager(client_factory=lambda opts: mock_client)

    session = await manager.start_session(
        instruction="task",
        caller_agent_id="proj:p1",
        cwd="/tmp",
    )
    await asyncio.wait_for(session.idle_event.wait(), timeout=5)

    session = await manager.close_session(session.session_id)
    assert session.status == "succeeded"
    assert session.done_event.is_set()


@pytest.mark.asyncio
async def test_list_for_caller() -> None:
    mock_client = _make_mock_client()
    manager = _make_manager(client_factory=lambda opts: mock_client)

    s1 = await manager.start_session(
        instruction="task 1",
        caller_agent_id="proj:p1",
        cwd="/tmp",
    )
    s2 = await manager.start_session(
        instruction="task 2",
        caller_agent_id="proj:p2",
        cwd="/tmp",
    )

    await asyncio.wait_for(s1.idle_event.wait(), timeout=5)
    await asyncio.wait_for(s2.idle_event.wait(), timeout=5)

    p1_sessions = manager.list_for_caller("proj:p1")
    assert len(p1_sessions) == 1
    assert p1_sessions[0].session_id == s1.session_id

    all_sessions = manager.list_for_caller("proj:p1", include_terminal=True)
    assert len(all_sessions) == 1


@pytest.mark.asyncio
async def test_bus_notification_on_completion() -> None:
    bus = MessageBus()
    out_q = bus.subscribe_outbound("test")
    mock_client = _make_mock_client()
    manager = _make_manager(bus=bus, client_factory=lambda opts: mock_client)

    session = await manager.start_session(
        instruction="task",
        caller_agent_id="proj:p1",
        cwd="/tmp",
        close_on_complete=True,
    )
    await asyncio.wait_for(session.done_event.wait(), timeout=5)

    # Drain outbound messages — at least one should be claude_code_event
    messages = []
    while not out_q.empty():
        messages.append(await out_q.get())
    assert any(m.msg_type == "claude_code_event" for m in messages)


@pytest.mark.asyncio
async def test_stop_all_cancels_sessions() -> None:
    never_done = asyncio.Event()

    async def fake_connect(prompt=None):
        await never_done.wait()

    blocking_client = AsyncMock()
    blocking_client.connect = fake_connect
    blocking_client.disconnect = AsyncMock()

    manager = _make_manager(client_factory=lambda opts: blocking_client)
    session = await manager.start_session(
        instruction="task",
        caller_agent_id="proj:p1",
        cwd="/tmp",
    )

    await manager.stop_all()
    assert session.status in TERMINAL_STATES
