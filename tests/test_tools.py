"""Tests for the tool system: base, registry, filesystem, and shell tools."""

import asyncio
from pathlib import Path

import pytest

from drclaw.agent.common import register_common_tools
from drclaw.tools.base import Tool
from drclaw.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
    _FileSystemTool,
    _resolve_path,
)
from drclaw.tools.registry import ToolRegistry
from drclaw.tools.shell import ExecTool, LongExecTool

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


class _EchoTool(Tool):
    """Minimal concrete Tool for testing the base class and registry."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo back the message."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        }

    async def execute(self, params: dict) -> str:
        return params["message"]


class _CountTool(Tool):
    """Tool with an integer parameter for testing numeric validation."""

    @property
    def name(self) -> str:
        return "count"

    @property
    def description(self) -> str:
        return "Count things."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
        }

    async def execute(self, params: dict) -> str:
        return str(params["n"])


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


# ---------------------------------------------------------------------------
# _resolve_path — security contract
# ---------------------------------------------------------------------------


class TestResolvePath:
    def test_absolute_path_inside_allowed(self, workspace: Path) -> None:
        f = workspace / "file.txt"
        result = _resolve_path(str(f), allowed_dir=workspace)
        assert result == f.resolve()

    def test_relative_path_resolved_via_workspace(self, workspace: Path) -> None:
        result = _resolve_path("sub/file.txt", workspace=workspace, allowed_dir=workspace)
        assert result == (workspace / "sub" / "file.txt").resolve()

    def test_dotdot_escape_raises_permission_error(self, workspace: Path) -> None:
        with pytest.raises(PermissionError):
            _resolve_path("../secret.txt", workspace=workspace, allowed_dir=workspace)

    def test_absolute_path_outside_allowed_raises(self, workspace: Path, tmp_path: Path) -> None:
        outside = tmp_path / "outside.txt"
        with pytest.raises(PermissionError):
            _resolve_path(str(outside), allowed_dir=workspace)

    def test_symlink_escape_raises_permission_error(self, workspace: Path, tmp_path: Path) -> None:
        """A symlink inside workspace pointing outside must be caught after resolve()."""
        target = tmp_path / "secret.txt"
        target.write_text("secret", encoding="utf-8")
        link = workspace / "link.txt"
        link.symlink_to(target)
        with pytest.raises(PermissionError):
            _resolve_path(str(link), allowed_dir=workspace)

    def test_no_allowed_dir_permits_any_path(self, workspace: Path) -> None:
        result = _resolve_path(str(workspace / "x.txt"))
        assert result == (workspace / "x.txt").resolve()

    def test_allowed_dir_itself_is_permitted(self, workspace: Path) -> None:
        # The workspace root should be considered "inside" itself
        result = _resolve_path(str(workspace), allowed_dir=workspace)
        assert result == workspace.resolve()


# ---------------------------------------------------------------------------
# _FileSystemTool base class
# ---------------------------------------------------------------------------


class TestFileSystemToolBase:
    def test_subclasses_share_init(self, workspace: Path) -> None:
        """All filesystem tool subclasses must accept workspace/allowed_dir."""
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            tool = cls(workspace=workspace, allowed_dir=workspace)
            assert isinstance(tool, _FileSystemTool)
            assert tool._workspace == workspace
            assert tool._allowed_dir == workspace


# ---------------------------------------------------------------------------
# Tool base class — validation
# ---------------------------------------------------------------------------


class TestToolBase:
    def test_to_schema(self) -> None:
        tool = _EchoTool()
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "echo"
        assert "message" in schema["function"]["parameters"]["properties"]

    def test_validate_params_ok(self) -> None:
        tool = _EchoTool()
        assert tool.validate_params({"message": "hi"}) == []

    def test_validate_params_missing_required(self) -> None:
        tool = _EchoTool()
        errors = tool.validate_params({})
        assert any("message" in e for e in errors)

    def test_validate_params_wrong_type(self) -> None:
        tool = _EchoTool()
        errors = tool.validate_params({"message": 42})
        assert errors

    def test_validate_params_bool_rejected_for_integer(self) -> None:
        """True/False must not pass integer validation (bool is subclass of int in Python)."""
        tool = _CountTool()
        errors = tool.validate_params({"n": True})
        assert errors
        assert any("bool" in e for e in errors)

    def test_validate_params_bool_rejected_for_number(self) -> None:
        """True/False must not pass number validation."""

        class _NumTool(Tool):
            @property
            def name(self) -> str:
                return "num"

            @property
            def description(self) -> str:
                return ""

            @property
            def parameters(self) -> dict:
                return {
                    "type": "object",
                    "properties": {"x": {"type": "number"}},
                    "required": ["x"],
                }

            async def execute(self, params: dict) -> str:
                return ""

        errors = _NumTool().validate_params({"x": False})
        assert errors
        assert any("bool" in e for e in errors)

    def test_validate_params_integer_value_ok(self) -> None:
        tool = _CountTool()
        assert tool.validate_params({"n": 5}) == []


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class TestToolRegistry:
    def test_register_and_has(self) -> None:
        reg = ToolRegistry()
        reg.register(_EchoTool())
        assert reg.has("echo")
        assert "echo" in reg

    def test_get_returns_tool(self) -> None:
        reg = ToolRegistry()
        tool = _EchoTool()
        reg.register(tool)
        assert reg.get("echo") is tool

    def test_get_unknown_returns_none(self) -> None:
        reg = ToolRegistry()
        assert reg.get("nope") is None

    def test_unregister(self) -> None:
        reg = ToolRegistry()
        reg.register(_EchoTool())
        reg.unregister("echo")
        assert not reg.has("echo")

    def test_tool_names(self) -> None:
        reg = ToolRegistry()
        reg.register(_EchoTool())
        assert "echo" in reg.tool_names

    def test_get_definitions(self) -> None:
        reg = ToolRegistry()
        reg.register(_EchoTool())
        defs = reg.get_definitions()
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "echo"

    def test_len(self) -> None:
        reg = ToolRegistry()
        assert len(reg) == 0
        reg.register(_EchoTool())
        assert len(reg) == 1

    async def test_execute_success(self) -> None:
        reg = ToolRegistry()
        reg.register(_EchoTool())
        result = await reg.execute("echo", {"message": "hello"})
        assert result == "hello"

    async def test_execute_unknown_tool(self) -> None:
        reg = ToolRegistry()
        result = await reg.execute("nope", {})
        assert "not found" in result.lower()

    async def test_execute_invalid_params(self) -> None:
        reg = ToolRegistry()
        reg.register(_EchoTool())
        result = await reg.execute("echo", {})  # missing required 'message'
        assert "Error" in result

    async def test_execute_error_result_appends_hint(self) -> None:
        """A tool returning 'Error: ...' gets the retry hint appended."""
        reg = ToolRegistry()
        reg.register(_EchoTool())
        result = await reg.execute("echo", {"message": "Error: something went wrong"})
        assert "Analyze the error" in result

    async def test_execute_non_error_content_no_hint(self) -> None:
        """Content that merely contains 'Error' without the colon-space prefix is NOT flagged."""
        reg = ToolRegistry()
        reg.register(_EchoTool())
        result = await reg.execute("echo", {"message": "Error handling in Python is important"})
        assert "Analyze the error" not in result
        assert result == "Error handling in Python is important"


# ---------------------------------------------------------------------------
# ReadFileTool
# ---------------------------------------------------------------------------


class TestReadFileTool:
    async def test_read_existing_file(self, workspace: Path) -> None:
        f = workspace / "hello.txt"
        f.write_text("hello world", encoding="utf-8")
        tool = ReadFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": str(f)})
        assert result == "hello world"

    async def test_read_relative_path(self, workspace: Path) -> None:
        (workspace / "rel.txt").write_text("relative", encoding="utf-8")
        tool = ReadFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": "rel.txt"})
        assert result == "relative"

    async def test_read_nonexistent_file(self, workspace: Path) -> None:
        tool = ReadFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": "missing.txt"})
        assert result.startswith("Error: ")

    async def test_read_directory_returns_error(self, workspace: Path) -> None:
        tool = ReadFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": str(workspace)})
        assert result.startswith("Error: ")

    async def test_read_outside_workspace_blocked(self, workspace: Path, tmp_path: Path) -> None:
        outside = tmp_path / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        tool = ReadFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": str(outside)})
        assert result.startswith("Error: ")


# ---------------------------------------------------------------------------
# WriteFileTool
# ---------------------------------------------------------------------------


class TestWriteFileTool:
    async def test_write_new_file(self, workspace: Path) -> None:
        tool = WriteFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": "new.txt", "content": "hello"})
        assert "Successfully" in result
        assert (workspace / "new.txt").read_text() == "hello"

    async def test_write_creates_parent_dirs(self, workspace: Path) -> None:
        tool = WriteFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": "a/b/c.txt", "content": "deep"})
        assert "Successfully" in result
        assert (workspace / "a" / "b" / "c.txt").read_text() == "deep"

    async def test_write_overwrites_existing(self, workspace: Path) -> None:
        f = workspace / "existing.txt"
        f.write_text("old", encoding="utf-8")
        tool = WriteFileTool(workspace=workspace, allowed_dir=workspace)
        await tool.execute({"path": str(f), "content": "new"})
        assert f.read_text() == "new"

    async def test_write_append_mode(self, workspace: Path) -> None:
        f = workspace / "append.txt"
        f.write_text("head\n", encoding="utf-8")
        tool = WriteFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": str(f), "content": "tail\n", "append": True})
        assert "Successfully appended" in result
        assert f.read_text(encoding="utf-8") == "head\ntail\n"

    async def test_write_outside_workspace_blocked(self, workspace: Path, tmp_path: Path) -> None:
        outside = tmp_path / "evil.txt"
        tool = WriteFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": str(outside), "content": "evil"})
        assert result.startswith("Error: ")
        assert not outside.exists()


# ---------------------------------------------------------------------------
# EditFileTool
# ---------------------------------------------------------------------------


class TestEditFileTool:
    async def test_edit_replaces_text(self, workspace: Path) -> None:
        f = workspace / "edit.txt"
        f.write_text("hello world", encoding="utf-8")
        tool = EditFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": str(f), "old_text": "world", "new_text": "there"})
        assert "Successfully" in result
        assert f.read_text() == "hello there"

    async def test_edit_not_found_returns_error(self, workspace: Path) -> None:
        f = workspace / "edit.txt"
        f.write_text("hello world", encoding="utf-8")
        tool = EditFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": str(f), "old_text": "goodbye", "new_text": "hi"})
        assert result.startswith("Error: ")

    async def test_edit_multiple_occurrences_returns_warning(self, workspace: Path) -> None:
        f = workspace / "multi.txt"
        f.write_text("foo foo foo", encoding="utf-8")
        tool = EditFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": str(f), "old_text": "foo", "new_text": "bar"})
        assert "Warning" in result or "3" in result

    async def test_edit_nonexistent_file_returns_error(self, workspace: Path) -> None:
        tool = EditFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": "ghost.txt", "old_text": "x", "new_text": "y"})
        assert result.startswith("Error: ")

    async def test_edit_outside_workspace_blocked(self, workspace: Path, tmp_path: Path) -> None:
        outside = tmp_path / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        tool = EditFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute(
            {"path": str(outside), "old_text": "secret", "new_text": "pwned"}
        )
        assert result.startswith("Error: ")
        assert outside.read_text() == "secret"

    async def test_edit_fuzzy_hint_on_near_miss(self, workspace: Path) -> None:
        """_not_found_message should provide a diff hint when old_text is close."""
        f = workspace / "hint.txt"
        f.write_text("def hello():\n    pass\n", encoding="utf-8")
        tool = EditFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute(
            {"path": str(f), "old_text": "def helo():\n    pass\n", "new_text": ""}
        )
        # Should detect the near-miss and report similarity
        assert "similar" in result or "match" in result


# ---------------------------------------------------------------------------
# ListDirTool
# ---------------------------------------------------------------------------


class TestListDirTool:
    async def test_list_directory(self, workspace: Path) -> None:
        (workspace / "a.txt").write_text("", encoding="utf-8")
        (workspace / "subdir").mkdir()
        tool = ListDirTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": str(workspace)})
        assert "a.txt" in result
        assert "subdir" in result

    async def test_list_empty_directory(self, workspace: Path) -> None:
        empty = workspace / "empty"
        empty.mkdir()
        tool = ListDirTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": str(empty)})
        assert "empty" in result.lower()

    async def test_list_nonexistent_dir_returns_error(self, workspace: Path) -> None:
        tool = ListDirTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": "nope"})
        assert result.startswith("Error: ")

    async def test_list_file_as_dir_returns_error(self, workspace: Path) -> None:
        f = workspace / "file.txt"
        f.write_text("x", encoding="utf-8")
        tool = ListDirTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": str(f)})
        assert result.startswith("Error: ")

    async def test_list_dir_shows_type_prefix(self, workspace: Path) -> None:
        (workspace / "f.txt").write_text("", encoding="utf-8")
        (workspace / "d").mkdir()
        tool = ListDirTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": str(workspace)})
        assert "[file]" in result
        assert "[dir]" in result

    async def test_list_outside_workspace_blocked(self, workspace: Path, tmp_path: Path) -> None:
        tool = ListDirTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute({"path": str(tmp_path)})
        assert result.startswith("Error: ")


# ---------------------------------------------------------------------------
# ExecTool
# ---------------------------------------------------------------------------


class TestExecTool:
    async def test_execute_simple_command(self) -> None:
        tool = ExecTool()
        result = await tool.execute({"command": "echo hello"})
        assert "hello" in result

    async def test_execute_with_working_dir_str(self, workspace: Path) -> None:
        tool = ExecTool()
        result = await tool.execute({"command": "pwd", "working_dir": str(workspace)})
        assert str(workspace.resolve()) in result

    async def test_execute_with_working_dir_path(self, workspace: Path) -> None:
        """Constructor working_dir accepts Path objects (consistent with filesystem tools)."""
        tool = ExecTool(working_dir=workspace)
        result = await tool.execute({"command": "pwd"})
        assert str(workspace.resolve()) in result

    async def test_execute_stderr_included(self) -> None:
        tool = ExecTool()
        result = await tool.execute({"command": "echo err >&2"})
        assert "err" in result

    async def test_execute_nonzero_exit_code_included(self) -> None:
        tool = ExecTool()
        result = await tool.execute({"command": "exit 1"})
        assert "Exit code" in result or "1" in result

    async def test_execute_injects_env_provider(self) -> None:
        tool = ExecTool(env_provider=lambda: {"RBOT_TEST_ENV": "from-store"})
        result = await tool.execute({"command": "printf '%s' \"$RBOT_TEST_ENV\""})
        assert "from-store" in result

    async def test_execute_timeout(self) -> None:
        tool = ExecTool(timeout=1)
        result = await tool.execute({"command": "sleep 10"})
        assert "timed out" in result.lower()

    async def test_cancel_kills_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeProcess:
            def __init__(self) -> None:
                self.returncode = None
                self.killed = False

            async def communicate(self) -> tuple[bytes, bytes]:
                await asyncio.sleep(10)
                return b"", b""

            def kill(self) -> None:
                self.killed = True
                self.returncode = -9

            async def wait(self) -> int:
                return -9

        proc = _FakeProcess()

        async def _fake_create_subprocess_shell(*args, **kwargs):  # type: ignore[no-untyped-def]
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_create_subprocess_shell)

        tool = ExecTool(timeout=60)
        task = asyncio.create_task(tool.execute({"command": "sleep 10"}))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert proc.killed is True

    async def test_deny_pattern_rm_rf(self) -> None:
        tool = ExecTool()
        result = await tool.execute({"command": "rm -rf /"})
        assert "blocked" in result.lower()

    async def test_deny_pattern_fork_bomb(self) -> None:
        tool = ExecTool()
        result = await tool.execute({"command": ":() { :|: & };:"})
        assert "blocked" in result.lower()

    async def test_deny_pattern_dd(self) -> None:
        tool = ExecTool()
        result = await tool.execute({"command": "dd if=/dev/zero of=/dev/sda"})
        assert "blocked" in result.lower()

    async def test_allow_patterns_blocks_unlisted(self) -> None:
        tool = ExecTool(allow_patterns=[r"^echo\b"])
        result = await tool.execute({"command": "ls -la"})
        assert "blocked" in result.lower()

    async def test_allow_patterns_permits_listed(self) -> None:
        tool = ExecTool(allow_patterns=[r"^echo\b"])
        result = await tool.execute({"command": "echo allowed"})
        assert "allowed" in result

    async def test_restrict_to_workspace_blocks_traversal(self, workspace: Path) -> None:
        tool = ExecTool(restrict_to_workspace=True, working_dir=workspace)
        result = await tool.execute({"command": "cat ../secret"})
        assert "blocked" in result.lower()

    async def test_restrict_to_workspace_allows_dev_null(self, workspace: Path) -> None:
        tool = ExecTool(restrict_to_workspace=True, working_dir=workspace)
        result = await tool.execute({"command": f"ls {workspace} 2>/dev/null"})
        assert "blocked" not in result.lower()

    async def test_output_truncated_at_10k(self) -> None:
        tool = ExecTool()
        result = await tool.execute({"command": "python3 -c \"print('x' * 20000)\""})
        assert "truncated" in result

    async def test_no_output_returns_placeholder(self) -> None:
        tool = ExecTool()
        result = await tool.execute({"command": "true"})
        assert result  # must return something, even if no stdout/stderr

    async def test_custom_deny_patterns(self) -> None:
        tool = ExecTool(deny_patterns=[r"\bcat\b"])
        result = await tool.execute({"command": "cat /etc/passwd"})
        assert "blocked" in result.lower()

    async def test_empty_deny_patterns_allows_all(self) -> None:
        tool = ExecTool(deny_patterns=[])
        result = await tool.execute({"command": "echo yes"})
        assert "yes" in result

    async def test_tool_schema(self) -> None:
        tool = ExecTool()
        schema = tool.to_schema()
        assert schema["function"]["name"] == "exec"
        assert "command" in schema["function"]["parameters"]["properties"]


class TestLongExecTool:
    def test_name_is_long_exec(self) -> None:
        tool = LongExecTool()
        assert tool.name == "long_exec"

    def test_default_timeout_1200(self) -> None:
        tool = LongExecTool()
        assert tool.timeout == 1200

    def test_custom_timeout(self) -> None:
        tool = LongExecTool(timeout=600)
        assert tool.timeout == 600

    async def test_execute_simple_command(self) -> None:
        tool = LongExecTool()
        result = await tool.execute({"command": "echo long_running"})
        assert "long_running" in result

    def test_inherits_deny_patterns(self) -> None:
        tool = LongExecTool()
        result = tool._guard_command("rm -rf /", "/tmp")
        assert result and "blocked" in result.lower()

    def test_tool_schema(self) -> None:
        tool = LongExecTool()
        schema = tool.to_schema()
        assert schema["function"]["name"] == "long_exec"


# ---------------------------------------------------------------------------
# register_common_tools
# ---------------------------------------------------------------------------


class TestRegisterCommonTools:
    def test_registers_five_tools_unrestricted(self, workspace: Path) -> None:
        """MainAgent (restrict_exec=False) gets 7 tools, no long_exec."""
        reg = ToolRegistry()
        register_common_tools(
            reg, workspace=workspace, write_allowed_dir=workspace, restrict_exec=False
        )
        assert len(reg) == 7
        assert set(reg.tool_names) == {
            "read_file",
            "write_file",
            "edit_file",
            "list_dir",
            "exec",
            "web_search",
            "web_fetch",
        }

    def test_registers_six_tools_restricted(self, workspace: Path) -> None:
        """ProjectAgent (restrict_exec=True) gets 8 tools including long_exec."""
        reg = ToolRegistry()
        register_common_tools(reg, workspace=workspace, write_allowed_dir=workspace)
        assert len(reg) == 8
        assert "long_exec" in reg.tool_names

    async def test_read_unrestricted(self, workspace: Path, tmp_path: Path) -> None:
        outside = tmp_path / "outside.txt"
        outside.write_text("visible", encoding="utf-8")
        reg = ToolRegistry()
        register_common_tools(reg, workspace=workspace, write_allowed_dir=workspace)
        result = await reg.execute("read_file", {"path": str(outside)})
        assert result == "visible"

    async def test_list_unrestricted(self, workspace: Path, tmp_path: Path) -> None:
        reg = ToolRegistry()
        register_common_tools(reg, workspace=workspace, write_allowed_dir=workspace)
        result = await reg.execute("list_dir", {"path": str(tmp_path)})
        assert "workspace" in result  # tmp_path contains the workspace subdir

    async def test_write_sandboxed(self, workspace: Path, tmp_path: Path) -> None:
        outside = tmp_path / "evil.txt"
        reg = ToolRegistry()
        register_common_tools(reg, workspace=workspace, write_allowed_dir=workspace)
        result = await reg.execute("write_file", {"path": str(outside), "content": "bad"})
        assert result.startswith("Error: ")
        assert not outside.exists()
