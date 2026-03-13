"""Tests for MessageTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from drclaw.models.messages import OutboundMessage
from drclaw.tools.message import MessageTool


@pytest.mark.asyncio
async def test_message_tool_uses_context_defaults(tmp_path: Path) -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send, workspace=tmp_path, allowed_dir=tmp_path)
    tool.set_context("feishu", "oc_group")
    tool.start_turn()

    result = await tool.execute({"content": "hello"})
    assert result == "Message sent to feishu:oc_group"
    assert len(sent) == 1
    assert sent[0].channel == "feishu"
    assert sent[0].chat_id == "oc_group"
    assert sent[0].text == "hello"
    assert tool.sent_in_turn_for("feishu", "oc_group") is True


@pytest.mark.asyncio
async def test_message_tool_media_path_outside_allowed_is_rejected(tmp_path: Path) -> None:
    async def _send(_: OutboundMessage) -> None:
        return

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("bad", encoding="utf-8")

    tool = MessageTool(send_callback=_send, workspace=allowed, allowed_dir=allowed)
    tool.set_context("feishu", "oc_group")
    tool.start_turn()

    result = await tool.execute({"content": "x", "media": [str(outside)]})
    assert result.startswith("Error: ")
    assert "outside allowed directory" in result


@pytest.mark.asyncio
async def test_message_tool_missing_media_file_is_rejected(tmp_path: Path) -> None:
    async def _send(_: OutboundMessage) -> None:
        return

    tool = MessageTool(send_callback=_send, workspace=tmp_path, allowed_dir=tmp_path)
    tool.set_context("feishu", "oc_group")
    tool.start_turn()

    result = await tool.execute({"content": "x", "media": ["missing.png"]})
    assert result == "Error: Media file not found: missing.png"


@pytest.mark.asyncio
async def test_message_tool_tracks_and_resets_turn_state(tmp_path: Path) -> None:
    async def _send(_: OutboundMessage) -> None:
        return

    tool = MessageTool(send_callback=_send, workspace=tmp_path, allowed_dir=tmp_path)
    tool.set_context("feishu", "oc_group")
    tool.start_turn()
    await tool.execute({"content": "first"})
    assert tool.sent_in_turn_for("feishu", "oc_group") is True

    tool.start_turn()
    assert tool.sent_in_turn_for("feishu", "oc_group") is False
