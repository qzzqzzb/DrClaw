"""Project-agent tools for managing long-running remote SSH jobs via tmux."""

from __future__ import annotations

import asyncio
import secrets
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from drclaw.remote_tmux.store import RemoteTmuxSessionRecord, RemoteTmuxSessionStore
from drclaw.tools.base import Tool

_TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
_REMOTE_ROOT = ".drclaw_remote_tmux"
_MAX_OUTPUT_CHARS = 10_000


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _quote(value: str) -> str:
    return shlex.quote(value)


def _validate_single_line(name: str, value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError(f"{name} is required")
    if any(ch in cleaned for ch in "\r\n"):
        raise ValueError(f"{name} must be a single line")
    return cleaned


def _validate_tmux_session_name(value: str) -> str:
    cleaned = _validate_single_line("session_name", value)
    for ch in cleaned:
        if not (ch.isalnum() or ch in "._-"):
            raise ValueError(
                "session_name may only contain letters, numbers, dot, underscore, and hyphen"
            )
    return cleaned


def _remote_paths(tmux_session_name: str) -> tuple[str, str]:
    base = f"~/{_REMOTE_ROOT}/{tmux_session_name}"
    return f"{base}/output.log", f"{base}/exit_code"


@dataclass
class SSHCommandResult:
    stdout: str
    stderr: str
    returncode: int


class SSHCommandRunner:
    """Run a single remote command through ssh."""

    def __init__(self, *, timeout_seconds: int = 60) -> None:
        self._timeout_seconds = timeout_seconds

    async def run(self, *, host: str, remote_command: str) -> SSHCommandResult:
        proc = await asyncio.create_subprocess_exec(
            "ssh",
            "-o",
            "BatchMode=yes",
            host,
            "bash",
            "-lc",
            remote_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            raise RuntimeError(f"ssh command timed out after {self._timeout_seconds} seconds")
        return SSHCommandResult(
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            returncode=proc.returncode or 0,
        )


@dataclass
class RemoteTmuxStatusResult:
    record: RemoteTmuxSessionRecord
    output: str
    output_source: str
    exit_code: int | None = None
    detail: str = ""


class RemoteTmuxManager:
    """Orchestrate project-scoped remote tmux sessions."""

    def __init__(
        self,
        store: RemoteTmuxSessionStore,
        *,
        ssh_runner: SSHCommandRunner | None = None,
    ) -> None:
        self._store = store
        self._ssh_runner = ssh_runner or SSHCommandRunner()

    async def start_session(
        self,
        *,
        host: str,
        command: str,
        remote_cwd: str = "",
        session_name: str = "",
    ) -> RemoteTmuxSessionRecord:
        normalized_host = _validate_single_line("host", host)
        normalized_command = _validate_single_line("command", command)
        normalized_cwd = remote_cwd.strip()
        if any(ch in normalized_cwd for ch in "\r\n"):
            raise ValueError("remote_cwd must be a single line")

        tmux_session_name = (
            _validate_tmux_session_name(session_name)
            if session_name.strip()
            else f"drclaw-{secrets.token_hex(4)}"
        )
        if self._store.find_active_by_host_and_tmux(
            host=normalized_host,
            tmux_session_name=tmux_session_name,
        ):
            raise ValueError(
                f"An active remote tmux session already exists for {normalized_host}:{tmux_session_name}"
            )

        remote_log_path, remote_exit_code_path = _remote_paths(tmux_session_name)
        await self._run_checked(
            host=normalized_host,
            remote_command=self._build_start_command(
                tmux_session_name=tmux_session_name,
                command=normalized_command,
                remote_cwd=normalized_cwd,
            ),
            context="start remote tmux session",
        )

        now = _utc_now()
        record = RemoteTmuxSessionRecord(
            session_id=f"rtmx-{secrets.token_hex(6)}",
            host=normalized_host,
            tmux_session_name=tmux_session_name,
            command=normalized_command,
            remote_cwd=normalized_cwd,
            remote_log_path=remote_log_path,
            remote_exit_code_path=remote_exit_code_path,
            status="running",
            created_at=now,
            updated_at=now,
        )
        self._store.upsert(record)
        return record

    async def get_status(
        self,
        session_id: str,
        *,
        tail_lines: int = 200,
    ) -> RemoteTmuxStatusResult:
        record = self._require_record(session_id)
        lines = max(1, min(int(tail_lines), 1000))
        result = await self._run_checked(
            host=record.host,
            remote_command=self._build_status_command(
                tmux_session_name=record.tmux_session_name,
                tail_lines=lines,
            ),
            context="check remote tmux session",
        )
        status, exit_code, output_source, output = self._parse_status_output(result.stdout)

        if status == "missing":
            status = "failed" if record.status not in _TERMINAL_STATES else record.status

        record.status = status
        record.updated_at = _utc_now()
        record.last_checked_at = record.updated_at
        self._store.upsert(record)
        return RemoteTmuxStatusResult(
            record=record,
            output=output,
            output_source=output_source,
            exit_code=exit_code,
            detail="tmux session not found on remote host" if output_source == "missing" else "",
        )

    async def terminate(self, session_id: str) -> RemoteTmuxSessionRecord:
        record = self._require_record(session_id)
        if record.status not in _TERMINAL_STATES:
            await self._run_checked(
                host=record.host,
                remote_command=self._build_terminate_command(
                    tmux_session_name=record.tmux_session_name,
                ),
                context="terminate remote tmux session",
            )
        record.status = "cancelled"
        record.updated_at = _utc_now()
        record.last_checked_at = record.updated_at
        self._store.upsert(record)
        return record

    def list_sessions(
        self,
        *,
        include_terminal: bool = False,
    ) -> list[RemoteTmuxSessionRecord]:
        return self._store.list_sessions(include_terminal=include_terminal)

    def _require_record(self, session_id: str) -> RemoteTmuxSessionRecord:
        normalized = _validate_single_line("session_id", session_id)
        record = self._store.get(normalized)
        if record is None:
            raise KeyError(f"No remote tmux session with id {normalized!r}.")
        return record

    async def _run_checked(
        self,
        *,
        host: str,
        remote_command: str,
        context: str,
    ) -> SSHCommandResult:
        result = await self._ssh_runner.run(host=host, remote_command=remote_command)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
            raise RuntimeError(f"Failed to {context}: {detail}")
        return result

    @staticmethod
    def _build_start_command(
        *,
        tmux_session_name: str,
        command: str,
        remote_cwd: str,
    ) -> str:
        root = f"$HOME/{_REMOTE_ROOT}/{tmux_session_name}"

        body_parts = [
            f'root="{root}"',
            'log_path="$root/output.log"',
            'exit_code_path="$root/exit_code"',
            'mkdir -p "$root"',
            'rm -f "$exit_code_path"',
            ': > "$log_path"',
        ]
        if remote_cwd:
            body_parts.append(f"cd {_quote(remote_cwd)}")
        body_parts.append("{ " + command + '; } >> "$log_path" 2>&1')
        body_parts.append("rc=$?")
        body_parts.append('printf \'%s\\n\' "$rc" > "$exit_code_path"')
        body_parts.append("exit $rc")
        body_script = "; ".join(body_parts)

        setup_parts = [
            (
                f"if tmux has-session -t {_quote(tmux_session_name)} 2>/dev/null; "
                "then echo 'tmux session already exists' >&2; exit 20; fi"
            ),
            (
                f"tmux new-session -d -s {_quote(tmux_session_name)} "
                f"{_quote(f'bash -lc {shlex.quote(body_script)}')}"
            ),
        ]
        return "; ".join(setup_parts)

    @staticmethod
    def _build_status_command(*, tmux_session_name: str, tail_lines: int) -> str:
        root = f"$HOME/{_REMOTE_ROOT}/{tmux_session_name}"
        meta = "__DRCLAW_META__"
        return "; ".join(
            [
                f'root="{root}"',
                'log_path="$root/output.log"',
                'exit_code_path="$root/exit_code"',
                "status=missing",
                "exit_code=",
                "output_source=missing",
                "if [ -f \"$exit_code_path\" ]; then exit_code=$(cat \"$exit_code_path\"); fi",
                (
                    "if [ -n \"$exit_code\" ]; then "
                    "if [ \"$exit_code\" = \"0\" ]; then status=succeeded; else status=failed; fi; "
                    "elif tmux has-session -t "
                    f"{_quote(tmux_session_name)}"
                    " 2>/dev/null; then status=running; fi"
                ),
                (
                    f"if [ -f \"$log_path\" ]; then output_source=log; "
                    f"printf '{meta} %s %s %s\\n' \"$status\" \"$exit_code\" \"$output_source\"; "
                    f"tail -n {int(tail_lines)} \"$log_path\"; "
                    "elif tmux has-session -t "
                    f"{_quote(tmux_session_name)}"
                    " 2>/dev/null; then output_source=capture-pane; "
                    f"printf '{meta} %s %s %s\\n' \"$status\" \"$exit_code\" \"$output_source\"; "
                    f"tmux capture-pane -p -t {_quote(tmux_session_name)} -S -{int(tail_lines)}; "
                    "else "
                    f"printf '{meta} %s %s %s\\n' \"$status\" \"$exit_code\" \"$output_source\"; fi"
                ),
            ]
        )

    @staticmethod
    def _build_terminate_command(*, tmux_session_name: str) -> str:
        return (
            f"if tmux has-session -t {_quote(tmux_session_name)} 2>/dev/null; "
            f"then tmux kill-session -t {_quote(tmux_session_name)}; fi"
        )

    @staticmethod
    def _parse_status_output(stdout: str) -> tuple[str, int | None, str, str]:
        if not stdout:
            raise RuntimeError("Remote tmux status returned no output")
        first, _, rest = stdout.partition("\n")
        prefix = "__DRCLAW_META__ "
        if not first.startswith(prefix):
            raise RuntimeError("Remote tmux status returned an invalid header")
        payload = first[len(prefix):].split(" ", 2)
        if len(payload) != 3:
            raise RuntimeError("Remote tmux status returned a malformed header")
        status, exit_code_raw, output_source = payload
        exit_code = None if not exit_code_raw else int(exit_code_raw)
        output = rest.rstrip("\n")
        if len(output) > _MAX_OUTPUT_CHARS:
            trimmed = len(output) - _MAX_OUTPUT_CHARS
            output = output[:_MAX_OUTPUT_CHARS] + f"\n... (truncated, {trimmed} more chars)"
        return status, exit_code, output_source, output


def _format_timestamp(value: datetime | None) -> str:
    return value.astimezone(timezone.utc).isoformat() if value else "n/a"


class StartRemoteTmuxSessionTool(Tool):
    """Start a remote tmux-backed long-running command over ssh."""

    def __init__(self, manager: RemoteTmuxManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "start_remote_tmux_session"

    @property
    def description(self) -> str:
        return (
            "Start a long-running remote command inside tmux over ssh. "
            "Use this for remote jobs that may outlive one response."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "SSH host or alias, such as user@host."},
                "command": {"type": "string", "description": "Remote shell command to run."},
                "remote_cwd": {
                    "type": "string",
                    "description": "Optional remote working directory before running the command.",
                },
                "session_name": {
                    "type": "string",
                    "description": "Optional tmux session name. If omitted, one is generated.",
                },
            },
            "required": ["host", "command"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        record = await self._manager.start_session(
            host=params["host"],
            command=params["command"],
            remote_cwd=params.get("remote_cwd", ""),
            session_name=params.get("session_name", ""),
        )
        lines = [
            "Remote tmux session started.",
            f"session_id={record.session_id}",
            f"host={record.host}",
            f"tmux_session_name={record.tmux_session_name}",
            f"status={record.status}",
            f"remote_cwd={record.remote_cwd or '(default shell cwd)'}",
            f"remote_log_path={record.remote_log_path}",
            f"remote_exit_code_path={record.remote_exit_code_path}",
        ]
        return "\n".join(lines)


class GetRemoteTmuxSessionStatusTool(Tool):
    """Inspect the latest output and state of a managed remote tmux session."""

    def __init__(self, manager: RemoteTmuxManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "get_remote_tmux_session_status"

    @property
    def description(self) -> str:
        return (
            "Check a managed remote tmux session by session_id and return the latest output tail."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Managed remote tmux session id.",
                },
                "tail_lines": {
                    "type": "integer",
                    "description": "Number of log lines to fetch. Default 200.",
                    "minimum": 1,
                    "maximum": 1000,
                },
            },
            "required": ["session_id"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        result = await self._manager.get_status(
            params["session_id"],
            tail_lines=params.get("tail_lines", 200),
        )
        record = result.record
        lines = [
            f"session_id={record.session_id}",
            f"host={record.host}",
            f"tmux_session_name={record.tmux_session_name}",
            f"status={record.status}",
            f"exit_code={result.exit_code if result.exit_code is not None else 'n/a'}",
            f"output_source={result.output_source}",
            f"remote_log_path={record.remote_log_path}",
            f"last_checked_at={_format_timestamp(record.last_checked_at)}",
        ]
        if result.detail:
            lines.append(f"detail={result.detail}")
        lines.append("")
        lines.append("--- Output ---")
        lines.append(result.output or "(no output)")
        return "\n".join(lines)


class ListRemoteTmuxSessionsTool(Tool):
    """List managed remote tmux sessions for the current project."""

    def __init__(self, manager: RemoteTmuxManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "list_remote_tmux_sessions"

    @property
    def description(self) -> str:
        return "List managed remote tmux sessions for this project."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "include_terminal": {
                    "type": "boolean",
                    "description": "Include succeeded, failed, and cancelled sessions.",
                },
            },
        }

    async def execute(self, params: dict[str, Any]) -> str:
        include_terminal = params.get("include_terminal", False)
        records = self._manager.list_sessions(include_terminal=include_terminal)
        if not records:
            return "No managed remote tmux sessions."
        lines = ["Managed remote tmux sessions:"]
        for rec in records:
            lines.append(
                "  "
                + " | ".join(
                    [
                        f"session_id={rec.session_id}",
                        f"host={rec.host}",
                        f"tmux_session_name={rec.tmux_session_name}",
                        f"status={rec.status}",
                    ]
                )
            )
        return "\n".join(lines)


class TerminateRemoteTmuxSessionTool(Tool):
    """Terminate a managed remote tmux session."""

    def __init__(self, manager: RemoteTmuxManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "terminate_remote_tmux_session"

    @property
    def description(self) -> str:
        return "Terminate a managed remote tmux session by session_id."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Managed remote tmux session id.",
                },
            },
            "required": ["session_id"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        record = await self._manager.terminate(params["session_id"])
        return "\n".join(
            [
                "Remote tmux session terminated.",
                f"session_id={record.session_id}",
                f"host={record.host}",
                f"tmux_session_name={record.tmux_session_name}",
                f"status={record.status}",
                f"last_checked_at={_format_timestamp(record.last_checked_at)}",
            ]
        )
