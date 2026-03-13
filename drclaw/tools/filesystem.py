"""File system tools: read, write, edit, list."""

import difflib
from pathlib import Path
from typing import Any

from drclaw.tools.base import Tool

# Fuzzy-match early-exit: stop scanning once we find a sufficiently good match.
_FUZZY_EARLY_EXIT = 0.9


def _resolve_path(
    path: str, workspace: Path | None = None, allowed_dir: Path | None = None
) -> Path:
    """Resolve path against workspace (if relative) and enforce directory restriction.

    SECURITY: Both the candidate path and allowed_dir are fully resolved (symlinks
    expanded, ..'s collapsed) before the containment check — this is the entire
    security guarantee against path-traversal and symlink-escape attacks.
    """
    p = Path(path).expanduser()
    if not p.is_absolute() and workspace:
        p = workspace / p
    resolved = p.resolve()
    if allowed_dir:
        try:
            resolved.relative_to(allowed_dir.resolve())
        except ValueError:
            raise PermissionError(f"Path {path} is outside allowed directory {allowed_dir}")
    return resolved


class _FileSystemTool(Tool):
    """Shared base for filesystem tools: workspace + allowed_dir sandboxing.

    NOTE: Constructing any subclass with allowed_dir=None disables sandbox
    enforcement entirely — write/read/list operations will accept any path on
    the filesystem. Always supply allowed_dir when running inside a project
    workspace.
    """

    def __init__(self, workspace: Path | None = None, allowed_dir: Path | None = None):
        self._workspace = workspace
        self._allowed_dir = allowed_dir


class ReadFileTool(_FileSystemTool):
    """Tool to read file contents."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read the contents of a file at the given path."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to read"},
            },
            "required": ["path"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        path: str = params["path"]
        try:
            file_path = _resolve_path(path, self._workspace, self._allowed_dir)
            if not file_path.exists():
                return f"Error: File not found: {path}"
            if not file_path.is_file():
                return f"Error: Not a file: {path}"
            return file_path.read_text(encoding="utf-8")
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: reading file: {str(e)}"


class WriteFileTool(_FileSystemTool):
    """Tool to write content to a file."""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Write content to a file at the given path. "
            "Creates parent directories if needed. "
            "Set append=true to append instead of overwrite. "
            "IMPORTANT: For large content (reports, code, data dumps), "
            "split into 600-1200 character chunks. "
            "First chunk: append=false. Subsequent chunks: append=true. "
            "This prevents output truncation errors."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to write to"},
                "content": {"type": "string", "description": "The content to write"},
                "append": {
                    "type": "boolean",
                    "description": "If true, append content to the file instead of overwriting.",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        path: str = params["path"]
        content: str = params["content"]
        append: bool = bool(params.get("append", False))
        try:
            file_path = _resolve_path(path, self._workspace, self._allowed_dir)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            if append:
                with file_path.open("a", encoding="utf-8") as f:
                    f.write(content)
                return f"Successfully appended {len(content)} bytes to {file_path}"
            file_path.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} bytes to {file_path}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: writing file: {str(e)}"


class EditFileTool(_FileSystemTool):
    """Tool to edit a file by replacing text."""

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing old_text with new_text. "
            "The old_text must exist exactly once in the file."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to edit"},
                "old_text": {"type": "string", "description": "The exact text to find and replace"},
                "new_text": {"type": "string", "description": "The text to replace with"},
            },
            "required": ["path", "old_text", "new_text"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        path: str = params["path"]
        old_text: str = params["old_text"]
        new_text: str = params["new_text"]
        try:
            file_path = _resolve_path(path, self._workspace, self._allowed_dir)
            if not file_path.exists():
                return f"Error: File not found: {path}"

            content = file_path.read_text(encoding="utf-8")

            if old_text not in content:
                return self._not_found_message(old_text, content, path)

            count = content.count(old_text)
            if count > 1:
                return (
                    f"Warning: old_text appears {count} times. "
                    "Please provide more context to make it unique."
                )

            file_path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
            return f"Successfully edited {file_path}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: editing file: {str(e)}"

    @staticmethod
    def _not_found_message(old_text: str, content: str, path: str) -> str:
        """Build a helpful error when old_text is not found.

        Scans the file for the best fuzzy match using a sliding window of the
        same line-count as old_text.  Exits early once a match exceeds
        _FUZZY_EARLY_EXIT to avoid O(n*m) cost on large files.
        """
        lines = content.splitlines(keepends=True)
        old_lines = old_text.splitlines(keepends=True)
        window = len(old_lines)

        best_ratio, best_start = 0.0, 0
        for i in range(max(1, len(lines) - window + 1)):
            ratio = difflib.SequenceMatcher(None, old_lines, lines[i : i + window]).ratio()
            if ratio > best_ratio:
                best_ratio, best_start = ratio, i
            if best_ratio >= _FUZZY_EARLY_EXIT:
                break

        if best_ratio > 0.5:
            diff = "\n".join(
                difflib.unified_diff(
                    old_lines,
                    lines[best_start : best_start + window],
                    fromfile="old_text (provided)",
                    tofile=f"{path} (actual, line {best_start + 1})",
                    lineterm="",
                )
            )
            return (
                f"Error: old_text not found in {path}.\n"
                f"Best match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
            )
        return (
            f"Error: old_text not found in {path}. No similar text found. Verify the file content."
        )


class ListDirTool(_FileSystemTool):
    """Tool to list directory contents."""

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "List the contents of a directory."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The directory path to list"},
            },
            "required": ["path"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        path: str = params["path"]
        try:
            dir_path = _resolve_path(path, self._workspace, self._allowed_dir)
            if not dir_path.exists():
                return f"Error: Directory not found: {path}"
            if not dir_path.is_dir():
                return f"Error: Not a directory: {path}"

            items = []
            for item in sorted(dir_path.iterdir()):
                prefix = "[dir] " if item.is_dir() else "[file] "
                items.append(f"{prefix}{item.name}")

            if not items:
                return f"Directory {path} is empty"

            return "\n".join(items)
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: listing directory: {str(e)}"
