"""Manager for Docker-backed sandbox jobs."""

from __future__ import annotations

import asyncio
import json
import secrets
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from loguru import logger

from drclaw.bus.queue import MessageBus
from drclaw.models.messages import InboundMessage, OutboundMessage
from drclaw.sandbox.backends import SandboxBackend
from drclaw.sandbox.models import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    SandboxJob,
    SandboxJobResult,
    SandboxJobState,
    SandboxPolicy,
    ShellTaskSpec,
    utc_now,
)


class SandboxJobManager:
    """Manage sandbox-backed jobs and caller notifications."""

    def __init__(
        self,
        *,
        runtime_root: Path,
        backend: SandboxBackend,
        bus: MessageBus | None = None,
        heartbeat_timeout_seconds: int = 300,
        max_total_active: int = 16,
        max_per_caller_active: int = 4,
    ) -> None:
        self.runtime_root = runtime_root
        self.backend = backend
        self.bus = bus
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.max_total_active = max_total_active
        self.max_per_caller_active = max_per_caller_active

        self._jobs: dict[str, SandboxJob] = {}
        self._request_to_job: dict[str, str] = {}
        self._active_by_caller: dict[str, set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def start_shell_job(
        self,
        *,
        title: str,
        description: str,
        command: str,
        caller_agent_id: str,
        sandbox_policy: SandboxPolicy = "readonly",
        workdir: str | None = None,
        timeout_seconds: int | None = None,
        notify_on_completion: bool = True,
        notify_channel: str = "system",
        notify_chat_id: str | None = None,
    ) -> SandboxJob:
        spec = ShellTaskSpec(
            command=command,
            workdir=workdir,
            timeout_seconds=timeout_seconds,
        )
        return await self.start_job(
            title=title,
            description=description,
            spec=spec,
            caller_agent_id=caller_agent_id,
            sandbox_policy=sandbox_policy,
            notify_on_completion=notify_on_completion,
            notify_channel=notify_channel,
            notify_chat_id=notify_chat_id,
        )

    async def start_job(
        self,
        *,
        title: str,
        description: str,
        spec: ShellTaskSpec,
        caller_agent_id: str,
        sandbox_policy: SandboxPolicy = "readonly",
        notify_on_completion: bool = True,
        notify_channel: str = "system",
        notify_chat_id: str | None = None,
    ) -> SandboxJob:
        async with self._lock:
            active_total = sum(
                1 for job in self._jobs.values() if job.status not in TERMINAL_STATES
            )
            if active_total >= self.max_total_active:
                raise RuntimeError("Too many active sandbox jobs; try again later.")

            caller_active = len(
                [
                    jid
                    for jid in self._active_by_caller[caller_agent_id]
                    if self._jobs.get(jid) and self._jobs[jid].status not in TERMINAL_STATES
                ]
            )
            if caller_active >= self.max_per_caller_active:
                raise RuntimeError(
                    f"Caller {caller_agent_id!r} already has {caller_active} active sandbox jobs."
                )

            job_id = secrets.token_hex(4)
            request_id = secrets.token_hex(6)
            workspace = self.runtime_root / f"{job_id}"
            workspace.mkdir(parents=True, exist_ok=True)

            job = SandboxJob(
                job_id=job_id,
                request_id=request_id,
                caller_agent_id=caller_agent_id,
                title=title,
                description=description,
                workspace_path=workspace,
                sandbox_policy=sandbox_policy,
                worker_kind="shell_task",
                notify_on_completion=notify_on_completion,
                notify_channel=notify_channel,
                notify_chat_id=notify_chat_id or caller_agent_id,
            )
            self._jobs[job_id] = job
            self._request_to_job[request_id] = job_id
            self._active_by_caller[caller_agent_id].add(job_id)
            job.task = asyncio.create_task(self._run_job(job_id=job_id, spec=spec))
            job.task.add_done_callback(lambda task, jid=job_id: self._on_task_done(jid, task))
            return job

    async def stop_all(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        async with self._lock:
            for job in self._jobs.values():
                if job.task and not job.task.done():
                    job.task.cancel()
                    tasks.append(job.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_job(self, job_id: str) -> SandboxJob | None:
        job = self._jobs.get(job_id)
        if job is not None:
            self._mark_timed_out_if_stale(job)
        return job

    def get_by_request(self, request_id: str) -> SandboxJob | None:
        job_id = self._request_to_job.get(request_id)
        if job_id is None:
            return None
        return self.get_job(job_id)

    def list_for_caller(
        self,
        caller_agent_id: str,
        *,
        include_terminal: bool = False,
    ) -> list[SandboxJob]:
        jobs: list[SandboxJob] = []
        for job_id in sorted(self._active_by_caller.get(caller_agent_id, set())):
            job = self._jobs.get(job_id)
            if job is None:
                continue
            self._mark_timed_out_if_stale(job)
            if not include_terminal and job.status in TERMINAL_STATES:
                continue
            jobs.append(job)
        return jobs

    def list_runtime_agents(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for job in self._jobs.values():
            if job.status in TERMINAL_STATES:
                continue
            ui_status = "running" if job.status in ACTIVE_STATES else "error"
            out.append(
                {
                    "id": job.agent_id,
                    "label": f"Sandbox {job.title} ({job.job_id})",
                    "type": "sandbox_job",
                    "status": ui_status,
                }
            )
        return sorted(out, key=lambda item: item["id"])

    async def wait_for_terminal(
        self,
        job_id: str,
        *,
        timeout_seconds: int | None = None,
    ) -> SandboxJob:
        job = self.get_job(job_id)
        if job is None:
            raise ValueError(f"Unknown job id: {job_id}")
        if job.status in TERMINAL_STATES:
            return job
        if timeout_seconds is None:
            await job.done_event.wait()
        else:
            await asyncio.wait_for(job.done_event.wait(), timeout=timeout_seconds)
        return job

    async def update_status(
        self,
        *,
        job_id: str,
        status_message: str,
        progress_pct: float | None = None,
        status: SandboxJobState | None = None,
    ) -> SandboxJob:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError(f"Unknown job id: {job_id}")
            if job.status in TERMINAL_STATES:
                return job
            job.mark_heartbeat(status_message, progress_pct)
            if status is not None and status not in TERMINAL_STATES:
                job.status = status
                if status == "running":
                    job.suspended_at = None

        await self._publish_outbound(msg_type="sandbox_job_status", job=job, text=status_message)
        return job

    async def pause_job(self, job_id: str, *, reason: str = "paused") -> SandboxJob:
        job = self.get_job(job_id)
        if job is None:
            raise ValueError(f"Unknown job id: {job_id}")
        if job.status in TERMINAL_STATES:
            return job
        if job.status in {"queued", "pending_approval", "suspended"}:
            raise RuntimeError(f"Cannot pause sandbox job while status={job.status}")

        await self.backend.pause_job(job)
        async with self._lock:
            live = self._jobs[job_id]
            live.status = "suspended"
            live.status_message = reason
            live.pause_reason = reason
            live.suspended_at = utc_now()
            live.last_heartbeat_at = live.suspended_at
        await self._publish_outbound(msg_type="sandbox_job_status", job=live, text=reason)
        return live

    async def resume_job(self, job_id: str, *, reason: str = "resumed") -> SandboxJob:
        job = self.get_job(job_id)
        if job is None:
            raise ValueError(f"Unknown job id: {job_id}")
        if job.status != "suspended":
            raise RuntimeError(f"Cannot resume sandbox job while status={job.status}")

        await self.backend.resume_job(job)
        async with self._lock:
            live = self._jobs[job_id]
            live.status = "running"
            live.status_message = reason
            live.pause_reason = ""
            live.resumed_at = utc_now()
            live.suspended_at = None
            live.last_heartbeat_at = live.resumed_at
        await self._publish_outbound(msg_type="sandbox_job_status", job=live, text=reason)
        return live

    async def cancel_job(self, job_id: str) -> SandboxJob:
        job = self.get_job(job_id)
        if job is None:
            raise ValueError(f"Unknown job id: {job_id}")
        if job.status in TERMINAL_STATES:
            return job

        try:
            await self.backend.cancel_job(job)
        finally:
            if job.task and not job.task.done():
                job.task.cancel()
                await asyncio.gather(job.task, return_exceptions=True)

        latest = self._jobs.get(job_id)
        if latest is None:
            raise ValueError(f"Unknown job id: {job_id}")
        if latest.status not in TERMINAL_STATES:
            return await self.report_completion(
                job_id=job_id,
                status="cancelled",
                summary="Sandbox job was cancelled.",
            )
        return latest

    async def report_completion(
        self,
        *,
        job_id: str,
        status: str,
        summary: str,
        artifacts: dict[str, Any] | None = None,
    ) -> SandboxJob:
        normalized = _normalize_terminal_status(status)
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError(f"Unknown job id: {job_id}")
            if job.status in TERMINAL_STATES:
                return job
            job.status = normalized
            job.status_message = summary or normalized
            job.summary = summary
            job.artifacts = dict(artifacts or {})
            job.artifacts.setdefault("workspace_path", str(job.workspace_path))
            if job.container_id and "container_id" not in job.artifacts:
                job.artifacts["container_id"] = job.container_id
            if job.image and "image" not in job.artifacts:
                job.artifacts["image"] = job.image
            job.ended_at = utc_now()
            job.last_heartbeat_at = job.ended_at
            job.done_event.set()

        await self._publish_outbound(
            msg_type="sandbox_job_report",
            job=job,
            text=job.summary or f"sandbox job {job.job_id} finished: {job.status}",
        )
        if job.notify_on_completion:
            await self._notify_caller_inbound(job)
        return job

    async def _run_job(self, *, job_id: str, spec: ShellTaskSpec) -> None:
        job = self._jobs[job_id]
        job.mark_starting()
        await self._publish_outbound(msg_type="sandbox_job_status", job=job, text="starting")
        try:
            result = await self.backend.run_job(
                job,
                spec,
                status_callback=lambda message, progress_pct=None, status=None: self.update_status(
                    job_id=job_id,
                    status_message=message,
                    progress_pct=progress_pct,
                    status=status,
                ),
            )
            latest = self._jobs.get(job_id)
            if latest and latest.status not in TERMINAL_STATES:
                await self.report_completion(
                    job_id=job_id,
                    status=result.status,
                    summary=result.summary,
                    artifacts=result.artifacts,
                )
        except asyncio.CancelledError:
            await self.report_completion(
                job_id=job_id,
                status="cancelled",
                summary="Sandbox job was cancelled.",
            )
            raise
        except Exception as exc:
            logger.exception("Sandbox job {} crashed", job_id)
            await self.report_completion(
                job_id=job_id,
                status="failed",
                summary=f"Sandbox job crashed: {exc}",
            )
        finally:
            logger.info(
                "Sandbox job {} completed; preserved workspace at {}",
                job.job_id,
                job.workspace_path,
            )

    def _on_task_done(self, job_id: str, task: asyncio.Task[None]) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        if task.cancelled():
            if job.status not in TERMINAL_STATES:
                job.status = "cancelled"
                job.status_message = "cancelled"
                job.ended_at = utc_now()
                job.done_event.set()
            return

        exc = task.exception()
        if exc is not None and job.status not in TERMINAL_STATES:
            logger.error("Sandbox job {} failed: {}", job_id, exc)
            job.status = "failed"
            job.status_message = str(exc)
            job.ended_at = utc_now()
            job.done_event.set()

    def _mark_timed_out_if_stale(self, job: SandboxJob) -> None:
        if job.status in TERMINAL_STATES:
            return
        if job.status == "suspended":
            return
        if not job.expects_heartbeat:
            return
        delta = utc_now() - job.last_heartbeat_at
        if delta > timedelta(seconds=self.heartbeat_timeout_seconds):
            job.status = "timed_out"
            job.status_message = "timed out waiting for heartbeat"
            job.ended_at = utc_now()
            job.done_event.set()

    async def _publish_outbound(self, *, msg_type: str, job: SandboxJob, text: str) -> None:
        if self.bus is None:
            return
        await self.bus.publish_outbound(
            OutboundMessage(
                channel="system",
                chat_id=job.caller_agent_id,
                text=text,
                source=job.agent_id,
                topic=job.caller_agent_id,
                msg_type=msg_type,
                metadata={
                    "request_id": job.request_id,
                    "job_id": job.job_id,
                    "caller_agent_id": job.caller_agent_id,
                    "title": job.title,
                    "description": job.description,
                    "status": job.status,
                    "status_message": job.status_message,
                    "progress_pct": job.progress_pct,
                    "workspace_path": str(job.workspace_path),
                    "sandbox_policy": job.sandbox_policy,
                    "worker_kind": job.worker_kind,
                    "container_id": job.container_id,
                    "artifacts": job.artifacts,
                },
            )
        )

    async def _notify_caller_inbound(self, job: SandboxJob) -> None:
        if self.bus is None:
            return
        text = f"[sandbox_job:{job.title}] request {job.request_id} {job.status}: {job.summary}"
        if job.artifacts:
            text += "\nartifacts: " + json.dumps(job.artifacts, ensure_ascii=False, default=str)
        await self.bus.publish_inbound(
            InboundMessage(
                channel=job.notify_channel,
                chat_id=job.notify_chat_id or job.caller_agent_id,
                text=text,
                source=job.agent_id,
                metadata={
                    "request_id": job.request_id,
                    "job_id": job.job_id,
                    "caller_agent_id": job.caller_agent_id,
                    "status": job.status,
                    "status_message": job.status_message,
                    "workspace_path": str(job.workspace_path),
                    "sandbox_policy": job.sandbox_policy,
                    "worker_kind": job.worker_kind,
                    "container_id": job.container_id,
                    "artifacts": job.artifacts,
                },
            ),
            topic=job.caller_agent_id,
        )


def _normalize_terminal_status(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"success", "succeeded", "done", "ok"}:
        return "succeeded"
    if normalized in {"fail", "failed", "error"}:
        return "failed"
    if normalized in {"cancel", "cancelled", "canceled"}:
        return "cancelled"
    if normalized in {"timeout", "timed_out"}:
        return "timed_out"
    return "failed"
