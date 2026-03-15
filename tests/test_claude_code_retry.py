"""Tests for retry logic in Claude Code session manager."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from drclaw.claude_code.retry import _is_retryable, with_retry


# ---------------------------------------------------------------------------
# Unit tests for _is_retryable
# ---------------------------------------------------------------------------


def test_is_retryable_returns_false_when_sdk_not_installed() -> None:
    """Baseline: ValueError is never retryable."""
    assert not _is_retryable(ValueError("boom"))


def test_is_retryable_cli_connection_error() -> None:
    try:
        from claude_agent_sdk import CLIConnectionError
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")
    assert _is_retryable(CLIConnectionError("transient"))


def test_is_retryable_process_error() -> None:
    try:
        from claude_agent_sdk import ProcessError
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")
    assert _is_retryable(ProcessError("crash"))


def test_is_retryable_cli_not_found_returns_false() -> None:
    """CLINotFoundError must NOT be retried — binary won't appear on its own."""
    try:
        from claude_agent_sdk import CLINotFoundError
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")
    assert not _is_retryable(CLINotFoundError("not found"))


# ---------------------------------------------------------------------------
# Unit tests for with_retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_succeeds_immediately() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result = await with_retry(fn, max_retries=3, base_delay=0.0, max_delay=1.0)
    assert result == "ok"
    assert calls == 1


@pytest.mark.asyncio
async def test_retry_non_retryable_raises_immediately() -> None:
    """Non-retryable errors must propagate without any retry."""
    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("not retryable")

    with pytest.raises(ValueError, match="not retryable"):
        await with_retry(fn, max_retries=5, base_delay=0.0, max_delay=1.0)

    assert calls == 1


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_failure() -> None:
    try:
        from claude_agent_sdk import CLIConnectionError
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")

    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise CLIConnectionError("transient")
        return "recovered"

    with patch("drclaw.claude_code.retry.asyncio.sleep", new=AsyncMock()):
        result = await with_retry(fn, max_retries=5, base_delay=0.01, max_delay=1.0)

    assert result == "recovered"
    assert calls == 3


@pytest.mark.asyncio
async def test_retry_exhausted_raises_last_exception() -> None:
    try:
        from claude_agent_sdk import ProcessError
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")

    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1
        raise ProcessError(f"crash #{calls}")

    with patch("drclaw.claude_code.retry.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(ProcessError, match="crash #4"):
            await with_retry(fn, max_retries=3, base_delay=0.01, max_delay=1.0)

    assert calls == 4  # 1 initial + 3 retries


@pytest.mark.asyncio
async def test_backoff_delays_grow() -> None:
    try:
        from claude_agent_sdk import CLIConnectionError
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")

    sleep_calls: list[float] = []
    orig_sleep = asyncio.sleep

    async def capture_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    async def fn() -> None:
        raise CLIConnectionError("boom")

    with patch("drclaw.claude_code.retry.asyncio.sleep", side_effect=capture_sleep):
        with pytest.raises(CLIConnectionError):
            await with_retry(fn, max_retries=3, base_delay=2.0, max_delay=100.0)

    assert len(sleep_calls) == 3
    # Each sleep must not exceed base * 2^attempt ceiling (before jitter).
    # With full jitter [0.5, 1.0], sleep[i] <= base * 2^i.
    assert sleep_calls[0] <= 2.0 * 1.0
    assert sleep_calls[1] <= 4.0 * 1.0
    assert sleep_calls[2] <= 8.0 * 1.0
    # And must be positive
    for s in sleep_calls:
        assert s > 0


@pytest.mark.asyncio
async def test_retry_respects_max_delay_cap() -> None:
    try:
        from claude_agent_sdk import CLIConnectionError
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")

    sleep_calls: list[float] = []

    async def capture_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    async def fn() -> None:
        raise CLIConnectionError("boom")

    with patch("drclaw.claude_code.retry.asyncio.sleep", side_effect=capture_sleep):
        with pytest.raises(CLIConnectionError):
            await with_retry(fn, max_retries=5, base_delay=100.0, max_delay=5.0)

    for s in sleep_calls:
        assert s <= 5.0


# ---------------------------------------------------------------------------
# Integration: manager uses retry on connect
# ---------------------------------------------------------------------------


def _make_manager_with_retries(**kwargs):
    from drclaw.claude_code.manager import ClaudeCodeSessionManager
    return ClaudeCodeSessionManager(
        max_retries=kwargs.pop("max_retries", 2),
        retry_base_delay_seconds=0.01,
        retry_max_delay_seconds=0.1,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_manager_retries_on_connect_failure() -> None:
    try:
        from claude_agent_sdk import CLIConnectionError, ResultMessage
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")

    connect_calls = 0

    async def flaky_connect(prompt=None):
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls < 3:
            raise CLIConnectionError("transient")

    def make_receive():
        async def fake_receive_response():
            yield ResultMessage(
                subtype="result",
                duration_ms=100,
                duration_api_ms=50,
                is_error=False,
                num_turns=1,
                session_id="mock",
                total_cost_usd=0.01,
            )
        return fake_receive_response()

    def client_factory(opts):
        c = AsyncMock()
        c.connect = AsyncMock(side_effect=flaky_connect)
        c.disconnect = AsyncMock()
        c.receive_response = make_receive
        return c

    with patch("drclaw.claude_code.retry.asyncio.sleep", new=AsyncMock()):
        manager = _make_manager_with_retries(max_retries=3, client_factory=client_factory)
        session = await manager.start_session(
            instruction="test",
            caller_agent_id="test_agent",
            cwd="/tmp",
        )
        await asyncio.wait_for(session.idle_event.wait(), timeout=5)

    assert session.status == "idle"
    assert connect_calls == 3


@pytest.mark.asyncio
async def test_manager_fails_after_exhausting_retries() -> None:
    try:
        from claude_agent_sdk import CLIConnectionError
    except ImportError:
        pytest.skip("claude_agent_sdk not installed")

    def client_factory(opts):
        c = AsyncMock()
        c.connect = AsyncMock(side_effect=CLIConnectionError("always fails"))
        c.disconnect = AsyncMock()
        return c

    with patch("drclaw.claude_code.retry.asyncio.sleep", new=AsyncMock()):
        manager = _make_manager_with_retries(max_retries=2, client_factory=client_factory)
        session = await manager.start_session(
            instruction="test",
            caller_agent_id="test_agent",
            cwd="/tmp",
        )
        await asyncio.wait_for(session.done_event.wait(), timeout=5)

    assert session.status == "failed"
    assert "always fails" in session.status_message
