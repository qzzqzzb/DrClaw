"""Cron scheduling tool."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from drclaw.cron.service import CronService
from drclaw.cron.types import CronSchedule
from drclaw.tools.base import Tool


class CronTool(Tool):
    """Manage scheduled jobs for main/project agents."""

    def __init__(
        self,
        cron_service: CronService,
        *,
        default_target: str,
        resolve_target: Callable[[str], str] | None = None,
        context_provider: Callable[[], tuple[str, str] | None] | None = None,
    ) -> None:
        self._cron = cron_service
        self._default_target = default_target
        self._resolve_target = resolve_target
        self._context_provider = context_provider

    @property
    def name(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return (
            "Schedule and manage jobs for agents. "
            "Actions: add, list, remove, enable, run."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "remove", "enable", "run"],
                    "description": "Action to perform",
                },
                "name": {"type": "string", "description": "Job name (for add)"},
                "target": {
                    "type": "string",
                    "description": (
                        "Target agent. Use 'main' or project name/ID or 'proj:<id>'. "
                        "Defaults to current agent context."
                    ),
                },
                "message": {"type": "string", "description": "Scheduled prompt message (for add)"},
                "every_seconds": {
                    "type": "integer",
                    "description": "Run every N seconds (for add)",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "Cron expression, e.g. '0 8 * * *' (for add)",
                },
                "tz": {
                    "type": "string",
                    "description": "IANA timezone for cron_expr, e.g. 'Asia/Shanghai'",
                },
                "at": {
                    "type": "string",
                    "description": "ISO datetime for one-time run, e.g. '2026-03-09T08:00:00'",
                },
                "channel": {
                    "type": "string",
                    "description": (
                        "Delivery channel for outbound result. "
                        "Defaults to current channel."
                    ),
                },
                "chat_id": {
                    "type": "string",
                    "description": "Delivery chat_id for outbound result, defaults to current chat",
                },
                "delete_after_run": {
                    "type": "boolean",
                    "description": (
                        "Delete one-time job after it runs. "
                        "Default is true for at schedules."
                    ),
                },
                "include_disabled": {
                    "type": "boolean",
                    "description": "Include disabled jobs in list output",
                },
                "job_id": {"type": "string", "description": "Job ID for remove/enable/run"},
                "enabled": {
                    "type": "boolean",
                    "description": "Enabled flag for enable action (default true)",
                },
                "force": {
                    "type": "boolean",
                    "description": "Run even if disabled (for run action)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        action = params["action"]
        if action == "add":
            return self._add(params)
        if action == "list":
            return self._list(params)
        if action == "remove":
            return self._remove(params)
        if action == "enable":
            return self._enable(params)
        if action == "run":
            return await self._run(params)
        return f"Error: unknown action {action!r}"

    def _resolve(self, raw: str | None) -> str:
        if raw is None or not raw.strip():
            return self._default_target
        value = raw.strip()
        if self._resolve_target is None:
            return value
        return self._resolve_target(value)

    def _delivery(self, target: str, channel: str | None, chat_id: str | None) -> tuple[str, str]:
        if channel and chat_id:
            return channel, chat_id
        if self._context_provider:
            current = self._context_provider()
            if current:
                ch, cid = current
                return channel or ch, chat_id or cid
        return channel or "system", chat_id or target

    @staticmethod
    def _schedule_from_params(params: dict[str, Any]) -> CronSchedule:
        every_seconds = params.get("every_seconds")
        cron_expr = params.get("cron_expr")
        tz = params.get("tz")
        at = params.get("at")
        choices = [every_seconds is not None, bool(cron_expr), bool(at)]
        if sum(1 for c in choices if c) != 1:
            raise ValueError("exactly one of every_seconds, cron_expr, or at is required")
        if tz and not cron_expr:
            raise ValueError("tz can only be used with cron_expr")
        if every_seconds is not None:
            return CronSchedule(kind="every", every_ms=int(every_seconds) * 1000)
        if cron_expr:
            return CronSchedule(kind="cron", expr=cron_expr, tz=tz)
        dt = datetime.fromisoformat(str(at))
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))

    @staticmethod
    def _format_schedule(job) -> str:  # noqa: ANN001
        if job.schedule.kind == "every":
            return f"every {(job.schedule.every_ms or 0) // 1000}s"
        if job.schedule.kind == "cron":
            expr = job.schedule.expr or ""
            return f"{expr} ({job.schedule.tz})" if job.schedule.tz else expr
        return "one-time"

    def _add(self, params: dict[str, Any]) -> str:
        message = str(params.get("message", "")).strip()
        if not message:
            return "Error: message is required for add"
        try:
            target = self._resolve(params.get("target"))
            schedule = self._schedule_from_params(params)
        except ValueError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error: invalid schedule/target: {exc}"

        ch, cid = self._delivery(
            target,
            params.get("channel"),
            params.get("chat_id"),
        )
        explicit_delete = params.get("delete_after_run")
        delete_after_run = (
            bool(explicit_delete)
            if explicit_delete is not None
            else schedule.kind == "at"
        )
        name = str(params.get("name") or message[:40] or f"job-{target}")
        try:
            job = self._cron.add_job(
                name=name,
                schedule=schedule,
                target=target,
                message=message,
                channel=ch,
                chat_id=cid,
                delete_after_run=delete_after_run,
            )
        except ValueError as exc:
            return f"Error: {exc}"
        return f"Created job '{job.name}' (id: {job.id}, target: {target})"

    def _list(self, params: dict[str, Any]) -> str:
        include_disabled = bool(params.get("include_disabled", False))
        target = params.get("target")
        jobs = self._cron.list_jobs(include_disabled=include_disabled)
        if target:
            try:
                resolved = self._resolve(target)
            except Exception as exc:
                return f"Error: {exc}"
            jobs = [job for job in jobs if job.payload.target == resolved]
        if not jobs:
            return "No scheduled jobs."
        lines = ["Scheduled jobs:"]
        for job in jobs:
            status = "enabled" if job.enabled else "disabled"
            lines.append(
                f"- {job.name} (id: {job.id}, target: {job.payload.target}, "
                f"schedule: {self._format_schedule(job)}, {status})"
            )
        return "\n".join(lines)

    def _remove(self, params: dict[str, Any]) -> str:
        job_id = str(params.get("job_id", "")).strip()
        if not job_id:
            return "Error: job_id is required for remove"
        if self._cron.remove_job(job_id):
            return f"Removed job {job_id}"
        return f"Error: job {job_id} not found"

    def _enable(self, params: dict[str, Any]) -> str:
        job_id = str(params.get("job_id", "")).strip()
        if not job_id:
            return "Error: job_id is required for enable"
        enabled = bool(params.get("enabled", True))
        job = self._cron.enable_job(job_id, enabled=enabled)
        if job is None:
            return f"Error: job {job_id} not found"
        state = "enabled" if enabled else "disabled"
        return f"Job '{job.name}' {state}"

    async def _run(self, params: dict[str, Any]) -> str:
        job_id = str(params.get("job_id", "")).strip()
        if not job_id:
            return "Error: job_id is required for run"
        force = bool(params.get("force", False))
        ok = await self._cron.run_job(job_id, force=force)
        if not ok:
            return f"Error: job {job_id} not found or disabled"
        return f"Executed job {job_id}"
