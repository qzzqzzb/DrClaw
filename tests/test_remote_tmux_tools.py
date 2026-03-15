"""Tests for remote tmux management tools."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from drclaw.remote_tmux.store import RemoteTmuxSessionRecord, RemoteTmuxSessionStore
from drclaw.tools.remote_tmux import (
    GetRemoteTmuxSessionStatusTool,
    ListRemoteTmuxSessionsTool,
    RemoteTmuxManager,
    SSHCommandResult,
    StartRemoteTmuxSessionTool,
    TerminateRemoteTmuxSessionTool,
)


class _FakeRunner:
    def __init__(self, results: list[SSHCommandResult]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, str]] = []

    async def run(self, *, host: str, remote_command: str) -> SSHCommandResult:
        self.calls.append({"host": host, "remote_command": remote_command})
        if not self.results:
            raise AssertionError("No fake ssh result queued")
        return self.results.pop(0)


def _manager(tmp_path: Path, runner: _FakeRunner) -> tuple[RemoteTmuxManager, RemoteTmuxSessionStore]:
    store = RemoteTmuxSessionStore(tmp_path / "remote_tmux_sessions.json")
    return RemoteTmuxManager(store, ssh_runner=runner), store


@pytest.mark.asyncio
async def test_start_remote_tmux_session_persists_record(tmp_path: Path) -> None:
    runner = _FakeRunner([SSHCommandResult(stdout="", stderr="", returncode=0)])
    manager, store = _manager(tmp_path, runner)
    tool = StartRemoteTmuxSessionTool(manager)

    result = await tool.execute(
        {
            "host": "gpu-box",
            "command": "python train.py --epochs 10",
            "remote_cwd": "/srv/train",
            "session_name": "train-run",
        }
    )

    saved = store.list_sessions()
    assert len(saved) == 1
    assert saved[0].host == "gpu-box"
    assert saved[0].tmux_session_name == "train-run"
    assert "session_id=" in result
    assert "remote_log_path=~/.drclaw_remote_tmux/train-run/output.log" in result
    assert runner.calls[0]["host"] == "gpu-box"
    assert "tmux new-session -d -s train-run" in runner.calls[0]["remote_command"]


@pytest.mark.asyncio
async def test_get_remote_tmux_status_uses_log_tail_and_updates_record(tmp_path: Path) -> None:
    runner = _FakeRunner(
        [
            SSHCommandResult(stdout="", stderr="", returncode=0),
            SSHCommandResult(
                stdout="__DRCLAW_META__ running  log\nline1\nline2\n",
                stderr="",
                returncode=0,
            ),
        ]
    )
    manager, store = _manager(tmp_path, runner)
    record = await manager.start_session(
        host="gpu-box",
        command="python train.py",
        session_name="job-1",
    )
    tool = GetRemoteTmuxSessionStatusTool(manager)

    result = await tool.execute({"session_id": record.session_id, "tail_lines": 20})

    updated = store.get(record.session_id)
    assert updated is not None
    assert updated.status == "running"
    assert updated.last_checked_at is not None
    assert "output_source=log" in result
    assert "line1" in result


@pytest.mark.asyncio
async def test_get_remote_tmux_status_falls_back_to_capture_pane(tmp_path: Path) -> None:
    runner = _FakeRunner(
        [
            SSHCommandResult(stdout="", stderr="", returncode=0),
            SSHCommandResult(
                stdout="__DRCLAW_META__ running  capture-pane\npane output\n",
                stderr="",
                returncode=0,
            ),
        ]
    )
    manager, _ = _manager(tmp_path, runner)
    record = await manager.start_session(
        host="gpu-box",
        command="python train.py",
        session_name="job-pane",
    )
    tool = GetRemoteTmuxSessionStatusTool(manager)

    result = await tool.execute({"session_id": record.session_id})

    assert "output_source=capture-pane" in result
    assert "pane output" in result


@pytest.mark.asyncio
async def test_terminate_remote_tmux_session_marks_cancelled(tmp_path: Path) -> None:
    runner = _FakeRunner(
        [
            SSHCommandResult(stdout="", stderr="", returncode=0),
            SSHCommandResult(stdout="", stderr="", returncode=0),
        ]
    )
    manager, store = _manager(tmp_path, runner)
    record = await manager.start_session(
        host="gpu-box",
        command="python train.py",
        session_name="job-kill",
    )
    tool = TerminateRemoteTmuxSessionTool(manager)

    result = await tool.execute({"session_id": record.session_id})

    updated = store.get(record.session_id)
    assert updated is not None
    assert updated.status == "cancelled"
    assert "status=cancelled" in result
    assert "tmux kill-session -t job-kill" in runner.calls[-1]["remote_command"]


@pytest.mark.asyncio
async def test_start_remote_tmux_rejects_duplicate_active_host_and_session(tmp_path: Path) -> None:
    runner = _FakeRunner([SSHCommandResult(stdout="", stderr="", returncode=0)])
    manager, store = _manager(tmp_path, runner)
    existing = await manager.start_session(
        host="gpu-box",
        command="python train.py",
        session_name="repeat-me",
    )
    assert store.get(existing.session_id) is not None

    with pytest.raises(ValueError):
        await manager.start_session(
            host="gpu-box",
            command="python other.py",
            session_name="repeat-me",
        )


@pytest.mark.asyncio
async def test_list_remote_tmux_sessions_hides_terminal_by_default(tmp_path: Path) -> None:
    store = RemoteTmuxSessionStore(tmp_path / "remote_tmux_sessions.json")
    manager = RemoteTmuxManager(store, ssh_runner=_FakeRunner([]))
    record_time = datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc)
    store.upsert(
        RemoteTmuxSessionRecord(
            session_id="rtmx-done",
            host="gpu-box",
            tmux_session_name="live",
            command="python train.py",
            remote_cwd="",
            remote_log_path="~/.drclaw_remote_tmux/live/output.log",
            remote_exit_code_path="~/.drclaw_remote_tmux/live/exit_code",
            status="succeeded",
            created_at=record_time,
            updated_at=record_time,
        )
    )
    store.upsert(
        RemoteTmuxSessionRecord(
            session_id="rtmx-other",
            host="gpu-box",
            tmux_session_name="still-running",
            command="python eval.py",
            remote_cwd="",
            remote_log_path="~/.drclaw_remote_tmux/still-running/output.log",
            remote_exit_code_path="~/.drclaw_remote_tmux/still-running/exit_code",
            status="running",
            created_at=record_time,
            updated_at=record_time,
        )
    )
    tool = ListRemoteTmuxSessionsTool(manager)

    result = await tool.execute({})

    assert "still-running" in result
    assert "live" not in result
