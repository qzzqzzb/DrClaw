"""Tests for persistent remote tmux session storage."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from drclaw.remote_tmux.store import RemoteTmuxSessionRecord, RemoteTmuxSessionStore


def _record(*, session_id: str, status: str = "running") -> RemoteTmuxSessionRecord:
    now = datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc)
    return RemoteTmuxSessionRecord(
        session_id=session_id,
        host="user@example",
        tmux_session_name=f"tmux-{session_id}",
        command="python train.py",
        remote_cwd="/srv/app",
        remote_log_path=f"~/.drclaw_remote_tmux/tmux-{session_id}/output.log",
        remote_exit_code_path=f"~/.drclaw_remote_tmux/tmux-{session_id}/exit_code",
        status=status,
        created_at=now,
        updated_at=now,
    )


def test_remote_tmux_store_roundtrip(tmp_path: Path) -> None:
    store = RemoteTmuxSessionStore(tmp_path / "remote_tmux_sessions.json")
    record = _record(session_id="rtmx-1")

    store.upsert(record)
    loaded = store.get("rtmx-1")

    assert loaded is not None
    assert loaded.host == "user@example"
    assert loaded.tmux_session_name == "tmux-rtmx-1"
    assert loaded.remote_log_path.endswith("/output.log")


def test_remote_tmux_store_filters_terminal_sessions(tmp_path: Path) -> None:
    store = RemoteTmuxSessionStore(tmp_path / "remote_tmux_sessions.json")
    store.upsert(_record(session_id="running-1", status="running"))
    store.upsert(_record(session_id="done-1", status="succeeded"))

    active = store.list_sessions(include_terminal=False)

    assert [item.session_id for item in active] == ["running-1"]


def test_find_active_by_host_and_tmux_ignores_terminal(tmp_path: Path) -> None:
    store = RemoteTmuxSessionStore(tmp_path / "remote_tmux_sessions.json")
    store.upsert(_record(session_id="old", status="failed"))
    store.upsert(_record(session_id="new", status="running"))

    found = store.find_active_by_host_and_tmux(
        host="user@example",
        tmux_session_name="tmux-new",
    )

    assert found is not None
    assert found.session_id == "new"
