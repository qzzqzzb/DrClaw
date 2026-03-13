"""Cron service for scheduling and executing agent tasks."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Coroutine

from loguru import logger

from drclaw.cron.types import CronJob, CronJobState, CronPayload, CronSchedule, CronStore

_MAX_SCAN_MINUTES = 366 * 24 * 60
_CRON_FIELD_RANGES = {
    "minute": (0, 59),
    "hour": (0, 23),
    "day": (1, 31),
    "month": (1, 12),
    "weekday": (0, 7),  # 0/7 = Sunday
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _parse_num(token: str, *, field: str) -> int:
    if not token.isdigit():
        raise ValueError(f"invalid {field} value: {token!r}")
    n = int(token)
    lo, hi = _CRON_FIELD_RANGES[field]
    if n < lo or n > hi:
        raise ValueError(f"{field} out of range [{lo},{hi}]: {n}")
    if field == "weekday" and n == 7:
        return 0
    return n


def _parse_field(expr: str, *, field: str) -> set[int]:
    lo, hi = _CRON_FIELD_RANGES[field]
    values: set[int] = set()
    for raw_part in expr.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError(f"empty token in {field} expression")
        step = 1
        if "/" in part:
            base, step_str = part.split("/", 1)
            if not step_str.isdigit() or int(step_str) <= 0:
                raise ValueError(f"invalid step in {field} expression: {part!r}")
            step = int(step_str)
            part = base
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            s, e = part.split("-", 1)
            start = _parse_num(s, field=field)
            end = _parse_num(e, field=field)
            if start > end:
                raise ValueError(f"invalid range in {field} expression: {part!r}")
        else:
            n = _parse_num(part, field=field)
            start = n
            end = n
        for n in range(start, end + 1, step):
            if field == "weekday" and n == 7:
                values.add(0)
            else:
                values.add(n)
    return values


def _parse_cron_expr(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron expression must have exactly 5 fields")
    minute = _parse_field(parts[0], field="minute")
    hour = _parse_field(parts[1], field="hour")
    day = _parse_field(parts[2], field="day")
    month = _parse_field(parts[3], field="month")
    weekday = _parse_field(parts[4], field="weekday")
    return minute, hour, day, month, weekday


def _matches_cron(dt: datetime, expr: str) -> bool:
    minute, hour, day, month, weekday = _parse_cron_expr(expr)
    # Python weekday: Monday=0..Sunday=6; cron weekday: Sunday=0..Saturday=6
    cron_weekday = (dt.weekday() + 1) % 7
    return (
        dt.minute in minute
        and dt.hour in hour
        and dt.day in day
        and dt.month in month
        and cron_weekday in weekday
    )


def _compute_next_run(schedule: CronSchedule, now_ms: int) -> int | None:
    """Compute next run timestamp in milliseconds."""
    if schedule.kind == "at":
        return schedule.at_ms if schedule.at_ms and schedule.at_ms > now_ms else None

    if schedule.kind == "every":
        if not schedule.every_ms or schedule.every_ms <= 0:
            return None
        return now_ms + schedule.every_ms

    if schedule.kind == "cron" and schedule.expr:
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
            base = datetime.fromtimestamp(now_ms / 1000, tz=tz)
            candidate = base.replace(second=0, microsecond=0) + timedelta(minutes=1)
            for _ in range(_MAX_SCAN_MINUTES):
                if _matches_cron(candidate, schedule.expr):
                    return int(candidate.timestamp() * 1000)
                candidate += timedelta(minutes=1)
            return None
        except Exception:
            return None

    return None


def _validate_schedule_for_add(schedule: CronSchedule) -> None:
    if schedule.tz and schedule.kind != "cron":
        raise ValueError("tz can only be used with cron schedules")
    if schedule.kind == "at":
        if schedule.at_ms is None:
            raise ValueError("at schedule requires at_ms")
        if schedule.at_ms <= _now_ms():
            raise ValueError("at schedule must be in the future")
        return
    if schedule.kind == "every":
        if schedule.every_ms is None or schedule.every_ms <= 0:
            raise ValueError("every schedule requires every_ms > 0")
        return
    if schedule.kind == "cron":
        if not schedule.expr:
            raise ValueError("cron schedule requires expr")
        if schedule.tz:
            from zoneinfo import ZoneInfo

            try:
                ZoneInfo(schedule.tz)
            except Exception:
                raise ValueError(f"unknown timezone {schedule.tz!r}") from None
        _parse_cron_expr(schedule.expr)
        return
    raise ValueError(f"unsupported schedule kind {schedule.kind!r}")


class CronService:
    """Service for managing and executing scheduled jobs."""

    def __init__(
        self,
        store_path: Path,
        on_job: Callable[[CronJob], Coroutine[Any, Any, str | None]] | None = None,
    ) -> None:
        self.store_path = store_path
        self.on_job = on_job
        self._store: CronStore | None = None
        self._running = False
        self._timer_task: asyncio.Task[None] | None = None

    def _load_store(self) -> CronStore:
        if self._store is not None:
            return self._store
        if not self.store_path.exists():
            self._store = CronStore()
            return self._store

        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            jobs: list[CronJob] = []
            for row in data.get("jobs", []):
                jobs.append(
                    CronJob(
                        id=row["id"],
                        name=row["name"],
                        enabled=row.get("enabled", True),
                        schedule=CronSchedule(
                            kind=row["schedule"]["kind"],
                            at_ms=row["schedule"].get("atMs"),
                            every_ms=row["schedule"].get("everyMs"),
                            expr=row["schedule"].get("expr"),
                            tz=row["schedule"].get("tz"),
                        ),
                        payload=CronPayload(
                            target=row["payload"].get("target", "main"),
                            message=row["payload"].get("message", ""),
                            channel=row["payload"].get("channel", "system"),
                            chat_id=row["payload"].get("chatId", "cron"),
                        ),
                        state=CronJobState(
                            next_run_at_ms=row.get("state", {}).get("nextRunAtMs"),
                            last_run_at_ms=row.get("state", {}).get("lastRunAtMs"),
                            last_status=row.get("state", {}).get("lastStatus"),
                            last_error=row.get("state", {}).get("lastError"),
                        ),
                        created_at_ms=row.get("createdAtMs", 0),
                        updated_at_ms=row.get("updatedAtMs", 0),
                        delete_after_run=row.get("deleteAfterRun", False),
                    )
                )
            self._store = CronStore(version=data.get("version", 1), jobs=jobs)
        except Exception as exc:
            logger.warning("Failed to load cron store from {}: {}", self.store_path, exc)
            self._store = CronStore()
        return self._store

    def _save_store(self) -> None:
        if self._store is None:
            return

        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self._store.version,
            "jobs": [
                {
                    "id": j.id,
                    "name": j.name,
                    "enabled": j.enabled,
                    "schedule": {
                        "kind": j.schedule.kind,
                        "atMs": j.schedule.at_ms,
                        "everyMs": j.schedule.every_ms,
                        "expr": j.schedule.expr,
                        "tz": j.schedule.tz,
                    },
                    "payload": {
                        "target": j.payload.target,
                        "message": j.payload.message,
                        "channel": j.payload.channel,
                        "chatId": j.payload.chat_id,
                    },
                    "state": {
                        "nextRunAtMs": j.state.next_run_at_ms,
                        "lastRunAtMs": j.state.last_run_at_ms,
                        "lastStatus": j.state.last_status,
                        "lastError": j.state.last_error,
                    },
                    "createdAtMs": j.created_at_ms,
                    "updatedAtMs": j.updated_at_ms,
                    "deleteAfterRun": j.delete_after_run,
                }
                for j in self._store.jobs
            ],
        }
        self.store_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _recompute_next_runs(self) -> None:
        if self._store is None:
            return
        now = _now_ms()
        for job in self._store.jobs:
            if job.enabled:
                job.state.next_run_at_ms = _compute_next_run(job.schedule, now)

    def _next_wake_ms(self) -> int | None:
        if self._store is None:
            return None
        candidates = [
            j.state.next_run_at_ms
            for j in self._store.jobs
            if j.enabled and j.state.next_run_at_ms is not None
        ]
        return min(candidates) if candidates else None

    def _arm_timer(self) -> None:
        if self._timer_task is not None:
            self._timer_task.cancel()
            self._timer_task = None
        if not self._running:
            return
        next_wake = self._next_wake_ms()
        if next_wake is None:
            return

        delay_s = max(0.0, (next_wake - _now_ms()) / 1000)

        async def _tick() -> None:
            await asyncio.sleep(delay_s)
            if self._running:
                await self._on_timer()

        self._timer_task = asyncio.create_task(_tick())

    async def _on_timer(self) -> None:
        if self._store is None:
            return
        now = _now_ms()
        due = [
            job
            for job in self._store.jobs
            if (
                job.enabled
                and job.state.next_run_at_ms is not None
                and now >= job.state.next_run_at_ms
            )
        ]
        for job in due:
            await self._execute_job(job)
        self._save_store()
        self._arm_timer()

    async def _execute_job(self, job: CronJob) -> None:
        started_at = _now_ms()
        try:
            if self.on_job is None:
                job.state.last_status = "skipped"
                job.state.last_error = "no on_job callback configured"
            else:
                await self.on_job(job)
                job.state.last_status = "ok"
                job.state.last_error = None
        except Exception as exc:
            job.state.last_status = "error"
            job.state.last_error = str(exc)
            logger.error("Cron job {} failed: {}", job.id, exc)

        job.state.last_run_at_ms = started_at
        job.updated_at_ms = _now_ms()

        if job.schedule.kind == "at":
            if job.delete_after_run and self._store is not None:
                self._store.jobs = [j for j in self._store.jobs if j.id != job.id]
            else:
                job.enabled = False
                job.state.next_run_at_ms = None
            return

        job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())

    async def start(self) -> None:
        self._running = True
        self._load_store()
        self._recompute_next_runs()
        self._save_store()
        self._arm_timer()
        count = len(self._store.jobs if self._store else [])
        logger.info("Cron service started with {} job(s)", count)

    def stop(self) -> None:
        self._running = False
        if self._timer_task is not None:
            self._timer_task.cancel()
            self._timer_task = None

    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        store = self._load_store()
        jobs = store.jobs if include_disabled else [j for j in store.jobs if j.enabled]
        return sorted(jobs, key=lambda j: j.state.next_run_at_ms or float("inf"))

    def add_job(
        self,
        *,
        name: str,
        schedule: CronSchedule,
        target: str,
        message: str,
        channel: str = "system",
        chat_id: str = "cron",
        delete_after_run: bool = False,
    ) -> CronJob:
        store = self._load_store()
        _validate_schedule_for_add(schedule)
        now = _now_ms()
        job = CronJob(
            id=str(uuid.uuid4())[:8],
            name=name,
            enabled=True,
            schedule=schedule,
            payload=CronPayload(
                target=target,
                message=message,
                channel=channel,
                chat_id=chat_id,
            ),
            state=CronJobState(next_run_at_ms=_compute_next_run(schedule, now)),
            created_at_ms=now,
            updated_at_ms=now,
            delete_after_run=delete_after_run,
        )
        store.jobs.append(job)
        self._save_store()
        self._arm_timer()
        return job

    def remove_job(self, job_id: str) -> bool:
        store = self._load_store()
        before = len(store.jobs)
        store.jobs = [j for j in store.jobs if j.id != job_id]
        removed = len(store.jobs) != before
        if removed:
            self._save_store()
            self._arm_timer()
        return removed

    def enable_job(self, job_id: str, *, enabled: bool = True) -> CronJob | None:
        store = self._load_store()
        for job in store.jobs:
            if job.id != job_id:
                continue
            job.enabled = enabled
            job.updated_at_ms = _now_ms()
            if enabled:
                job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())
            else:
                job.state.next_run_at_ms = None
            self._save_store()
            self._arm_timer()
            return job
        return None

    async def run_job(self, job_id: str, *, force: bool = False) -> bool:
        store = self._load_store()
        for job in store.jobs:
            if job.id != job_id:
                continue
            if not force and not job.enabled:
                return False
            await self._execute_job(job)
            self._save_store()
            self._arm_timer()
            return True
        return False

    def status(self) -> dict[str, Any]:
        store = self._load_store()
        return {
            "enabled": self._running,
            "jobs": len(store.jobs),
            "next_wake_at_ms": self._next_wake_ms(),
        }
