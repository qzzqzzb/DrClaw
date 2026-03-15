"""Tools for Docker-backed sandbox jobs."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from drclaw.tools.base import Tool

if TYPE_CHECKING:
    from drclaw.sandbox.manager import SandboxJobManager


class CreateJobTool(Tool):
    """Create one Docker-backed sandbox job."""

    def __init__(self, manager: SandboxJobManager, *, caller_agent_id: str) -> None:
        self._manager = manager
        self._caller_agent_id = caller_agent_id

    @property
    def name(self) -> str:
        return "create_job"

    @property
    def description(self) -> str:
        return (
            "Create a sandboxed Docker job for shell-based work. "
            "Use this for high-risk commands that should not run on the host."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1},
                "description": {"type": "string"},
                "command": {"type": "string", "minLength": 1},
                "sandbox": {
                    "type": "string",
                    "enum": ["readonly", "workspace_write"],
                    "description": "Workspace mount policy inside the sandbox container.",
                },
                "workdir": {
                    "type": "string",
                    "description": "Optional workdir relative to the job workspace.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional wall-clock timeout for the container job.",
                },
                "await_result": {
                    "type": "boolean",
                    "description": "If true, wait for the terminal result before returning.",
                },
            },
            "required": ["title", "command"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        title = params["title"]
        description = str(params.get("description") or "")
        command = params["command"]
        sandbox = params.get("sandbox", "readonly")
        workdir = params.get("workdir")
        timeout_seconds = params.get("timeout_seconds")
        await_result = bool(params.get("await_result", False))

        try:
            job = await self._manager.start_shell_job(
                title=title,
                description=description,
                command=command,
                caller_agent_id=self._caller_agent_id,
                sandbox_policy=sandbox,
                workdir=workdir,
                timeout_seconds=timeout_seconds,
                notify_on_completion=not await_result,
            )
        except Exception as exc:
            return f"Error: failed to start sandbox job: {exc}"

        if not await_result:
            return (
                f"Started sandbox job '{job.title}' "
                f"(job_id={job.job_id}, request_id={job.request_id})."
            )

        try:
            job = await self._manager.wait_for_terminal(job.job_id, timeout_seconds=timeout_seconds)
        except asyncio.TimeoutError:
            live = self._manager.get_job(job.job_id)
            if live is None:
                return (
                    "Error: waiting for sandbox job timed out, "
                    "and the job is no longer available."
                )
            return (
                "Error: waiting for sandbox job timed out. "
                f"Job is still active (request_id={live.request_id}, job_id={live.job_id}, "
                f"status={live.status}, msg={live.status_message}). "
                "Do not start a duplicate job yet; check with get_job_status/list_active_jobs."
            )
        except Exception as exc:
            return f"Error: waiting for sandbox job failed: {exc}"

        return _format_job_terminal(job)


class GetSandboxJobStatusTool(Tool):
    """Read one sandbox job status."""

    def __init__(self, manager: SandboxJobManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "get_job_status"

    @property
    def description(self) -> str:
        return "Get status for one sandbox job by request_id or job_id."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "job_id": {"type": "string"},
            },
        }

    async def execute(self, params: dict[str, Any]) -> str:
        request_id = params.get("request_id")
        job_id = params.get("job_id")
        job = None
        if request_id:
            job = self._manager.get_by_request(request_id)
        elif job_id:
            job = self._manager.get_job(job_id)
        else:
            return "Error: one of request_id or job_id is required."

        if job is None:
            return "No matching sandbox job."
        return _format_job_status(job)


class ListActiveSandboxJobsTool(Tool):
    """List active sandbox jobs for one caller agent."""

    def __init__(self, manager: SandboxJobManager, *, caller_agent_id: str) -> None:
        self._manager = manager
        self._caller_agent_id = caller_agent_id

    @property
    def name(self) -> str:
        return "list_active_jobs"

    @property
    def description(self) -> str:
        return "List active sandbox jobs started by this agent."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, params: dict[str, Any]) -> str:
        jobs = self._manager.list_for_caller(self._caller_agent_id, include_terminal=False)
        if not jobs:
            return "No active sandbox jobs."
        lines = ["Active sandbox jobs:"]
        for job in jobs:
            lines.append(
                f"- request_id={job.request_id} job_id={job.job_id} title={job.title} "
                f"status={job.status} msg={job.status_message}"
            )
        return "\n".join(lines)


class PauseSandboxJobTool(Tool):
    """Pause one running sandbox job."""

    def __init__(self, manager: SandboxJobManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "pause_job"

    @property
    def description(self) -> str:
        return "Pause a running sandbox job by job_id."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["job_id"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        try:
            job = await self._manager.pause_job(
                params["job_id"],
                reason=str(params.get("reason") or "paused"),
            )
        except Exception as exc:
            return f"Error: failed to pause sandbox job: {exc}"
        return f"Sandbox job paused: {job.job_id} ({job.status_message})"


class ResumeSandboxJobTool(Tool):
    """Resume one suspended sandbox job."""

    def __init__(self, manager: SandboxJobManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "resume_job"

    @property
    def description(self) -> str:
        return "Resume a suspended sandbox job by job_id."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["job_id"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        try:
            job = await self._manager.resume_job(
                params["job_id"],
                reason=str(params.get("reason") or "resumed"),
            )
        except Exception as exc:
            return f"Error: failed to resume sandbox job: {exc}"
        return f"Sandbox job resumed: {job.job_id} ({job.status_message})"


class CancelSandboxJobTool(Tool):
    """Cancel one sandbox job."""

    def __init__(self, manager: SandboxJobManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "cancel_job"

    @property
    def description(self) -> str:
        return "Cancel a sandbox job by job_id."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
            },
            "required": ["job_id"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        try:
            job = await self._manager.cancel_job(params["job_id"])
        except Exception as exc:
            return f"Error: failed to cancel sandbox job: {exc}"
        return f"Sandbox job cancelled: {job.job_id} ({job.status})"


def _format_job_status(job: Any) -> str:
    base = (
        f"request_id={job.request_id} job_id={job.job_id} title={job.title} "
        f"caller={job.caller_agent_id} status={job.status} msg={job.status_message} "
        f"workspace_path={job.workspace_path} sandbox={job.sandbox_policy} "
        f"worker_kind={job.worker_kind}"
    )
    if job.container_id:
        base += f" container_id={job.container_id}"
    if job.artifacts:
        artifacts = json.dumps(job.artifacts, ensure_ascii=False, default=str)
        return f"{base}\nartifacts: {artifacts}"
    return base


def _format_job_terminal(job: Any) -> str:
    base = _format_job_status(job)
    if job.summary:
        return f"{base}\nsummary: {job.summary}"
    return base
