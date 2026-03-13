"""Tests for MemoryStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from drclaw.agent.memory import MemoryStore
from drclaw.providers.base import LLMResponse, ToolCallRequest
from drclaw.session.manager import Message, Session
from tests.mocks import MockProvider


@pytest.fixture
def base_dir(tmp_path: Path) -> Path:
    return tmp_path / "project"  # intentionally not pre-created: MemoryStore must create it


@pytest.fixture
def store(base_dir: Path) -> MemoryStore:
    return MemoryStore(base_dir)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_init_creates_base_dir(tmp_path: Path):
    """MemoryStore creates base_dir on construction if it does not exist."""
    d = tmp_path / "nonexistent" / "project"
    assert not d.exists()
    MemoryStore(d)
    assert d.is_dir()


# ---------------------------------------------------------------------------
# Basic read/write
# ---------------------------------------------------------------------------


def test_read_long_term_empty(store: MemoryStore):
    """Missing MEMORY.md returns empty string."""
    assert store.read_long_term() == ""


def test_write_read_long_term(store: MemoryStore):
    """write_long_term then read_long_term round-trips the content."""
    store.write_long_term("# Facts\n- user likes Python\n")
    assert store.read_long_term() == "# Facts\n- user likes Python\n"


def test_append_history(store: MemoryStore, base_dir: Path):
    """append_history entries are separated by a double newline."""
    store.append_history("[2024-01-01 10:00] First entry.")
    store.append_history("[2024-01-01 11:00] Second entry.")
    text = (base_dir / "HISTORY.md").read_text(encoding="utf-8")
    assert "[2024-01-01 10:00] First entry.\n\n" in text
    assert "[2024-01-01 11:00] Second entry.\n\n" in text


# ---------------------------------------------------------------------------
# get_memory_context
# ---------------------------------------------------------------------------


def test_get_memory_context_empty(store: MemoryStore):
    """Returns empty string when MEMORY.md doesn't exist."""
    assert store.get_memory_context() == ""


def test_get_memory_context(store: MemoryStore):
    """Returns formatted string with ## Long-term Memory header."""
    store.write_long_term("- user is a researcher\n")
    ctx = store.get_memory_context()
    assert ctx.startswith("## Long-term Memory\n")
    assert "- user is a researcher\n" in ctx


# ---------------------------------------------------------------------------
# consolidate — helpers
# ---------------------------------------------------------------------------


def _make_session(n_pairs: int) -> Session:
    """Return a session with n_pairs of (user, assistant) messages."""
    msgs: list[Message] = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ] * n_pairs
    return Session(session_key="cli:main", messages=msgs)


def _tool_response(history_entry: str, memory_update: str) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCallRequest(
                id="tc1",
                name="save_memory",
                arguments={"history_entry": history_entry, "memory_update": memory_update},
            )
        ],
        stop_reason="tool_use",
        input_tokens=100,
        output_tokens=50,
    )


# ---------------------------------------------------------------------------
# consolidate — success paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_success(base_dir: Path):
    """MockProvider returns save_memory tool call — MEMORY.md and HISTORY.md written."""
    provider = MockProvider(
        responses=[
            _tool_response("[2024-01-01 10:00] Discussed Python.", "# Facts\n- user likes Python\n")
        ]
    )
    store = MemoryStore(base_dir)
    session = _make_session(30)  # 60 messages — well above default window

    result = await store.consolidate(session, provider)

    assert result is True
    assert "user likes Python" in (base_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "Discussed Python" in (base_dir / "HISTORY.md").read_text(encoding="utf-8")
    # After consolidation, old messages are trimmed; only keep_count remain.
    assert session.last_consolidated == 0
    assert len(session.messages) == 25  # keep_count = 50 // 2


@pytest.mark.asyncio
async def test_consolidate_archive_all(base_dir: Path):
    """archive_all=True consolidates every message and resets last_consolidated to 0."""
    provider = MockProvider(
        responses=[_tool_response("[2024-01-01 12:00] Full archive.", "# Archive\n- all done\n")]
    )
    store = MemoryStore(base_dir)
    session = _make_session(5)  # only 10 messages — below normal trigger threshold

    result = await store.consolidate(session, provider, archive_all=True)

    assert result is True
    assert session.last_consolidated == 0
    assert "Full archive" in (base_dir / "HISTORY.md").read_text(encoding="utf-8")
    assert len(provider.calls) == 1  # LLM was called despite low message count


@pytest.mark.asyncio
async def test_consolidate_trims_messages(base_dir: Path):
    """Non-archive consolidation trims old messages and resets last_consolidated."""
    provider = MockProvider(
        responses=[_tool_response("[2024-01-01 10:00] Done.", "# Facts\n- noted\n")]
    )
    store = MemoryStore(base_dir)
    session = _make_session(30)  # 60 messages, keep_count = 25

    await store.consolidate(session, provider, memory_window=50)

    assert len(session.messages) == 25
    assert session.last_consolidated == 0


# ---------------------------------------------------------------------------
# consolidate — no-op paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_no_tool_call(base_dir: Path):
    """MockProvider returns text only — returns False without raising."""
    provider = MockProvider(
        responses=[
            LLMResponse(
                content="Here is a summary.",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=50,
                output_tokens=20,
            )
        ]
    )
    store = MemoryStore(base_dir)
    session = _make_session(30)

    result = await store.consolidate(session, provider)

    assert result is False
    assert not (base_dir / "MEMORY.md").exists()
    assert not (base_dir / "HISTORY.md").exists()


@pytest.mark.asyncio
async def test_consolidate_noop_when_few_messages(base_dir: Path):
    """Returns True immediately when message count is within keep_count; LLM not called."""
    provider = MockProvider()
    store = MemoryStore(base_dir)
    session = Session(session_key="cli:main", messages=[{"role": "user", "content": "hi"}])

    result = await store.consolidate(session, provider, memory_window=50)

    assert result is True
    assert provider.calls == []


@pytest.mark.asyncio
async def test_consolidate_noop_when_already_consolidated(base_dir: Path):
    """Returns True without calling LLM when last_consolidated is already at the keep boundary."""
    provider = MockProvider()
    store = MemoryStore(base_dir)
    session = _make_session(30)  # 60 messages, keep_count=25, boundary=35
    session.last_consolidated = 35  # already at boundary

    result = await store.consolidate(session, provider, memory_window=50)

    assert result is True
    assert provider.calls == []


# ---------------------------------------------------------------------------
# consolidate — error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_provider_raises_returns_false(base_dir: Path):
    """If provider.complete() raises, consolidate returns False without propagating."""

    class BrokenProvider(MockProvider):
        async def complete(self, messages, tools=None, system=None):  # type: ignore[override]
            raise RuntimeError("network error")

    store = MemoryStore(base_dir)
    session = _make_session(30)

    result = await store.consolidate(session, BrokenProvider())

    assert result is False


@pytest.mark.asyncio
async def test_consolidate_wrong_tool_name_returns_false(base_dir: Path):
    """If LLM calls a tool other than save_memory, returns False."""
    provider = MockProvider(
        responses=[
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="tc1", name="wrong_tool", arguments={})],
                stop_reason="tool_use",
                input_tokens=10,
                output_tokens=5,
            )
        ]
    )
    store = MemoryStore(base_dir)
    session = _make_session(30)

    result = await store.consolidate(session, provider)

    assert result is False


@pytest.mark.asyncio
async def test_consolidate_empty_args_returns_false(base_dir: Path):
    """If save_memory is called with both fields empty, returns False and writes nothing."""
    provider = MockProvider(responses=[_tool_response("", "")])
    store = MemoryStore(base_dir)
    session = _make_session(30)

    result = await store.consolidate(session, provider)

    assert result is False
    assert not (base_dir / "MEMORY.md").exists()
    assert not (base_dir / "HISTORY.md").exists()
