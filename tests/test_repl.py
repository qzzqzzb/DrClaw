"""Tests for the interactive REPL."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from drclaw.agent.registry import AgentRegistry, AgentStatus
from drclaw.bus.queue import MessageBus
from drclaw.cli.repl import (
    EXIT_COMMANDS,
    _handle_command,
    _is_exit_command,
    _parse_mention,
    _parse_target,
    run_repl,
)
from drclaw.config.schema import DrClawConfig
from drclaw.models.messages import InboundMessage, OutboundMessage
from drclaw.models.project import JsonProjectStore
from tests.mocks import MockProvider, make_project

# ---------------------------------------------------------------------------
# _is_exit_command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cmd", list(EXIT_COMMANDS))
def test_is_exit_command_recognizes_all(cmd: str) -> None:
    assert _is_exit_command(cmd) is True


@pytest.mark.parametrize("cmd", ["EXIT", "Quit", "/QUIT", ":Q"])
def test_is_exit_command_case_insensitive(cmd: str) -> None:
    assert _is_exit_command(cmd) is True


@pytest.mark.parametrize("cmd", ["hello", "e x i t", "quitting", ""])
def test_is_exit_command_rejects_non_exit(cmd: str) -> None:
    assert _is_exit_command(cmd) is False


# ---------------------------------------------------------------------------
# _parse_mention
# ---------------------------------------------------------------------------


def test_parse_mention_with_project_and_message() -> None:
    name, rest = _parse_mention("@report generate weekly summary")
    assert name == "report"
    assert rest == "generate weekly summary"


def test_parse_mention_with_project_only() -> None:
    name, rest = _parse_mention("@report")
    assert name == "report"
    assert rest == ""


def test_parse_mention_no_mention() -> None:
    name, rest = _parse_mention("hello world")
    assert name is None
    assert rest == "hello world"


def test_parse_mention_empty_at() -> None:
    name, rest = _parse_mention("@ something")
    assert name is None
    assert rest == "@ something"


def test_parse_mention_with_leading_whitespace() -> None:
    name, rest = _parse_mention("  @proj  hi there")
    assert name == "proj"
    assert rest == "hi there"


# ---------------------------------------------------------------------------
# _parse_target
# ---------------------------------------------------------------------------


def _patch_loop_run():
    """Patch AgentLoop.run to be a no-op coroutine."""
    async def _fake_run(self):  # noqa: ANN001
        pass
    return patch("drclaw.agent.loop.AgentLoop.run", _fake_run)


@pytest.mark.asyncio
async def test_parse_target_no_mention(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    bus = MessageBus()
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    store = JsonProjectStore(config.data_path)

    agent_id, text = await _parse_target("hello world", registry, store)
    assert agent_id == "main"
    assert text == "hello world"


@pytest.mark.asyncio
async def test_parse_target_running_agent(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    bus = MessageBus()
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    store = JsonProjectStore(config.data_path)
    project = store.create_project("Report", "weekly reports")

    with _patch_loop_run():
        registry.spawn_project(project, interactive=True)

    agent_id, text = await _parse_target("@Report hi there", registry, store)
    assert agent_id == f"proj:{project.id}"
    assert text == "hi there"


@pytest.mark.asyncio
async def test_parse_target_auto_spawns(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    bus = MessageBus()
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    store = JsonProjectStore(config.data_path)
    project = store.create_project("Report", "weekly")

    with _patch_loop_run():
        agent_id, text = await _parse_target("@Report hi", registry, store)

    assert agent_id == f"proj:{project.id}"
    assert text == "hi"
    # Agent was spawned
    assert registry.get(f"proj:{project.id}") is not None


@pytest.mark.asyncio
async def test_parse_target_unknown(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    bus = MessageBus()
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    store = JsonProjectStore(config.data_path)

    agent_id, text = await _parse_target("@nonexistent hi", registry, store)
    assert agent_id is None
    assert "not found" in text.lower()


# ---------------------------------------------------------------------------
# _handle_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slash_agents_command(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    bus = MessageBus()
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)

    with _patch_loop_run():
        registry.start_main()

    con = MagicMock()
    handled = await _handle_command("/agents", registry, con)
    assert handled is True
    con.print.assert_called_once()


@pytest.mark.asyncio
async def test_slash_stop_command(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    bus = MessageBus()
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)
    project = make_project("p1", "Report")

    async def _hang(self):  # noqa: ANN001
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    with patch("drclaw.agent.loop.AgentLoop.run", _hang):
        registry.spawn_project(project, interactive=True)

        con = MagicMock()
        handled = await _handle_command("/stop Report", registry, con)

    assert handled is True
    handle = registry.get("proj:p1")
    assert handle is not None
    assert handle.status == AgentStatus.DONE


@pytest.mark.asyncio
async def test_slash_stop_main_disallowed(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    bus = MessageBus()
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)

    con = MagicMock()
    handled = await _handle_command("/stop main", registry, con)
    assert handled is True
    # Error about not stopping main was printed
    error_calls = [str(c) for c in con.print.call_args_list if "Cannot stop" in str(c)]
    assert len(error_calls) > 0


# ---------------------------------------------------------------------------
# run_repl — helpers
# ---------------------------------------------------------------------------


def _make_mock_registry(bus: MessageBus, config: DrClawConfig) -> AgentRegistry:
    """Build an AgentRegistry with a mocked main agent loop."""
    provider = MockProvider()
    registry = AgentRegistry(config, provider, bus)

    # Create a mock handle with a fake loop
    mock_loop = MagicMock()
    mock_loop.stop = MagicMock()
    mock_loop.agent_id = "main"
    mock_loop.on_tool_call = None

    async def fake_run() -> None:
        while True:
            try:
                msg = await asyncio.wait_for(
                    bus.consume_inbound("main"), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            await bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    text="mock reply", source="main",
                )
            )

    mock_loop.run = AsyncMock(side_effect=fake_run)
    mock_loop.consolidate_on_startup = AsyncMock()

    from drclaw.agent.registry import AgentHandle
    handle = AgentHandle(
        agent_id="main",
        agent_type="main",
        label="Assistant Agent",
        project=None,
        loop=mock_loop,
        task=asyncio.create_task(fake_run()),
    )
    registry._handles["main"] = handle
    bus.subscribe("main")

    return registry


@contextmanager
def _repl_patches(prompt_fn):  # noqa: ANN001
    """Patch REPL internals and wire *prompt_fn* as the prompt callback."""
    with (
        patch("drclaw.cli.repl._save_terminal", return_value=None),
        patch("drclaw.cli.repl._restore_terminal"),
        patch("drclaw.cli.repl._flush_input"),
        patch("drclaw.cli.repl.PromptSession") as mock_sess,
        patch("drclaw.cli.repl.patch_stdout"),
        patch("drclaw.cli.repl.console") as mock_con,
    ):
        mock_sess.return_value.prompt_async = prompt_fn
        yield mock_con


# ---------------------------------------------------------------------------
# run_repl — turn roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repl_turn_roundtrip(tmp_path: Path) -> None:
    """User input -> bus -> mock agent -> response displayed."""
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    bus = MessageBus()
    registry = _make_mock_registry(bus, config)
    store = JsonProjectStore(config.data_path)

    inputs = iter(["hello", "exit"])

    async def fake_prompt(*_a, **_kw):  # noqa: ANN002, ANN003
        return next(inputs)

    with _repl_patches(fake_prompt):
        await run_repl(registry, bus, store)


# ---------------------------------------------------------------------------
# run_repl — shutdown on KeyboardInterrupt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repl_shutdown_on_interrupt(tmp_path: Path) -> None:
    """Ctrl+C triggers clean shutdown."""
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    bus = MessageBus()
    registry = _make_mock_registry(bus, config)
    store = JsonProjectStore(config.data_path)

    async def raise_interrupt(*_a, **_kw):  # noqa: ANN002, ANN003
        raise KeyboardInterrupt

    with _repl_patches(raise_interrupt):
        await run_repl(registry, bus, store)


# ---------------------------------------------------------------------------
# run_repl — empty input skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repl_skips_empty_input(tmp_path: Path) -> None:
    """Empty lines are ignored, no message published to bus."""
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    bus = MessageBus()
    registry = _make_mock_registry(bus, config)
    store = JsonProjectStore(config.data_path)

    inputs = iter(["", "  ", "exit"])

    async def fake_prompt(*_a, **_kw):  # noqa: ANN002, ANN003
        return next(inputs)

    published: list[str] = []
    original_publish = bus.publish_inbound

    async def tracking_publish(msg, topic="main"):  # noqa: ANN001
        published.append(msg.text)
        await original_publish(msg, topic)

    bus.publish_inbound = tracking_publish  # type: ignore[assignment]

    with _repl_patches(fake_prompt):
        await run_repl(registry, bus, store)

    assert published == []


# ---------------------------------------------------------------------------
# run_repl — @mention routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repl_mention_routes_to_project(tmp_path: Path) -> None:
    """@project_name routes directly to a ProjectAgent via bus topic."""
    from tests.mocks import make_text_response

    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    provider = MockProvider()
    store = JsonProjectStore(config.data_path)
    project = store.create_project("Report", "weekly reports")

    provider.queue(make_text_response("Report agent says hello!"))

    bus = MessageBus()
    registry = _make_mock_registry(bus, config)

    inputs = iter(["@report hi", "exit"])

    async def fake_prompt(*_a, **_kw):  # noqa: ANN002, ANN003
        return next(inputs)

    published_topics: list[str] = []
    original_publish = bus.publish_inbound

    async def tracking_publish(msg: InboundMessage, topic: str = "main") -> None:
        published_topics.append(topic)
        await original_publish(msg, topic)

    bus.publish_inbound = tracking_publish  # type: ignore[assignment]

    with _repl_patches(fake_prompt):
        await run_repl(registry, bus, store, data_dir=config.data_path)

    assert len(published_topics) == 1
    assert published_topics[0] == f"proj:{project.id}"


@pytest.mark.asyncio
async def test_repl_mention_unknown_prints_error(tmp_path: Path) -> None:
    """@nonexistent prints error and re-prompts."""
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    bus = MessageBus()
    registry = _make_mock_registry(bus, config)
    store = JsonProjectStore(config.data_path)

    inputs = iter(["@nonexistent hi", "exit"])

    async def fake_prompt(*_a, **_kw):  # noqa: ANN002, ANN003
        return next(inputs)

    with _repl_patches(fake_prompt) as mock_con:
        await run_repl(registry, bus, store)

    error_calls = [
        str(c) for c in mock_con.print.call_args_list
        if "not found" in str(c).lower() or "error" in str(c).lower()
    ]
    assert len(error_calls) > 0
