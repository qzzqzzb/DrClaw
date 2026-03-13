"""LaunchAgent helpers for running the tray app on macOS."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path


def default_launch_agent_path(label: str) -> Path:
    return Path("~/Library/LaunchAgents").expanduser() / f"{label}.plist"


def render_launch_agent_plist(
    *,
    label: str,
    run_at_load: bool,
    stdout_path: Path,
    stderr_path: Path,
) -> bytes:
    payload = {
        "Label": label,
        "ProgramArguments": [sys.executable, "-m", "drclaw.tray"],
        "RunAtLoad": run_at_load,
        "KeepAlive": False,
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML)


def write_launch_agent(
    *,
    label: str,
    run_at_load: bool,
    stdout_path: Path,
    stderr_path: Path,
    plist_path: Path | None = None,
) -> Path:
    target = plist_path or default_launch_agent_path(label)
    target.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        render_launch_agent_plist(
            label=label,
            run_at_load=run_at_load,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
    )
    return target


def launchctl_target(label: str) -> str:
    return f"gui/{os.getuid()}/{label}"


def launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
        check=False,
    )
