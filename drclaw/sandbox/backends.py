"""Sandbox backend implementations."""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from drclaw.sandbox.models import SandboxJob, SandboxJobResult, SandboxJobState, ShellTaskSpec

_MAX_LOG_CHARS = 10_000


class StatusCallback(Protocol):
    async def __call__(
        self,
        status_message: str,
        progress_pct: float | None = None,
        status: SandboxJobState | None = None,
    ) -> None: ...


class SandboxBackend(Protocol):
    """Execution backend for sandbox jobs."""

    async def run_job(
        self,
        job: SandboxJob,
        spec: ShellTaskSpec,
        *,
        status_callback: StatusCallback | None = None,
    ) -> SandboxJobResult: ...

    async def pause_job(self, job: SandboxJob) -> None: ...

    async def resume_job(self, job: SandboxJob) -> None: ...

    async def cancel_job(self, job: SandboxJob) -> None: ...


@dataclass
class _CommandResult:
    stdout: str
    stderr: str
    returncode: int


class DockerSandboxBackend:
    """Docker CLI based backend for shell-task sandbox jobs."""

    def __init__(
        self,
        *,
        image: str | None = None,
        memory_limit: str = "512m",
        cpu_limit: float = 1.0,
        network_enabled: bool = False,
        default_timeout_seconds: int = 900,
    ) -> None:
        self.image = image or os.environ.get("DRCLAW_SANDBOX_IMAGE") or "python:3.11-slim"
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.network_enabled = network_enabled
        self.default_timeout_seconds = default_timeout_seconds

    async def run_job(
        self,
        job: SandboxJob,
        spec: ShellTaskSpec,
        *,
        status_callback: StatusCallback | None = None,
    ) -> SandboxJobResult:
        self._ensure_docker_available()
        workspace = job.workspace_path.resolve()
        container_name = f"drclaw-sjob-{job.job_id}"
        container_workdir = self._resolve_container_workdir(workspace, spec.workdir)

        if status_callback is not None:
            await status_callback("creating docker container", status="starting")

        create_args = self._build_create_args(
            workspace=workspace,
            container_name=container_name,
            container_workdir=container_workdir,
            sandbox_policy=job.sandbox_policy,
            command=spec.command,
        )
        create_res = await self._run_command(create_args, timeout=30)
        if create_res.returncode != 0:
            raise RuntimeError(create_res.stderr.strip() or "docker create failed")

        container_id = (create_res.stdout.strip() or container_name).splitlines()[-1]
        job.container_id = container_id
        job.image = self.image

        try:
            start_res = await self._run_command(["docker", "start", container_id], timeout=30)
            if start_res.returncode != 0:
                raise RuntimeError(start_res.stderr.strip() or "docker start failed")
            if status_callback is not None:
                await status_callback(
                    f"running in docker container {container_id[:12]}",
                    status="running",
                )

            timeout_seconds = spec.timeout_seconds or self.default_timeout_seconds
            wait_proc = await asyncio.create_subprocess_exec(
                "docker",
                "wait",
                container_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    wait_proc.communicate(),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                wait_proc.kill()
                try:
                    await asyncio.wait_for(wait_proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                await self.cancel_job(job)
                logs = await self._collect_logs(container_id)
                return SandboxJobResult(
                    status="timed_out",
                    summary=f"Sandbox job timed out after {timeout_seconds} seconds.",
                    artifacts={
                        "container_id": container_id,
                        "image": self.image,
                        "logs": logs,
                    },
                )

            exit_text = stdout.decode("utf-8", errors="replace").strip()
            wait_err = stderr.decode("utf-8", errors="replace").strip()
            if wait_proc.returncode != 0:
                raise RuntimeError(wait_err or "docker wait failed")

            exit_code = int(exit_text.splitlines()[-1] or "1")
            logs = await self._collect_logs(container_id)
            artifacts = {
                "container_id": container_id,
                "image": self.image,
                "exit_code": exit_code,
                "logs": logs,
                "command": spec.command,
            }
            if exit_code == 0:
                return SandboxJobResult(
                    status="succeeded",
                    summary="Sandbox job completed successfully.",
                    artifacts=artifacts,
                )
            return SandboxJobResult(
                status="failed",
                summary=f"Sandbox job failed with exit code {exit_code}.",
                artifacts=artifacts,
            )
        except asyncio.CancelledError:
            await self.cancel_job(job)
            raise
        finally:
            await self._cleanup_container(container_id)

    async def pause_job(self, job: SandboxJob) -> None:
        if not job.container_id:
            raise RuntimeError("sandbox job has no docker container yet")
        res = await self._run_command(["docker", "pause", job.container_id], timeout=30)
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or "docker pause failed")

    async def resume_job(self, job: SandboxJob) -> None:
        if not job.container_id:
            raise RuntimeError("sandbox job has no docker container yet")
        res = await self._run_command(["docker", "unpause", job.container_id], timeout=30)
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or "docker unpause failed")

    async def cancel_job(self, job: SandboxJob) -> None:
        if not job.container_id:
            return
        await self._run_command(["docker", "rm", "-f", job.container_id], timeout=30)

    def _ensure_docker_available(self) -> None:
        if shutil.which("docker") is None:
            raise RuntimeError("docker CLI not found on PATH")

    def _build_create_args(
        self,
        *,
        workspace: Path,
        container_name: str,
        container_workdir: str,
        sandbox_policy: str,
        command: str,
    ) -> list[str]:
        mount_mode = "ro" if sandbox_policy == "readonly" else "rw"
        args = [
            "docker",
            "create",
            "--name",
            container_name,
            "--user",
            "65534:65534",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=64m",
            "--read-only",
            "--memory",
            self.memory_limit,
            "--cpus",
            str(self.cpu_limit),
            "--workdir",
            container_workdir,
            "-v",
            f"{workspace}:/workspace:{mount_mode}",
        ]
        if not self.network_enabled:
            args.extend(["--network", "none"])
        args.extend([self.image, "sh", "-lc", command])
        return args

    def _resolve_container_workdir(self, workspace: Path, workdir: str | None) -> str:
        if not workdir:
            return "/workspace"
        requested = Path(workdir)
        resolved = (
            (workspace / requested).resolve()
            if not requested.is_absolute()
            else requested.resolve()
        )
        try:
            rel = resolved.relative_to(workspace)
        except ValueError as exc:
            raise RuntimeError("sandbox workdir must stay inside the job workspace") from exc
        if not rel.parts:
            return "/workspace"
        return PurePosixPath("/workspace").joinpath(*rel.parts).as_posix()

    async def _collect_logs(self, container_id: str) -> str:
        res = await self._run_command(["docker", "logs", container_id], timeout=30)
        text = "\n".join(part for part in (res.stdout, res.stderr) if part).strip()
        if len(text) > _MAX_LOG_CHARS:
            extra = len(text) - _MAX_LOG_CHARS
            return text[:_MAX_LOG_CHARS] + f"\n... (truncated, {extra} more chars)"
        return text

    async def _cleanup_container(self, container_id: str) -> None:
        await self._run_command(["docker", "rm", "-f", container_id], timeout=30)

    async def _run_command(
        self,
        args: list[str],
        *,
        timeout: int,
    ) -> _CommandResult:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            cmd = " ".join(shlex.quote(arg) for arg in args)
            raise RuntimeError(f"command timed out: {cmd}") from None
        return _CommandResult(
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            returncode=proc.returncode,
        )
