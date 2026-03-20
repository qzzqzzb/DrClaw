"""Tests for the AgentLoop engine."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from drclaw.agent.context import ContextBuilder
from drclaw.agent.debug import DebugLogger
from drclaw.agent.loop import MAX_HOPS, AgentLoop
from drclaw.agent.memory import MemoryStore
from drclaw.bus.queue import MessageBus
from drclaw.models.messages import InboundMessage, OutboundMessage
from drclaw.providers.base import ASSISTANT_PROVIDER_FIELDS_KEY, LLMResponse, ToolCallRequest
from drclaw.session.manager import SessionManager
from drclaw.tools.background_tasks import BackgroundToolTaskManager
from drclaw.tools.base import Tool
from drclaw.tools.registry import ToolRegistry
from tests.conftest import read_jsonl_entries
from tests.mocks import (
    MockProvider,
    make_error_response,
    make_text_response,
    make_tool_response,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo input"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        return params["text"]


class _SlowTool(Tool):
    @property
    def name(self) -> str:
        return "slow_tool"

    @property
    def description(self) -> str:
        return "Sleep before returning."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"delay": {"type": "number"}},
            "required": ["delay"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        await asyncio.sleep(float(params["delay"]))
        return "slow-result"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sessions"
    d.mkdir()
    return d


@pytest.fixture
def make_loop(sessions_dir: Path, mock_provider: MockProvider):
    """Factory that builds an AgentLoop with optional overrides."""

    def _make(
        registry: ToolRegistry | None = None,
        memory_store: MemoryStore | None = None,
        bus: MessageBus | None = None,
        max_iterations: int = 40,
        memory_window: int = 100,
        max_history: int | None = None,
        agent_id: str = "main",
        session_key: str | None = None,
        background_task_manager: BackgroundToolTaskManager | None = None,
        tool_detach_timeout_seconds: float = 60,
    ) -> AgentLoop:
        return AgentLoop(
            provider=mock_provider,
            tool_registry=registry or ToolRegistry(),
            context_builder=ContextBuilder("You are a test agent."),
            session_manager=SessionManager(sessions_dir),
            bus=bus,
            memory_store=memory_store,
            max_iterations=max_iterations,
            memory_window=memory_window,
            max_history=max_history,
            agent_id=agent_id,
            session_key=session_key,
            background_task_manager=background_task_manager,
            tool_detach_timeout_seconds=tool_detach_timeout_seconds,
        )

    return _make


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_text_response(make_loop, mock_provider: MockProvider) -> None:
    """MockProvider returns text → process_direct returns it."""
    mock_provider.queue(make_text_response("Hello there!"))
    loop = make_loop()
    result = await loop.process_direct("hi", "cli:test")
    assert result == "Hello there!"


@pytest.mark.asyncio
async def test_process_direct_persists_user_source(make_loop, mock_provider: MockProvider) -> None:
    mock_provider.queue(make_text_response("ok"))
    loop = make_loop()

    _ = await loop.process_direct("delegated input", "cli:test", source="proj:abc123")
    session = loop.session_manager.load("cli:test")
    user_messages = [m for m in session.messages if m.get("role") == "user"]
    assert user_messages
    assert user_messages[0].get("source") == "proj:abc123"


@pytest.mark.asyncio
async def test_process_direct_persists_source_for_assistant_and_tool_messages(
    make_loop, mock_provider: MockProvider,
) -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())
    mock_provider.queue(
        make_tool_response("echo", {"text": "ping"}),
        make_text_response("done"),
    )
    loop = make_loop(registry=registry, agent_id="proj:abc123")

    _ = await loop.process_direct("echo ping", "cli:test", source="main")
    session = loop.session_manager.load("cli:test")

    non_system_messages = [m for m in session.messages if m.get("role") != "system"]
    assert non_system_messages
    assert all(
        isinstance(m.get("source"), str) and bool(str(m.get("source")).strip())
        for m in non_system_messages
    )

    assistant_messages = [m for m in non_system_messages if m.get("role") == "assistant"]
    tool_messages = [m for m in non_system_messages if m.get("role") == "tool"]
    assert assistant_messages
    assert tool_messages
    assert all(m.get("source") == "proj:abc123" for m in assistant_messages)
    assert all(m.get("source") == "proj:abc123" for m in tool_messages)


@pytest.mark.asyncio
async def test_history_source_not_forwarded_to_provider(
    make_loop, mock_provider: MockProvider,
) -> None:
    loop = make_loop()
    session = loop.session_manager.load("cli:test")
    session.messages = [
        {"role": "user", "content": "old", "source": "proj:delegated"},
        {"role": "assistant", "content": "ack"},
    ]
    loop.session_manager.save(session)

    mock_provider.queue(make_text_response("new"))
    _ = await loop.process_direct("next", "cli:test")

    first_call_messages = mock_provider.calls[0]["messages"]
    history_user = next(m for m in first_call_messages if m.get("name") == "proj:delegated")
    assert "source" not in history_user
    assert history_user.get("role") == "user"
    assert history_user.get("content") == "[Message Source: proj:delegated]\nold"


@pytest.mark.asyncio
async def test_history_preserves_internal_assistant_provider_fields(
    make_loop, mock_provider: MockProvider,
) -> None:
    loop = make_loop()
    session = loop.session_manager.load("cli:test")
    session.messages = [
        {"role": "user", "content": "old question", "source": "user"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "tc1",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{\"query\":\"drclaw\"}"},
                }
            ],
            ASSISTANT_PROVIDER_FIELDS_KEY: {"reasoning_content": "step by step"},
            "source": "main",
        }
    ]
    loop.session_manager.save(session)

    mock_provider.queue(make_text_response("ok"))
    _ = await loop.process_direct("next", "cli:test")

    first_call_messages = mock_provider.calls[0]["messages"]
    history_assistant = next(m for m in first_call_messages if m.get("role") == "assistant")
    assert history_assistant[ASSISTANT_PROVIDER_FIELDS_KEY] == {
        "reasoning_content": "step by step"
    }


@pytest.mark.asyncio
async def test_process_direct_persists_assistant_provider_metadata(
    make_loop, mock_provider: MockProvider,
) -> None:
    mock_provider.queue(
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="tc1", name="echo", arguments={"text": "ping"})],
            stop_reason="tool_use",
            input_tokens=1,
            output_tokens=1,
            assistant_metadata={"reasoning_content": "step by step"},
        ),
        make_text_response("done"),
    )
    registry = ToolRegistry()
    registry.register(_EchoTool())
    loop = make_loop(registry=registry)

    _ = await loop.process_direct("echo ping", "cli:test")
    session = loop.session_manager.load("cli:test")
    assistant_msg = next(m for m in session.messages if m.get("role") == "assistant")
    assert assistant_msg[ASSISTANT_PROVIDER_FIELDS_KEY] == {"reasoning_content": "step by step"}


@pytest.mark.asyncio
async def test_history_user_source_does_not_inject_name_or_header(
    make_loop, mock_provider: MockProvider,
) -> None:
    loop = make_loop()
    session = loop.session_manager.load("cli:test")
    session.messages = [
        {"role": "user", "content": "plain-user", "source": "user"},
    ]
    loop.session_manager.save(session)

    mock_provider.queue(make_text_response("ok"))
    _ = await loop.process_direct("next", "cli:test")

    first_call_messages = mock_provider.calls[0]["messages"]
    history_user = next(
        m for m in first_call_messages
        if m.get("role") == "user" and m.get("content") == "plain-user"
    )
    assert "name" not in history_user


@pytest.mark.asyncio
async def test_history_external_human_source_does_not_inject_name_or_header(
    make_loop, mock_provider: MockProvider,
) -> None:
    loop = make_loop()
    session = loop.session_manager.load("cli:test")
    session.messages = [
        {"role": "user", "content": "from-feishu", "source": "feishu:ou_123"},
    ]
    loop.session_manager.save(session)

    mock_provider.queue(make_text_response("ok"))
    _ = await loop.process_direct("next", "cli:test")

    first_call_messages = mock_provider.calls[0]["messages"]
    history_user = next(
        m for m in first_call_messages
        if m.get("role") == "user" and m.get("content") == "from-feishu"
    )
    assert "name" not in history_user


@pytest.mark.asyncio
async def test_process_direct_records_usage(make_loop, mock_provider: MockProvider) -> None:
    class _UsageStore:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def record(self, **kwargs: Any) -> None:
            self.calls.append(kwargs)

    usage_store = _UsageStore()
    mock_provider.queue(
        LLMResponse(
            content="ok",
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=11,
            output_tokens=7,
            input_cost_usd=0.0011,
            output_cost_usd=0.0007,
            total_cost_usd=0.0018,
        )
    )
    loop = make_loop()
    loop.usage_store = usage_store
    await loop.process_direct("hi", "cli:test")

    assert len(usage_store.calls) == 1
    assert usage_store.calls[0]["agent_id"] == "main"
    assert usage_store.calls[0]["input_tokens"] == 11
    assert usage_store.calls[0]["output_tokens"] == 7
    assert usage_store.calls[0]["total_cost_usd"] == 0.0018


@pytest.mark.asyncio
async def test_tool_call_cycle(make_loop, mock_provider: MockProvider) -> None:
    """Provider returns tool_call then text → tool executed, text returned."""
    registry = ToolRegistry()
    registry.register(_EchoTool())

    mock_provider.queue(
        make_tool_response("echo", {"text": "ping"}),
        make_text_response("Done!"),
    )

    loop = make_loop(registry=registry)
    result = await loop.process_direct("echo ping", "cli:test")
    assert result == "Done!"
    # Provider was called twice (tool call + final response)
    assert len(mock_provider.calls) == 2


@pytest.mark.asyncio
async def test_long_tool_call_detaches_to_background(
    make_loop, mock_provider: MockProvider,
) -> None:
    registry = ToolRegistry()
    registry.register(_SlowTool())
    manager = BackgroundToolTaskManager(caller_agent_id="main")

    mock_provider.queue(
        make_tool_response("slow_tool", {"delay": 0.2}),
        make_text_response("I detached it."),
    )

    loop = make_loop(
        registry=registry,
        background_task_manager=manager,
        tool_detach_timeout_seconds=1 / 20,
    )
    result = await loop.process_direct("run slow tool", "cli:test")
    assert result == "I detached it."

    second_call_msgs = mock_provider.calls[1]["messages"]
    tool_results = [m for m in second_call_msgs if m.get("role") == "tool"]
    assert tool_results
    assert "running in background" in tool_results[-1]["content"]
    assert "task_id=" in tool_results[-1]["content"]

    active = manager.list_for_caller()
    assert len(active) == 1
    assert active[0].status == "running"

    await asyncio.sleep(0.3)
    done = manager.list_for_caller(include_terminal=True)
    assert len(done) == 1
    assert done[0].status == "succeeded"
    assert done[0].result is not None
    assert "slow-result" in done[0].result


@pytest.mark.asyncio
async def test_detached_task_completion_triggers_callback_turn(
    make_loop, mock_provider: MockProvider,
) -> None:
    bus = MessageBus()
    outbound_q = bus.subscribe_outbound("test")
    registry = ToolRegistry()
    registry.register(_SlowTool())
    manager = BackgroundToolTaskManager(caller_agent_id="main")

    mock_provider.queue(
        make_tool_response("slow_tool", {"delay": 0.1}),
        make_text_response("first-turn"),
        make_text_response("callback-turn"),
    )
    loop = make_loop(
        registry=registry,
        bus=bus,
        background_task_manager=manager,
        tool_detach_timeout_seconds=1 / 20,
    )
    runner = asyncio.create_task(loop.run())
    await bus.publish_inbound(
        InboundMessage(
            channel="web",
            chat_id="chat1",
            text="start",
        ),
        topic="main",
    )

    seen_types: list[str] = []
    seen_texts: list[str] = []
    for _ in range(8):
        msg = await asyncio.wait_for(outbound_q.get(), timeout=1.5)
        seen_types.append(msg.msg_type)
        seen_texts.append(msg.text)
        if msg.msg_type == "chat" and msg.text == "callback-turn":
            break

    loop.stop()
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner

    assert "tool_task_started" in seen_types
    assert "tool_task_completed" in seen_types
    assert "first-turn" in seen_texts
    assert "callback-turn" in seen_texts


@pytest.mark.asyncio
async def test_max_iterations_cap(make_loop, mock_provider: MockProvider) -> None:
    """When max iterations hit, loop requests one final answer-only response."""
    registry = ToolRegistry()
    registry.register(_EchoTool())

    # Queue tool responses to hit cap, then one text response for finalize pass.
    for i in range(3):
        mock_provider.queue(make_tool_response("echo", {"text": f"iter{i}"}, f"tc{i}"))
    mock_provider.queue(make_text_response("Final summary without more tools."))

    loop = make_loop(registry=registry, max_iterations=3)
    result = await loop.process_direct("loop forever", "cli:test")
    assert result == "Final summary without more tools."
    # Provider called max_iterations + 1 finalization pass.
    assert len(mock_provider.calls) == 4
    assert mock_provider.calls[-1]["tools"] is None
    final_messages = mock_provider.calls[-1]["messages"]
    assert any(
        m.get("role") == "system"
        and "iteration budget has been exhausted" in str(m.get("content", ""))
        for m in final_messages
    )


@pytest.mark.asyncio
async def test_max_iterations_finalize_ignores_tool_calls(
    make_loop, mock_provider: MockProvider
) -> None:
    """If finalization pass still returns tool_calls, they are not executed."""
    registry = ToolRegistry()
    registry.register(_EchoTool())

    mock_provider.queue(
        make_tool_response("echo", {"text": "iter0"}, "tc0"),
        make_tool_response("echo", {"text": "iter1"}, "tc1"),
        make_tool_response("echo", {"text": "should-not-run"}, "tc-final"),
    )

    loop = make_loop(registry=registry, max_iterations=2)
    result = await loop.process_direct("loop forever", "cli:test")

    assert "maximum number of processing steps" in result
    assert len(mock_provider.calls) == 3
    assert mock_provider.calls[-1]["tools"] is None

    session = loop.session_manager.load("cli:test")
    tool_msgs = [m for m in session.messages if m.get("role") == "tool"]
    # Only the two in-budget tool calls were executed.
    assert len(tool_msgs) == 2


@pytest.mark.asyncio
async def test_save_turn_truncation(
    make_loop, mock_provider: MockProvider, sessions_dir: Path
) -> None:
    """Tool result > 500 chars → truncated in session."""
    registry = ToolRegistry()

    class _BigTool(Tool):
        @property
        def name(self) -> str:
            return "big"

        @property
        def description(self) -> str:
            return "Return big output"

        @property
        def parameters(self) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

        async def execute(self, params: dict[str, Any]) -> str:
            return "x" * 1000

    registry.register(_BigTool())
    mock_provider.queue(
        make_tool_response("big", {}),
        make_text_response("done"),
    )

    loop = make_loop(registry=registry)
    await loop.process_direct("big output", "cli:test")

    session = loop.session_manager.load("cli:test")
    tool_msgs = [m for m in session.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert len(tool_msgs[0]["content"]) < 1000
    assert "truncated" in tool_msgs[0]["content"]


@pytest.mark.asyncio
async def test_save_turn_filter_empty_assistant(make_loop, mock_provider: MockProvider) -> None:
    """Empty assistant (no content, no tool_calls) not saved."""
    mock_provider.queue(make_text_response("real answer"))
    loop = make_loop()
    await loop.process_direct("hi", "cli:test")

    session = loop.session_manager.load("cli:test")
    for msg in session.messages:
        if msg.get("role") == "assistant":
            # All saved assistants must have content or tool_calls
            assert msg.get("content") or msg.get("tool_calls")


@pytest.mark.asyncio
async def test_save_turn_filter_runtime_context(make_loop, mock_provider: MockProvider) -> None:
    """Runtime context messages filtered from session."""
    mock_provider.queue(make_text_response("ok"))
    loop = make_loop()
    await loop.process_direct("hello", "cli:test")

    session = loop.session_manager.load("cli:test")
    for msg in session.messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            assert not (
                isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
            )


@pytest.mark.asyncio
async def test_dispatch_includes_runtime_webui_language_for_main_agent(
    make_loop, mock_provider: MockProvider
) -> None:
    bus = MessageBus()
    mock_provider.queue(make_text_response("ok"))
    loop = make_loop(bus=bus, agent_id="main")

    await loop._dispatch(
        InboundMessage(
            channel="web",
            chat_id="c1",
            text="hello",
            metadata={"webui_language": "zh"},
        )
    )
    runtime_ctx = mock_provider.calls[0]["messages"][1]["content"]
    assert "WebUI Preferred Response Language: zh" in runtime_ctx


@pytest.mark.asyncio
async def test_dispatch_does_not_inject_runtime_webui_language_for_project_agents(
    make_loop, mock_provider: MockProvider
) -> None:
    bus = MessageBus()
    mock_provider.queue(make_text_response("ok"))
    loop = make_loop(bus=bus, agent_id="proj:abc")

    await loop._dispatch(
        InboundMessage(
            channel="web",
            chat_id="c1",
            text="hello",
            metadata={"webui_language": "zh"},
        )
    )
    runtime_ctx = mock_provider.calls[0]["messages"][1]["content"]
    assert "WebUI Preferred Response Language" not in runtime_ctx


@pytest.mark.asyncio
async def test_processing_lock(make_loop, mock_provider: MockProvider) -> None:
    """Concurrent _dispatch calls serialized (second waits for first)."""
    bus = MessageBus()
    loop = make_loop(bus=bus)

    order: list[int] = []

    original_process = loop.process_direct

    async def slow_process(content, session_key, **kwargs):
        order.append(1)
        await asyncio.sleep(0.05)
        result = await original_process(content, session_key, **kwargs)
        order.append(2)
        return result

    loop.process_direct = slow_process  # type: ignore[assignment]

    msg1 = InboundMessage(channel="cli", chat_id="a", text="first")
    msg2 = InboundMessage(channel="cli", chat_id="b", text="second")

    await asyncio.gather(loop._dispatch(msg1), loop._dispatch(msg2))

    # Under the lock, calls are serialized: [1, 2, 1, 2] not [1, 1, 2, 2]
    assert order == [1, 2, 1, 2]


@pytest.mark.asyncio
async def test_tool_call_published_before_tool_side_effect_outbound(
    make_loop, mock_provider: MockProvider
) -> None:
    bus = MessageBus()
    outbound_q = bus.subscribe_outbound("test")
    registry = ToolRegistry()

    class _PublishingTool(Tool):
        @property
        def name(self) -> str:
            return "publish_status"

        @property
        def description(self) -> str:
            return "Publish an outbound status as a side effect."

        @property
        def parameters(self) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

        async def execute(self, params: dict[str, Any]) -> str:
            await bus.publish_outbound(
                OutboundMessage(
                    channel="system",
                    chat_id="proj:test",
                    text="status from tool",
                    source="equip:test:1",
                    topic="proj:test",
                    msg_type="equipment_status",
                )
            )
            return "ok"

    registry.register(_PublishingTool())
    mock_provider.queue(
        make_tool_response("publish_status", {}),
        make_text_response("done"),
    )
    loop = make_loop(registry=registry, bus=bus, agent_id="main")

    await loop._dispatch(InboundMessage(channel="cli", chat_id="c1", text="run tool"))

    first = await asyncio.wait_for(outbound_q.get(), timeout=0.2)
    second = await asyncio.wait_for(outbound_q.get(), timeout=0.2)
    assert first.msg_type == "tool_call"
    assert second.msg_type == "equipment_status"


@pytest.mark.asyncio
async def test_message_tool_suppresses_auto_final_outbound(
    make_loop, mock_provider: MockProvider
) -> None:
    bus = MessageBus()
    outbound_q = bus.subscribe_outbound("test")
    registry = ToolRegistry()

    class _MessageLikeTool(Tool):
        def __init__(self) -> None:
            self._channel = ""
            self._chat_id = ""
            self._sent = False

        @property
        def name(self) -> str:
            return "message"

        @property
        def description(self) -> str:
            return "Send outbound message"

        @property
        def parameters(self) -> dict[str, Any]:
            return {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
            }

        def set_context(self, channel: str, chat_id: str) -> None:
            self._channel = channel
            self._chat_id = chat_id

        def start_turn(self) -> None:
            self._sent = False

        def sent_in_turn_for(self, channel: str, chat_id: str) -> bool:
            return self._sent and channel == self._channel and chat_id == self._chat_id

        async def execute(self, params: dict[str, Any]) -> str:
            await bus.publish_outbound(
                OutboundMessage(
                    channel=self._channel,
                    chat_id=self._chat_id,
                    text=params["content"],
                    source="main",
                )
            )
            self._sent = True
            return "Message sent"

    registry.register(_MessageLikeTool())
    mock_provider.queue(
        make_tool_response("message", {"content": "from tool"}),
        make_text_response("final response that should be suppressed"),
    )
    loop = make_loop(registry=registry, bus=bus, agent_id="main")

    await loop._dispatch(InboundMessage(channel="feishu", chat_id="c1", text="run message tool"))

    first = await asyncio.wait_for(outbound_q.get(), timeout=0.2)
    second = await asyncio.wait_for(outbound_q.get(), timeout=0.2)
    assert first.msg_type == "tool_call"
    assert second.text == "from tool"

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(outbound_q.get(), timeout=0.1)


@pytest.mark.asyncio
async def test_memory_consolidation_trigger(
    make_loop, mock_provider: MockProvider, tmp_path: Path
) -> None:
    """Consolidation fires when unconsolidated >= memory_window."""
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    memory_store = MemoryStore(mem_dir)

    # Queue: first call for agent loop, second for consolidation
    mock_provider.queue(
        make_text_response("answer"),
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="mem1",
                    name="save_memory",
                    arguments={
                        "history_entry": "[2026-03-04] Test consolidation.",
                        "memory_update": "Test memory.",
                    },
                )
            ],
            stop_reason="tool_use",
            input_tokens=10,
            output_tokens=5,
        ),
    )

    loop = make_loop(memory_store=memory_store, memory_window=5)

    # Pre-populate session with enough messages to trigger consolidation
    session = loop.session_manager.load("cli:test")
    for i in range(6):
        session.messages.append({"role": "user", "content": f"msg {i}"})
        session.messages.append({"role": "assistant", "content": f"reply {i}"})
    loop.session_manager.save(session)

    await loop.process_direct("trigger consolidation", "cli:test")

    # Provider called twice: once for agent loop, once for consolidation
    assert len(mock_provider.calls) == 2
    # Memory files should be written
    assert memory_store.history_file.exists()


@pytest.mark.asyncio
async def test_consolidate_on_startup_clears_session(
    make_loop, mock_provider: MockProvider, tmp_path: Path
) -> None:
    """consolidate_on_startup clears stale messages without an LLM call."""
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    memory_store = MemoryStore(mem_dir)
    loop = make_loop(memory_store=memory_store)

    # Pre-populate session
    session = loop.session_manager.load("cli:test")
    session.messages.append({"role": "user", "content": "old msg"})
    session.messages.append({"role": "assistant", "content": "old reply"})
    loop.session_manager.save(session)

    await loop.consolidate_on_startup("cli:test")

    # Session should be cleared, no LLM call made
    reloaded = loop.session_manager.load("cli:test")
    assert reloaded.messages == []
    assert reloaded.last_consolidated == 0
    assert len(mock_provider.calls) == 0


@pytest.mark.asyncio
async def test_consolidate_on_startup_noop_empty(
    make_loop, mock_provider: MockProvider, tmp_path: Path
) -> None:
    """consolidate_on_startup is a no-op when session has no messages."""
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    memory_store = MemoryStore(mem_dir)

    loop = make_loop(memory_store=memory_store)
    await loop.consolidate_on_startup("cli:test")

    # No provider call made, no session file written
    assert len(mock_provider.calls) == 0


@pytest.mark.asyncio
async def test_error_stop_reason(make_loop, mock_provider: MockProvider) -> None:
    """Provider returns stop_reason='error' → error message returned."""
    mock_provider.queue(make_error_response())
    loop = make_loop()
    result = await loop.process_direct("cause error", "cli:test")
    assert "error" in result.lower()


@pytest.mark.asyncio
async def test_error_stop_reason_prefers_provider_error_content(
    make_loop, mock_provider: MockProvider,
) -> None:
    mock_provider.queue(
        LLMResponse(
            content="Error calling Codex (ReadTimeout): ReadTimeout('')",
            tool_calls=[],
            stop_reason="error",
            input_tokens=0,
            output_tokens=0,
        )
    )
    loop = make_loop()
    result = await loop.process_direct("cause error", "cli:test")
    assert "ReadTimeout" in result


@pytest.mark.asyncio
async def test_dispatch_publishes_error_msg_type_on_error_stop_reason(
    make_loop, mock_provider: MockProvider
) -> None:
    """stop_reason='error' publishes an outbound error event for UI status."""
    bus = MessageBus()
    outbound_q = bus.subscribe_outbound("test")
    mock_provider.queue(make_error_response())
    loop = make_loop(bus=bus, agent_id="proj:test")

    await loop._dispatch(InboundMessage(channel="web", chat_id="c1", text="cause error"))

    out = await asyncio.wait_for(outbound_q.get(), timeout=0.2)
    assert out.msg_type == "error"
    assert out.source == "proj:test"
    assert "error" in out.text.lower()


@pytest.mark.asyncio
async def test_dispatch_publishes_error_msg_when_provider_raises(
    sessions_dir: Path,
) -> None:
    """Unexpected provider exceptions still emit an outbound error event."""

    class _RaisingProvider(MockProvider):
        async def complete(self, messages, tools=None, system=None):  # noqa: ANN001
            raise RuntimeError("model api out of budget")

    bus = MessageBus()
    outbound_q = bus.subscribe_outbound("test")
    loop = AgentLoop(
        provider=_RaisingProvider(),
        tool_registry=ToolRegistry(),
        context_builder=ContextBuilder("You are a test agent."),
        session_manager=SessionManager(sessions_dir),
        bus=bus,
        agent_id="proj:test",
    )

    with pytest.raises(RuntimeError, match="out of budget"):
        await loop._dispatch(InboundMessage(channel="web", chat_id="c1", text="hello"))

    out = await asyncio.wait_for(outbound_q.get(), timeout=0.2)
    assert out.msg_type == "error"
    assert out.source == "proj:test"
    assert "out of budget" in out.text


@pytest.mark.asyncio
async def test_process_direct_saves_session(
    make_loop, mock_provider: MockProvider, sessions_dir: Path
) -> None:
    """After process_direct, session file exists with messages."""
    mock_provider.queue(make_text_response("saved"))
    loop = make_loop()
    await loop.process_direct("persist me", "cli:test")

    session = loop.session_manager.load("cli:test")
    assert len(session.messages) > 0
    roles = [m["role"] for m in session.messages]
    assert "user" in roles
    assert "assistant" in roles
    assert all(
        isinstance(m.get("source"), str) and bool(str(m.get("source")).strip())
        for m in session.messages
    )


@pytest.mark.asyncio
async def test_bus_run_dispatch(make_loop, mock_provider: MockProvider) -> None:
    """Inbound message via bus → outbound response published."""
    bus = MessageBus()
    mock_provider.queue(make_text_response("bus reply"))
    loop = make_loop(bus=bus)

    await bus.publish_inbound(InboundMessage(channel="test", chat_id="42", text="hello bus"))

    # Run the loop in a task, let it process one message, then stop
    async def run_and_stop():
        task = asyncio.create_task(loop.run())
        out = await asyncio.wait_for(bus.consume_outbound(), timeout=5.0)
        loop.stop()
        await task
        return out

    out = await run_and_stop()
    assert out.text == "bus reply"
    assert out.channel == "test"
    assert out.chat_id == "42"


@pytest.mark.asyncio
async def test_run_without_bus_raises(make_loop) -> None:
    """run() without a bus raises RuntimeError, not AssertionError."""
    loop = make_loop()
    with pytest.raises(RuntimeError, match="MessageBus required"):
        await loop.run()


# ---------------------------------------------------------------------------
# Debug logger integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debug_logger_records_loop(
    make_loop, mock_provider: MockProvider, tmp_path: Path
) -> None:
    """DebugLogger attached to AgentLoop records request + response entries."""
    log_path = tmp_path / "debug.jsonl"
    dl = DebugLogger(log_path)
    dl.log_session_start("cli:test", "test-model")

    mock_provider.queue(make_text_response("Hi!"))
    loop = make_loop()
    loop.debug_logger = dl
    await loop.process_direct("hello", "cli:test")
    dl.close()

    entries = read_jsonl_entries(log_path)
    types = [e["type"] for e in entries]
    assert "session_start" in types
    assert "llm_request" in types
    assert "llm_response" in types
    assert "session_end" in types

    resp = next(e for e in entries if e["type"] == "llm_response")
    assert resp["agent_id"] == "main"
    assert resp["session_key"] == "cli:test"
    assert resp["content"] == "Hi!"


@pytest.mark.asyncio
async def test_debug_logger_records_tool_calls(
    make_loop, mock_provider: MockProvider, tmp_path: Path
) -> None:
    """DebugLogger records tool_exec entries when tools are called."""
    log_path = tmp_path / "debug.jsonl"
    dl = DebugLogger(log_path)

    registry = ToolRegistry()
    registry.register(_EchoTool())

    mock_provider.queue(
        make_tool_response("echo", {"text": "ping"}),
        make_text_response("Done!"),
    )

    loop = make_loop(registry=registry)
    loop.debug_logger = dl
    await loop.process_direct("echo ping", "cli:test")
    dl.close()

    entries = read_jsonl_entries(log_path)
    types = [e["type"] for e in entries]

    # request, response(tool_use), tool_exec, request, response(end_turn), session_end
    assert types.count("llm_request") == 2
    assert types.count("llm_response") == 2
    assert "tool_exec" in types

    te = next(e for e in entries if e["type"] == "tool_exec")
    assert te["agent_id"] == "main"
    assert te["session_key"] == "cli:test"
    assert te["tool_name"] == "echo"
    assert te["arguments"] == {"text": "ping"}
    assert te["result"] == "ping"


# ---------------------------------------------------------------------------
# max_history tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_history_limits_context(make_loop, mock_provider: MockProvider) -> None:
    """With max_history=10, only ~10 history messages are passed to the LLM."""
    mock_provider.queue(make_text_response("limited-reply"))
    loop = make_loop(max_history=10)

    # Pre-populate session with 100 messages (50 user + 50 assistant).
    # Use a prefix that won't collide with the current turn's content.
    session = loop.session_manager.load("cli:test")
    for i in range(50):
        session.messages.append({"role": "user", "content": f"hist-u-{i}"})
        session.messages.append({"role": "assistant", "content": f"hist-a-{i}"})
    loop.session_manager.save(session)

    await loop.process_direct("current-turn-question", "cli:test")

    # build_messages returns: [system, *history, runtime_context, user_message]
    # The mock captures messages by reference, so the assistant response from
    # _run_agent_loop is also present. Count only the "hist-" prefixed messages
    # to isolate the history portion.
    assert len(mock_provider.calls) == 1
    sent_messages = mock_provider.calls[0]["messages"]
    history_msgs = [m for m in sent_messages if str(m.get("content", "")).startswith("hist-")]
    assert len(history_msgs) == 10


@pytest.mark.asyncio
async def test_max_history_none_passes_all(make_loop, mock_provider: MockProvider) -> None:
    """With max_history=None (default), all history messages are passed."""
    mock_provider.queue(make_text_response("unlimited-reply"))
    loop = make_loop(max_history=None)

    # Pre-populate session with 20 messages
    session = loop.session_manager.load("cli:test")
    for i in range(10):
        session.messages.append({"role": "user", "content": f"hist-u-{i}"})
        session.messages.append({"role": "assistant", "content": f"hist-a-{i}"})
    loop.session_manager.save(session)

    await loop.process_direct("current-turn-question", "cli:test")

    assert len(mock_provider.calls) == 1
    sent_messages = mock_provider.calls[0]["messages"]
    history_msgs = [m for m in sent_messages if str(m.get("content", "")).startswith("hist-")]
    # All 20 history messages
    assert len(history_msgs) == 20


# ---------------------------------------------------------------------------
# Topic awareness & hop count tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_loop_consumes_own_topic(make_loop, mock_provider: MockProvider) -> None:
    """Loop with agent_id='proj:x' only sees messages on that topic."""
    bus = MessageBus()
    mock_provider.queue(make_text_response("proj reply"))
    loop = make_loop(bus=bus, agent_id="proj:x")

    # Publish to a different topic — should NOT be consumed by proj:x
    await bus.publish_inbound(
        InboundMessage(channel="cli", chat_id="99", text="wrong"), topic="other"
    )

    async def run_and_stop():
        task = asyncio.create_task(loop.run())
        # Publish after loop starts so auto-subscribe has happened
        await asyncio.sleep(0.01)
        await bus.publish_inbound(
            InboundMessage(channel="cli", chat_id="42", text="hello"), topic="proj:x"
        )
        out = await asyncio.wait_for(bus.consume_outbound(), timeout=5.0)
        loop.stop()
        await task
        return out

    out = await run_and_stop()
    assert out.text == "proj reply"
    assert out.chat_id == "42"


@pytest.mark.asyncio
async def test_outbound_includes_source(make_loop, mock_provider: MockProvider) -> None:
    """Outbound message has source=agent_id."""
    bus = MessageBus()
    mock_provider.queue(make_text_response("tagged"))
    loop = make_loop(bus=bus, agent_id="proj:abc")

    async def run_and_stop():
        task = asyncio.create_task(loop.run())
        await asyncio.sleep(0.01)
        await bus.publish_inbound(
            InboundMessage(channel="cli", chat_id="1", text="hi"), topic="proj:abc"
        )
        out = await asyncio.wait_for(bus.consume_outbound(), timeout=5.0)
        loop.stop()
        await task
        return out

    out = await run_and_stop()
    assert out.source == "proj:abc"


@pytest.mark.asyncio
async def test_delegated_message_reports_back_to_main(
    make_loop, mock_provider: MockProvider
) -> None:
    """Delegated student work reports to main as inbound, not outbound."""
    bus = MessageBus()
    mock_provider.queue(make_text_response("research complete"))
    loop = make_loop(bus=bus, agent_id="proj:x")

    delegated = InboundMessage(
        channel="web",
        chat_id="chat-1",
        text="do research",
        source="main",
        hop_count=1,
        metadata={
            "delegated": True,
            "delegated_by": "main",
            "reply_to_topic": "main",
            "reply_channel": "web",
            "reply_chat_id": "chat-1",
        },
    )
    await loop._dispatch(delegated)

    callback = await asyncio.wait_for(bus.consume_inbound("main"), timeout=1.0)
    assert callback.channel == "web"
    assert callback.chat_id == "chat-1"
    assert callback.source == "proj:x"
    assert callback.hop_count == 2
    assert callback.metadata.get("delegated_result") is True
    assert "Student report from proj:x:" in callback.text
    assert "research complete" in callback.text

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(bus.consume_outbound(), timeout=0.1)


@pytest.mark.asyncio
async def test_session_key_defaults_to_agent_id(make_loop) -> None:
    """session_key defaults to agent_id; explicit override takes precedence."""
    loop = make_loop(agent_id="main")
    assert loop.session_key == "main"

    loop2 = make_loop(agent_id="proj:x")
    assert loop2.session_key == "proj:x"

    # Explicit session_key overrides agent_id
    loop3 = AgentLoop(
        provider=loop.provider,
        tool_registry=ToolRegistry(),
        context_builder=ContextBuilder("test"),
        session_manager=loop.session_manager,
        agent_id="proj:y",
        session_key="custom-key",
    )
    assert loop3.session_key == "custom-key"

    # session_key tracks agent_id when mutated post-construction (registry pattern)
    loop4 = make_loop(agent_id="main")
    assert loop4.session_key == "main"
    loop4.agent_id = "proj:z"
    assert loop4.session_key == "proj:z"


@pytest.mark.asyncio
async def test_dispatch_uses_agent_session_key(make_loop, mock_provider: MockProvider) -> None:
    """_dispatch stores session under self.session_key, not msg.session_key."""
    bus = MessageBus()
    mock_provider.queue(make_text_response("reply"))
    loop = make_loop(bus=bus, agent_id="proj:x")

    # Inbound message has a different session_key (telegram:123)
    msg = InboundMessage(channel="telegram", chat_id="123", text="hello")
    await loop._dispatch(msg)

    # Session should be stored under the agent's session_key, not the message's
    session = loop.session_manager.load("proj:x")
    assert len(session.messages) > 0

    # The message's own session_key should NOT have a session
    msg_session = loop.session_manager.load("telegram:123")
    assert len(msg_session.messages) == 0


@pytest.mark.asyncio
async def test_hop_count_exceeded_rejected(make_loop, mock_provider: MockProvider) -> None:
    """Message with hop_count >= MAX_HOPS is rejected without processing."""
    bus = MessageBus()
    loop = make_loop(bus=bus, agent_id="main")

    msg = InboundMessage(channel="cli", chat_id="1", text="looping", hop_count=MAX_HOPS)
    await loop._dispatch(msg)

    out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
    assert "hop count" in out.text
    assert str(MAX_HOPS) in out.text
    # Provider should NOT have been called
    assert len(mock_provider.calls) == 0
