"""Tests for tray runtime and launchd helpers."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from drclaw.config.schema import DrClawConfig, TrayConfig
from drclaw.tray.app import TrayRuntime, build_daemon_launch
from drclaw.tray.launchd import render_launch_agent_plist, write_launch_agent


def test_build_daemon_launch_uses_config_command_env_and_cwd(tmp_path: Path) -> None:
    config = DrClawConfig(
        tray=TrayConfig(
            daemon_program=["drclaw", "daemon", "-f", "web"],
            daemon_env={"NO_PROXY": "foo.example"},
            daemon_cwd=str(tmp_path),
        )
    )
    with patch.dict(os.environ, {"BASE_ENV": "1"}, clear=True):
        cmd, env, cwd = build_daemon_launch(config)

    assert cmd == ["drclaw", "daemon", "-f", "web"]
    assert env["BASE_ENV"] == "1"
    assert env["NO_PROXY"] == "foo.example"
    assert cwd == tmp_path


def test_stop_daemon_sends_sigint_and_waits() -> None:
    config = DrClawConfig(tray=TrayConfig(shutdown_timeout_seconds=6))
    runtime = TrayRuntime(config)

    proc = MagicMock()
    proc.poll.return_value = None
    runtime._daemon = proc

    runtime.stop_daemon()

    proc.send_signal.assert_called_once_with(signal.SIGINT)
    proc.wait.assert_called_once_with(timeout=6)


def test_stop_daemon_timeout_uses_terminate_then_kill() -> None:
    config = DrClawConfig(tray=TrayConfig(shutdown_timeout_seconds=1))
    runtime = TrayRuntime(config)

    proc = MagicMock()
    proc.poll.return_value = None
    proc.wait.side_effect = [
        subprocess.TimeoutExpired(cmd="x", timeout=1),
        subprocess.TimeoutExpired(cmd="x", timeout=2),
        None,
    ]
    runtime._daemon = proc

    runtime.stop_daemon()

    proc.send_signal.assert_called_once_with(signal.SIGINT)
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


def test_render_launch_agent_plist_contains_expected_keys(tmp_path: Path) -> None:
    stdout_path = tmp_path / "tray.out.log"
    stderr_path = tmp_path / "tray.err.log"
    plist = render_launch_agent_plist(
        label="com.drclaw.tray",
        run_at_load=False,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )

    text = plist.decode("utf-8")
    assert "com.drclaw.tray" in text
    assert "drclaw.tray" in text
    assert "RunAtLoad" in text
    assert "KeepAlive" in text


def test_write_launch_agent_writes_plist_file(tmp_path: Path) -> None:
    plist_path = tmp_path / "LaunchAgents" / "com.drclaw.tray.plist"
    stdout_path = tmp_path / "logs" / "out.log"
    stderr_path = tmp_path / "logs" / "err.log"

    output = write_launch_agent(
        label="com.drclaw.tray",
        run_at_load=False,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        plist_path=plist_path,
    )

    assert output == plist_path
    assert plist_path.exists()
