"""Unit tests for the web frontend adapter."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from drclaw.config.schema import DrClawConfig
from drclaw.daemon.frontend import FrontendAdapter
from drclaw.frontends.web.adapter import (
    WebAdapter,
    _docker_mode_enabled,
    _is_allowed_origin,
    _is_allowed_peer,
    _is_loopback_host,
    _is_loopback_peer,
)
from drclaw.models.messages import InboundMessage, OutboundMessage

# -- Protocol compliance -------------------------------------------------------


def test_web_adapter_implements_frontend_protocol():
    adapter = WebAdapter()
    assert isinstance(adapter, FrontendAdapter)


def test_adapter_id():
    assert WebAdapter.adapter_id == "web"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_web_adapter_accepts_loopback_bind_hosts(host: str):
    assert WebAdapter(host=host).host == host


def test_web_adapter_rejects_non_loopback_bind_host():
    with pytest.raises(ValueError, match="loopback"):
        WebAdapter(host="0.0.0.0")


def test_web_adapter_uses_docker_bind_host_when_enabled():
    adapter = WebAdapter(docker_mode=True)
    assert adapter.bind_host == "0.0.0.0"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]"])
def test_is_loopback_host_accepts_local_values(host: str):
    assert _is_loopback_host(host) is True


@pytest.mark.parametrize("host", ["192.168.1.10", "example.com", ""])
def test_is_loopback_host_rejects_remote_values(host: str):
    assert _is_loopback_host(host) is False


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", None])
def test_is_loopback_peer(peer: str | None):
    assert _is_loopback_peer(peer) is (peer is not None)


@pytest.mark.parametrize(
    ("peer", "docker_mode", "expected"),
    [
        ("127.0.0.1", False, True),
        ("192.168.1.10", False, False),
        ("192.168.1.10", True, True),
        (None, True, False),
    ],
)
def test_is_allowed_peer(peer: str | None, docker_mode: bool, expected: bool):
    assert _is_allowed_peer(peer, docker_mode=docker_mode) is expected


def test_docker_mode_enabled_reads_adapter_flag():
    request = MagicMock()
    request.app = {"adapter": WebAdapter(docker_mode=True)}
    assert _docker_mode_enabled(request) is True


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("http://127.0.0.1:5173", True),
        ("https://localhost:8080", True),
        ("tauri://localhost", True),
        ("http://192.168.1.10:8080", False),
        ("https://example.com", False),
        ("null", False),
    ],
)
def test_allowed_origin_is_loopback_only(origin: str, expected: bool):
    assert _is_allowed_origin(origin) is expected


# -- drain_outbound filtering -------------------------------------------------


@pytest.fixture
def mock_kernel(tmp_path):
    from drclaw.bus.queue import MessageBus

    kernel = MagicMock()
    kernel.bus = MessageBus()
    kernel.config = DrClawConfig(data_dir=str(tmp_path))
    kernel.registry = MagicMock()
    kernel.registry.list_agents.return_value = []
    return kernel


@pytest.mark.asyncio
async def test_drain_outbound_broadcasts_all_channels(mock_kernel):
    adapter = WebAdapter()
    adapter._kernel = mock_kernel

    # Create a mock WS
    ws = AsyncMock()
    ws.closed = False
    adapter.register_connection(ws)

    # Start drain task and let it reach the await
    task = asyncio.create_task(adapter._drain_outbound())
    await asyncio.sleep(0)

    # Publish a CLI message — should still be broadcast to all connections
    await mock_kernel.bus.publish_outbound(
        OutboundMessage(channel="cli", chat_id="xxx", text="from-cli", source="proj:report")
    )
    await asyncio.sleep(0)

    # Publish a web message
    await mock_kernel.bus.publish_outbound(
        OutboundMessage(channel="web", chat_id="yyy", text="from-web", source="main")
    )

    # Give drain loop time to process
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert ws.send_json.call_count == 2
    payloads = [call[0][0] for call in ws.send_json.call_args_list]
    assert payloads[0]["text"] == "from-cli"
    assert payloads[0]["source"] == "proj:report"
    assert payloads[1]["text"] == "from-web"
    assert payloads[1]["source"] == "main"


@pytest.mark.asyncio
async def test_drain_outbound_includes_files_when_media_present(mock_kernel):
    adapter = WebAdapter()
    adapter._kernel = mock_kernel

    media_file = mock_kernel.config.data_path / "report.txt"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_text("hello media", encoding="utf-8")

    ws = AsyncMock()
    ws.closed = False
    adapter.register_connection(ws)

    task = asyncio.create_task(adapter._drain_outbound())
    await asyncio.sleep(0)

    await mock_kernel.bus.publish_outbound(
        OutboundMessage(
            channel="web",
            chat_id="main",
            text="with media",
            source="main",
            media=[str(media_file)],
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert ws.send_json.call_count == 1
    payload = ws.send_json.call_args[0][0]
    assert payload["text"] == "with media"
    assert isinstance(payload.get("files"), list)
    assert payload["files"][0]["name"] == "report.txt"
    assert payload["files"][0]["download_url"].startswith("/api/chat/files/")


@pytest.mark.asyncio
async def test_drain_outbound_skips_missing_connection(mock_kernel):
    adapter = WebAdapter()
    adapter._kernel = mock_kernel

    task = asyncio.create_task(adapter._drain_outbound())

    # Publish to a chat_id with no connection — should not crash
    await mock_kernel.bus.publish_outbound(
        OutboundMessage(channel="web", chat_id="ghost", text="boo", source="main")
    )
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_drain_outbound_skips_tool_call_when_verbose_chat_disabled(mock_kernel):
    mock_kernel.config.daemon.verbose_chat = False
    adapter = WebAdapter()
    adapter._kernel = mock_kernel

    ws = AsyncMock()
    ws.closed = False
    adapter.register_connection(ws)

    task = asyncio.create_task(adapter._drain_outbound())
    await asyncio.sleep(0)
    await mock_kernel.bus.publish_outbound(
        OutboundMessage(
            channel="web",
            chat_id="main",
            text='search({"q":"x"})',
            source="main",
            topic="main",
            msg_type="tool_call",
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    ws.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_drain_inbound_skips_cross_agent_when_verbose_chat_disabled(mock_kernel):
    mock_kernel.config.daemon.verbose_chat = False
    adapter = WebAdapter()
    adapter._kernel = mock_kernel

    ws = AsyncMock()
    ws.closed = False
    adapter.register_connection(ws)

    task = asyncio.create_task(adapter._drain_inbound())
    await asyncio.sleep(0)
    await mock_kernel.bus.publish_inbound(
        InboundMessage(
            channel="web",
            chat_id="main",
            text="Student report",
            source="proj:demo",
        ),
        topic="main",
    )
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    ws.send_json.assert_not_called()


# -- Connection management -----------------------------------------------------


def test_register_and_remove_connection():
    adapter = WebAdapter()
    ws = MagicMock()
    chat_id = adapter.register_connection(ws)
    assert chat_id in adapter._connections
    adapter.remove_connection(chat_id)
    assert chat_id not in adapter._connections


def test_remove_nonexistent_connection():
    adapter = WebAdapter()
    adapter.remove_connection("nope")  # should not raise
