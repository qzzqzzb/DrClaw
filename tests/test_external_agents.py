"""Tests for external-agent bridge and tool integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from drclaw.bus.queue import MessageBus
from drclaw.config.schema import DrClawConfig, ExternalAgentConfig
from drclaw.external_agents.bridge import ExternalAgentBridge
from drclaw.tools.external_agent_tools import CallExternalAgentTool


class _FakeResponse:
    def __init__(self, status: int = 202, body: str = "") -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body


class _FakeRequestContext:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        return False


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict] = []

    def post(self, url: str, *, json: dict, timeout) -> _FakeRequestContext:  # noqa: ANN001
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return _FakeRequestContext(self._response)


def _config(tmp_path: Path) -> DrClawConfig:
    return DrClawConfig(
        data_dir=str(tmp_path),
        external_agents=[
            ExternalAgentConfig(
                id="chem",
                label="Chem External",
                request_url="http://127.0.0.1:9010/request",
                callback_timeout_seconds=30,
            )
        ],
    )


def test_bridge_lists_external_agents(tmp_path: Path):
    bus = MessageBus()
    bridge = ExternalAgentBridge(_config(tmp_path), bus, session=MagicMock())
    rows = bridge.list_agent_payloads()
    assert len(rows) == 1
    assert rows[0]["id"] == "ext:chem"
    assert rows[0]["type"] == "external"


@pytest.mark.asyncio
async def test_submit_web_then_callback_routes_outbound(tmp_path: Path):
    bus = MessageBus()
    session = _FakeSession(_FakeResponse(status=202))
    bridge = ExternalAgentBridge(_config(tmp_path), bus, session=session)

    req_id = await bridge.submit_from_web(
        agent_id="ext:chem",
        text="hello",
        metadata={"foo": "bar"},
        chat_id="chat1",
    )
    assert req_id
    assert len(session.calls) == 1
    assert session.calls[0]["json"]["agent_id"] == "ext:chem"
    assert session.calls[0]["json"]["text"] == "hello"

    status, body = await bridge.handle_callback(
        {
            "request_id": req_id,
            "agent_id": "ext:chem",
            "text": "external reply",
            "metadata": {"m": 1},
        }
    )
    assert status == 202
    assert body["ok"] is True

    outbound = await bus.consume_outbound()
    assert outbound.msg_type == "chat"
    assert outbound.source == "ext:chem"
    assert outbound.topic == "ext:chem"
    assert outbound.text == "external reply"


@pytest.mark.asyncio
async def test_submit_agent_then_callback_routes_inbound(tmp_path: Path):
    bus = MessageBus()
    session = _FakeSession(_FakeResponse(status=202))
    bridge = ExternalAgentBridge(_config(tmp_path), bus, session=session)

    req_id = await bridge.submit_from_agent(
        agent_id="ext:chem",
        text="do work",
        metadata={},
        origin_topic="main",
    )
    status, _ = await bridge.handle_callback(
        {
            "request_id": req_id,
            "agent_id": "ext:chem",
            "text": "done",
        }
    )
    assert status == 202

    inbound = await bus.consume_inbound("main")
    assert inbound.source == "ext:chem"
    assert inbound.text == "done"
    assert inbound.topic == "main"


@pytest.mark.asyncio
async def test_callback_unknown_request(tmp_path: Path):
    bus = MessageBus()
    bridge = ExternalAgentBridge(_config(tmp_path), bus, session=MagicMock())
    status, body = await bridge.handle_callback({"request_id": "missing", "text": "x"})
    assert status == 404
    assert "Unknown request_id" in body["error"]


@pytest.mark.asyncio
async def test_call_external_agent_tool_executes_bridge():
    bridge = MagicMock(spec=ExternalAgentBridge)
    bridge.is_external_agent.return_value = True
    bridge.submit_from_agent = AsyncMock(return_value="r1")

    tool = CallExternalAgentTool(bridge, caller_agent_id="main")
    result = await tool.execute(
        {
            "agent_id": "ext:chem",
            "message": "hello",
            "metadata": {"lang": "en"},
        }
    )
    assert "Queued external request r1" in result
    bridge.submit_from_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_external_agent_tool_rejects_unknown_agent():
    bridge = MagicMock(spec=ExternalAgentBridge)
    bridge.is_external_agent.return_value = False
    tool = CallExternalAgentTool(bridge, caller_agent_id="main")
    result = await tool.execute({"agent_id": "ext:missing", "message": "hello"})
    assert result.startswith("Error:")
