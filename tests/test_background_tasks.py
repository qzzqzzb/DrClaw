"""Tests for background tool task tracking and control tools."""

from __future__ import annotations

import asyncio

import pytest

from drclaw.bus.queue import MessageBus
from drclaw.tools.background_tasks import (
    BackgroundToolTaskManager,
    CancelBackgroundToolTaskTool,
    GetBackgroundToolTaskStatusTool,
    ListBackgroundToolTasksTool,
)


@pytest.mark.asyncio
async def test_register_detached_publishes_events_and_callback() -> None:
    bus = MessageBus()
    outbound_q = bus.subscribe_outbound("test")
    manager = BackgroundToolTaskManager(caller_agent_id="main")

    async def _slow() -> str:
        await asyncio.sleep(0.05)
        return "done output"

    task = asyncio.create_task(_slow())
    rec = await manager.register_detached(
        task=task,
        tool_name="fake_tool",
        tool_args={"x": 1},
        channel="web",
        chat_id="chat1",
        bus=bus,
    )

    started = await asyncio.wait_for(outbound_q.get(), timeout=1.0)
    assert started.msg_type == "tool_task_started"
    assert started.metadata["task_id"] == rec.task_id

    completed = await asyncio.wait_for(outbound_q.get(), timeout=1.0)
    assert completed.msg_type == "tool_task_completed"
    assert completed.metadata["task_id"] == rec.task_id

    callback = await asyncio.wait_for(bus.consume_inbound("main"), timeout=1.0)
    assert callback.metadata["background_task_callback"] is True
    assert callback.metadata["task_id"] == rec.task_id

    done = manager.get(rec.task_id)
    assert done is not None
    assert done.status == "succeeded"
    assert done.result is not None


@pytest.mark.asyncio
async def test_background_task_tools_list_get_cancel() -> None:
    manager = BackgroundToolTaskManager(caller_agent_id="main")

    async def _slow() -> str:
        await asyncio.sleep(1.0)
        return "unreachable"

    rec = await manager.register_detached(
        task=asyncio.create_task(_slow()),
        tool_name="slow_tool",
        tool_args={"a": "b"},
        channel="web",
        chat_id="chat2",
    )

    list_tool = ListBackgroundToolTasksTool(manager)
    get_tool = GetBackgroundToolTaskStatusTool(manager)
    cancel_tool = CancelBackgroundToolTaskTool(manager)

    listed = await list_tool.execute({})
    assert rec.task_id in listed
    assert "slow_tool" in listed

    status = await get_tool.execute({"task_id": rec.task_id})
    assert f"task_id={rec.task_id}" in status
    assert "status=running" in status

    cancel = await cancel_tool.execute({"task_id": rec.task_id})
    assert rec.task_id in cancel

    # Let done-callback transition state to cancelled.
    await asyncio.sleep(0.05)
    status2 = await get_tool.execute({"task_id": rec.task_id})
    assert "status=cancelled" in status2
