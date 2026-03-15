"""Shell execution tool."""

import asyncio
import os
import re
import shlex
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from drclaw.tools.base import Tool

_MAX_OUTPUT = 10_000

# Paths that should never trigger the workspace sandbox guard (e.g. /dev/null
# used in redirections like ``2>/dev/null``).
_SAFE_DEV_PATHS = frozenset(
    Path(p) for p in ("/dev/null", "/dev/zero", "/dev/urandom", "/dev/random", "/dev/stdin", "/dev/stdout", "/dev/stderr")
)


class ExecTool(Tool):
    """Tool to execute shell commands with safety guards."""

    _DEFAULT_DENY: list[str] = [
        r"\bdel\s+/[fq]\b",  # del /f, del /q
        r"\brmdir\s+/s\b",  # rmdir /s
        r"(?:^|[;&|]\s*)format\b",  # format (standalone command only)
        r"\b(mkfs|diskpart)\b",  # disk operations
        r"\bdd\s+if=",  # dd
        r">\s*/dev/sd",  # write to disk
        r"\b(shutdown|reboot|poweroff)\b",  # system power
        r":\(\)\s*\{.*\};\s*:",  # fork bomb
    ]

    def __init__(
        self,
        timeout: int = 60,
        working_dir: Path | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        path_append: str = "",
        env_provider: Callable[[], Mapping[str, str]] | None = None,
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.deny_patterns = (
            deny_patterns if deny_patterns is not None else list(self._DEFAULT_DENY)
        )
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        self.path_append = path_append
        self.env_provider = env_provider

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "Execute a shell command and return its output. Use with caution."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command",
                },
            },
            "required": ["command"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        command: str = params["command"]
        working_dir: str | None = params.get("working_dir")
        cwd, cwd_error = self._resolve_cwd(working_dir)
        if cwd_error:
            return cwd_error
        guard_error = self._guard_command(command, str(cwd))
        if guard_error:
            return guard_error

        env = os.environ.copy()
        if self.env_provider is not None:
            env.update(dict(self.env_provider()))
        if self.path_append:
            env["PATH"] = env.get("PATH", "") + os.pathsep + self.path_append

        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
            except asyncio.TimeoutError:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                return f"Error: Command timed out after {self.timeout} seconds"
            except asyncio.CancelledError:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                raise

            parts: list[str] = []
            if stdout:
                parts.append(stdout.decode("utf-8", errors="replace"))
            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    parts.append(f"STDERR:\n{stderr_text}")
            if process.returncode != 0:
                parts.append(f"\nExit code: {process.returncode}")

            result = "\n".join(parts) if parts else "(no output)"
            if len(result) > _MAX_OUTPUT:
                trimmed = len(result) - _MAX_OUTPUT
                result = result[:_MAX_OUTPUT] + f"\n... (truncated, {trimmed} more chars)"
            return result

        except asyncio.CancelledError:
            # Keep cancellation semantics for detached task manager.
            raise
        except Exception as e:
            if process and process.returncode is None:
                process.kill()
            return f"Error: executing command: {str(e)}"

    def _resolve_cwd(self, working_dir: str | None) -> tuple[str | Path, str | None]:
        """Resolve the execution cwd and keep restricted tools inside their workspace."""
        default_cwd: str | Path = self.working_dir or os.getcwd()
        if not self.restrict_to_workspace:
            return working_dir or default_cwd, None

        workspace_root = Path(default_cwd).resolve()
        if working_dir is None:
            return workspace_root, None

        requested = Path(working_dir).expanduser()
        if not requested.is_absolute():
            requested = workspace_root / requested
        resolved = requested.resolve()
        try:
            resolved.relative_to(workspace_root)
        except ValueError:
            return "", "Error: Command blocked by safety guard (working dir outside workspace)"
        return resolved, None

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort safety guard for potentially destructive commands.

        NOTE: restrict_to_workspace is a guardrail against accidental misuse,
        NOT a security boundary. It can be bypassed via shell expansion
        (``$(echo '../')``), symlinks, environment variables, and other shell
        features. Do not rely on it to sandbox untrusted input.
        """
        # Pattern matching is case-insensitive — operate on the lowered string.
        lower = command.strip().lower()

        if self._matches_dangerous_rm(command):
            return "Error: Command blocked by safety guard (dangerous pattern detected)"

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        if self.restrict_to_workspace:
            if "..\\" in command or "../" in command:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()
            # Path extraction operates on the original command (not lowered) because
            # filesystem paths are case-sensitive on POSIX and need their original case.
            for raw in self._extract_absolute_paths(command):
                try:
                    p = Path(raw.strip()).resolve()
                except Exception:
                    continue
                if p.is_absolute() and p not in _SAFE_DEV_PATHS and cwd_path not in p.parents and p != cwd_path:
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None

    @staticmethod
    def _extract_absolute_paths(command: str) -> list[str]:
        """Extract absolute path literals from a shell command string.

        Operates on the original (non-lowered) command because paths are
        case-sensitive on POSIX systems.
        """
        win_paths = re.findall(r"[A-Za-z]:\\[^\s\"'|><;]+", command)
        posix_paths = re.findall(r"(?:^|[\s|>])(/[^\s\"'>]+)", command)
        quoted_win_paths = re.findall(r'"([A-Za-z]:\\[^"]+)"', command)
        quoted_win_paths.extend(re.findall(r"'([A-Za-z]:\\[^']+)'", command))
        quoted_posix_paths = re.findall(r'"(/[^"]+)"', command)
        quoted_posix_paths.extend(re.findall(r"'(/[^']+)'", command))
        return list(dict.fromkeys(win_paths + posix_paths + quoted_win_paths + quoted_posix_paths))

    @staticmethod
    def _matches_dangerous_rm(command: str) -> bool:
        """Detect rm commands using force/recursive flags, including long options."""
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            return False

        force_flags = {"-f", "--force"}
        recursive_flags = {"-r", "-R", "--recursive"}
        command_breaks = {";", "&&", "||", "|", "&"}

        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token in command_breaks:
                i += 1
                continue

            if Path(token).name != "rm":
                i += 1
                continue

            j = i + 1
            while j < len(tokens) and tokens[j] not in command_breaks:
                arg = tokens[j]
                if arg == "--":
                    break
                if arg.startswith("--"):
                    if arg in force_flags or arg in recursive_flags:
                        return True
                    j += 1
                    continue
                if arg.startswith("-") and len(arg) > 1:
                    short_flags = set(arg[1:])
                    if "f" in short_flags or "r" in short_flags or "R" in short_flags:
                        return True
                    j += 1
                    continue
                j += 1
            i = j + 1

        return False


class LongExecTool(ExecTool):
    """ExecTool variant with a 20-minute timeout for long-running tasks."""

    def __init__(self, **kwargs: Any):
        kwargs.setdefault("timeout", 1200)
        super().__init__(**kwargs)

    @property
    def name(self) -> str:
        return "long_exec"

    @property
    def description(self) -> str:
        return (
            "Execute a long-running shell command (up to 20 minutes). "
            "Use this instead of exec when the command may take more than 60 seconds."
        )
