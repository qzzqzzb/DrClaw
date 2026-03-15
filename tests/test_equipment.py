"""Tests for equipment prototype/runtime flow."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from drclaw.bus.queue import MessageBus
from drclaw.config.schema import DrClawConfig
from drclaw.equipment.manager import EquipmentRuntimeManager
from drclaw.equipment.prototypes import EquipmentPrototypeStore
from drclaw.tools.equipment_tools import UseEquipmentTool
from tests.mocks import MockProvider, make_text_response, make_tool_response


def _make_store(tmp_path: Path) -> EquipmentPrototypeStore:
    root = tmp_path / "equipments"
    skill_dir = root / "analyzer" / "skills" / "baseline"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: baseline\ndescription: baseline analyzer\n---\n\n# Baseline"
    )
    return EquipmentPrototypeStore(root)


@pytest.mark.asyncio
async def test_prototype_store_loads_valid_entries(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    p = store.get("analyzer")
    assert p is not None
    assert p.name == "analyzer"
    assert p.skills_dir.name == "skills"
    assert len(store.list()) == 1


@pytest.mark.asyncio
async def test_equipment_run_reports_success(tmp_path: Path) -> None:
    provider = MockProvider()
    provider.queue(
        make_tool_response(
            "update_status",
            {"status_message": "running baseline", "progress_pct": 25},
            "tc1",
        ),
        make_tool_response(
            "report_to_caller",
            {"status": "success", "summary": "Baseline completed."},
            "tc2",
        ),
        make_text_response("done"),
    )
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    manager = EquipmentRuntimeManager(
        prototype_store=_make_store(tmp_path),
        runtime_root=tmp_path / "runtime",
        provider=provider,
        config=config,
    )

    runtime = await manager.start_run(
        prototype_name="analyzer",
        instruction="Run a baseline experiment",
        caller_agent_id="proj:p1",
    )
    runtime = await manager.wait_for_terminal(runtime.runtime_id, timeout_seconds=5)

    assert runtime.status == "succeeded"
    assert runtime.summary == "Baseline completed."
    assert runtime.done_event.is_set()
    assert manager.get_by_request(runtime.request_id) is runtime
    assert runtime.workspace_path.is_dir()
    assert runtime.artifacts.get("workspace_path") == str(runtime.workspace_path)
    system_msg = provider.calls[0]["messages"][0]["content"]
    assert "If you hit an unrecoverable blocker" in system_msg
    assert "report_to_caller with failed status" in system_msg
    assert "default to uv" in system_msg
    assert "seek help from the user or caller" in system_msg


@pytest.mark.asyncio
async def test_equipment_run_without_report_fails(tmp_path: Path) -> None:
    provider = MockProvider()
    provider.queue(make_text_response("I am done"))  # no report_to_caller call
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    manager = EquipmentRuntimeManager(
        prototype_store=_make_store(tmp_path),
        runtime_root=tmp_path / "runtime",
        provider=provider,
        config=config,
    )

    runtime = await manager.start_run(
        prototype_name="analyzer",
        instruction="Do something quickly",
        caller_agent_id="main",
    )
    runtime = await manager.wait_for_terminal(runtime.runtime_id, timeout_seconds=5)
    assert runtime.status == "failed"
    assert "without calling report_to_caller" in runtime.summary


@pytest.mark.asyncio
async def test_use_equipment_tool_waits_for_result(tmp_path: Path) -> None:
    provider = MockProvider()
    provider.queue(
        make_tool_response(
            "report_to_caller",
            {
                "status": "success",
                "summary": "all set",
                "artifacts": {"report_path": "results/report.md"},
            },
            "tc1",
        ),
        make_text_response("done"),
    )
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    manager = EquipmentRuntimeManager(
        prototype_store=_make_store(tmp_path),
        runtime_root=tmp_path / "runtime",
        provider=provider,
        config=config,
    )
    tool = UseEquipmentTool(manager, caller_agent_id="proj:p2")
    result = await tool.execute(
        {
            "prototype_name": "analyzer",
            "instruction": "quick check",
            "await_result": True,
            "timeout_seconds": 5,
        }
    )
    assert "status=succeeded" in result
    assert "summary: all set" in result
    assert "workspace_path=" in result
    assert "artifacts:" in result
    assert "report_path" in result


@pytest.mark.asyncio
async def test_use_equipment_await_result_suppresses_completion_inbound(tmp_path: Path) -> None:
    provider = MockProvider()
    provider.queue(
        make_tool_response(
            "report_to_caller",
            {"status": "success", "summary": "all set"},
            "tc1",
        ),
        make_text_response("done"),
    )
    bus = MessageBus()
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    manager = EquipmentRuntimeManager(
        prototype_store=_make_store(tmp_path),
        runtime_root=tmp_path / "runtime",
        provider=provider,
        config=config,
        bus=bus,
    )
    tool = UseEquipmentTool(manager, caller_agent_id="proj:p2")
    result = await tool.execute(
        {
            "prototype_name": "analyzer",
            "instruction": "quick check",
            "await_result": True,
            "timeout_seconds": 5,
        }
    )
    assert "status=succeeded" in result

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(bus.consume_inbound("proj:p2"), timeout=0.1)


@pytest.mark.asyncio
async def test_use_equipment_async_notifies_caller_inbound(tmp_path: Path) -> None:
    provider = MockProvider()
    provider.queue(
        make_tool_response(
            "report_to_caller",
            {
                "status": "success",
                "summary": "all set",
                "artifacts": {"report_path": "results/report.md"},
            },
            "tc1",
        ),
        make_text_response("done"),
    )
    bus = MessageBus()
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    manager = EquipmentRuntimeManager(
        prototype_store=_make_store(tmp_path),
        runtime_root=tmp_path / "runtime",
        provider=provider,
        config=config,
        bus=bus,
    )
    tool = UseEquipmentTool(manager, caller_agent_id="proj:p4")
    started = await tool.execute(
        {
            "prototype_name": "analyzer",
            "instruction": "quick check",
            "await_result": False,
        }
    )
    assert "Started equipment 'analyzer'" in started

    inbound = await asyncio.wait_for(bus.consume_inbound("proj:p4"), timeout=1.0)
    assert inbound.source.startswith("equip:analyzer:")
    assert "succeeded" in inbound.text
    assert "artifacts:" in inbound.text
    assert "report_path" in inbound.text
    assert "workspace_path" in inbound.metadata
    assert inbound.metadata["artifacts"]["report_path"] == "results/report.md"


@pytest.mark.asyncio
async def test_use_equipment_timeout_returns_active_runtime_hint(tmp_path: Path) -> None:
    provider = MockProvider()
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    manager = EquipmentRuntimeManager(
        prototype_store=_make_store(tmp_path),
        runtime_root=tmp_path / "runtime",
        provider=provider,
        config=config,
    )
    _ = await manager.start_run(
        prototype_name="analyzer",
        instruction="long run",
        caller_agent_id="proj:p5",
    )
    manager.wait_for_terminal = AsyncMock(side_effect=asyncio.TimeoutError())  # type: ignore[method-assign]

    tool = UseEquipmentTool(manager, caller_agent_id="proj:p5")
    result = await tool.execute(
        {
            "prototype_name": "analyzer",
            "instruction": "long run",
            "await_result": True,
            "timeout_seconds": 1,
        }
    )

    assert "timed out" in result
    assert "request_id=" in result
    assert "runtime_id=" in result
    assert "Do not start a duplicate run yet" in result


@pytest.mark.asyncio
async def test_equipment_run_recovery_prompts_report_once(tmp_path: Path) -> None:
    provider = MockProvider()
    provider.queue(
        make_text_response("I am done"),
        make_tool_response(
            "report_to_caller",
            {"status": "success", "summary": "Recovered in follow-up."},
            "tc1",
        ),
        make_text_response("done"),
    )
    config = DrClawConfig(data_dir=str(tmp_path / "data"))
    manager = EquipmentRuntimeManager(
        prototype_store=_make_store(tmp_path),
        runtime_root=tmp_path / "runtime",
        provider=provider,
        config=config,
    )

    runtime = await manager.start_run(
        prototype_name="analyzer",
        instruction="Do something quickly",
        caller_agent_id="main",
    )
    runtime = await manager.wait_for_terminal(runtime.runtime_id, timeout_seconds=5)
    assert runtime.status == "succeeded"
    assert runtime.summary == "Recovered in follow-up."
