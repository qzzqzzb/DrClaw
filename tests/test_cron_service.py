"""Tests for the cron scheduler service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from drclaw.cron.service import CronService
from drclaw.cron.types import CronSchedule


def _future_at_ms(minutes: int = 5) -> int:
    dt = datetime.now(tz=timezone.utc) + timedelta(minutes=minutes)
    return int(dt.timestamp() * 1000)


def test_add_and_reload_job(tmp_path: Path) -> None:
    store = tmp_path / "cron" / "jobs.json"
    service = CronService(store)
    job = service.add_job(
        name="daily-check",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        target="main",
        message="report status",
        channel="system",
        chat_id="cron",
    )

    reloaded = CronService(store).list_jobs()
    assert store.exists()
    assert len(reloaded) == 1
    assert reloaded[0].id == job.id
    assert reloaded[0].payload.target == "main"
    assert reloaded[0].payload.message == "report status"


@pytest.mark.asyncio
async def test_run_job_updates_state(tmp_path: Path) -> None:
    store = tmp_path / "cron" / "jobs.json"
    seen: list[str] = []

    async def on_job(job) -> str | None:  # noqa: ANN001
        seen.append(job.id)
        return "ok"

    service = CronService(store, on_job=on_job)
    job = service.add_job(
        name="tick",
        schedule=CronSchedule(kind="every", every_ms=30_000),
        target="main",
        message="hello",
    )
    ok = await service.run_job(job.id)
    assert ok is True
    assert seen == [job.id]

    loaded = service.list_jobs(include_disabled=True)
    assert loaded[0].state.last_status == "ok"
    assert loaded[0].state.last_run_at_ms is not None
    assert loaded[0].state.next_run_at_ms is not None


@pytest.mark.asyncio
async def test_at_job_auto_deletes_when_configured(tmp_path: Path) -> None:
    store = tmp_path / "cron" / "jobs.json"
    async def on_job(_job) -> str | None:  # noqa: ANN001
        return "ok"

    service = CronService(store, on_job=on_job)
    job = service.add_job(
        name="one-shot",
        schedule=CronSchedule(kind="at", at_ms=_future_at_ms()),
        target="main",
        message="once",
        delete_after_run=True,
    )

    assert await service.run_job(job.id) is True
    assert service.list_jobs(include_disabled=True) == []


def test_enable_disable_job(tmp_path: Path) -> None:
    store = tmp_path / "cron" / "jobs.json"
    service = CronService(store)
    job = service.add_job(
        name="toggle",
        schedule=CronSchedule(kind="every", every_ms=30_000),
        target="main",
        message="ping",
    )
    disabled = service.enable_job(job.id, enabled=False)
    assert disabled is not None
    assert disabled.enabled is False
    assert disabled.state.next_run_at_ms is None

    enabled = service.enable_job(job.id, enabled=True)
    assert enabled is not None
    assert enabled.enabled is True
    assert enabled.state.next_run_at_ms is not None
