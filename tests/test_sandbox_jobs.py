"""Tests for sandbox job manager and tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from drclaw.bus.queue import MessageBus
from drclaw.sandbox.manager import SandboxJobManager
from drclaw.sandbox.models import SandboxJob, SandboxJobResult, ShellTaskSpec
from drclaw.tools.sandbox_jobs import (
    CancelSandboxJobTool,
    CreateJobTool,
    GetSandboxJobStatusTool,
    ListActiveSandboxJobsTool,
    PauseSandboxJobTool,
    ResumeSandboxJobTool,
)


class FakeSandboxBackend:
    def __init__(self) -> None:
        self.resume_gate = asyncio.Event()
        self.resume_gate.set()
        self.finish_gate = asyncio.Event()
        self.cancelled = False

    async def run_job(
        self,
        job: SandboxJob,
        spec: ShellTaskSpec,
        *,
        status_callback=None,
    ) -> SandboxJobResult:
        job.container_id = f"ctr-{job.job_id}"
        if status_callback is not None:
            await status_callback("running", status="running")
        while not self.finish_gate.is_set():
            await self.resume_gate.wait()
            try:
                await asyncio.wait_for(self.finish_gate.wait(), timeout=0.01)
            except asyncio.TimeoutError:
                continue
        if self.cancelled:
            return SandboxJobResult(
                status="cancelled",
                summary="Sandbox job was cancelled.",
                artifacts={"backend": "fake"},
            )
        return SandboxJobResult(
            status="succeeded",
            summary=f"Executed: {spec.command}",
            artifacts={"backend": "fake", "command": spec.command},
        )

    async def pause_job(self, job: SandboxJob) -> None:
        self.resume_gate.clear()

    async def resume_job(self, job: SandboxJob) -> None:
        self.resume_gate.set()

    async def cancel_job(self, job: SandboxJob) -> None:
        self.cancelled = True
        self.finish_gate.set()


@pytest.mark.asyncio
async def test_create_job_tool_waits_for_terminal(tmp_path: Path) -> None:
    backend = FakeSandboxBackend()
    manager = SandboxJobManager(
        runtime_root=tmp_path / "sandbox_jobs",
        backend=backend,
    )
    tool = CreateJobTool(manager, caller_agent_id="main")

    async def _finish() -> None:
        await asyncio.sleep(0.05)
        backend.finish_gate.set()

    asyncio.create_task(_finish())
    result = await tool.execute(
        {
            "title": "baseline",
            "description": "run a baseline command",
            "command": "echo hi",
            "await_result": True,
            "timeout_seconds": 3,
        }
    )
    assert "status=succeeded" in result
    assert "Executed: echo hi" in result
    assert "sandbox=readonly" in result


@pytest.mark.asyncio
async def test_pause_resume_and_cancel_tools(tmp_path: Path) -> None:
    backend = FakeSandboxBackend()
    bus = MessageBus()
    manager = SandboxJobManager(
        runtime_root=tmp_path / "sandbox_jobs",
        backend=backend,
        bus=bus,
    )
    create = CreateJobTool(manager, caller_agent_id="proj:p1")
    pause = PauseSandboxJobTool(manager)
    resume = ResumeSandboxJobTool(manager)
    cancel = CancelSandboxJobTool(manager)
    status = GetSandboxJobStatusTool(manager)
    listing = ListActiveSandboxJobsTool(manager, caller_agent_id="proj:p1")

    started = await create.execute(
        {
            "title": "long job",
            "command": "sleep 30",
            "sandbox": "workspace_write",
            "await_result": False,
        }
    )
    assert "Started sandbox job 'long job'" in started

    jobs = manager.list_for_caller("proj:p1")
    assert len(jobs) == 1
    job = jobs[0]
    for _ in range(100):
        live = manager.get_job(job.job_id)
        if live is not None and live.status == "running":
            break
        await asyncio.sleep(0.01)
    assert manager.get_job(job.job_id).status == "running"

    paused = await pause.execute({"job_id": job.job_id, "reason": "manual pause"})
    assert "Sandbox job paused" in paused
    assert manager.get_job(job.job_id).status == "suspended"

    active = await listing.execute({})
    assert "status=suspended" in active

    resumed = await resume.execute({"job_id": job.job_id})
    assert "Sandbox job resumed" in resumed
    assert manager.get_job(job.job_id).status == "running"

    snapshot = await status.execute({"job_id": job.job_id})
    assert f"job_id={job.job_id}" in snapshot
    assert "status=running" in snapshot

    cancelled = await cancel.execute({"job_id": job.job_id})
    assert "Sandbox job cancelled" in cancelled
    final = manager.get_job(job.job_id)
    assert final is not None
    assert final.status == "cancelled"

    inbound = await asyncio.wait_for(bus.consume_inbound("proj:p1"), timeout=1.0)
    assert "cancelled" in inbound.text
