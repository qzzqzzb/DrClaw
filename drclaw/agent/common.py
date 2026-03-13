"""Shared tool registration for agents."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from drclaw.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from drclaw.tools.registry import ToolRegistry
from drclaw.tools.shell import ExecTool, LongExecTool
from drclaw.tools.web import WebFetchTool, WebSearchTool


def register_common_tools(
    registry: ToolRegistry,
    workspace: Path,
    write_allowed_dir: Path,
    read_allowed_dir: Path | None = None,
    restrict_exec: bool = True,
    web_search_api_key: str | None = None,
    web_search_max_results: int = 5,
    web_search_endpoint: str = "https://google.serper.dev/search",
    env_provider: Callable[[], Mapping[str, str]] | None = None,
) -> None:
    """Register filesystem and shell tools on *registry*.

    Security model (Phase 2 preparation):

    * **Read / List** — default unrestricted (``read_allowed_dir=None``)
      so agents can read workspace/global skill directories outside the
      workspace.  Pass a ``Path`` to re-sandbox if needed.
    * **Write / Edit** — always sandboxed to *write_allowed_dir*.
    * **Exec** — ``restrict_to_workspace`` is configurable via
      *restrict_exec*.  ProjectAgent restricts commands to its workspace;
      MainAgent leaves them unrestricted since it operates at the
      top-level and has no meaningful workspace boundary.

    This is intentionally permissive for the initial version — DrClaw does
    not yet enforce hard security guarantees.  Tighten as the permission
    system lands in Phase 3.
    """
    registry.register(ReadFileTool(workspace=workspace, allowed_dir=read_allowed_dir))
    registry.register(ListDirTool(workspace=workspace, allowed_dir=read_allowed_dir))
    registry.register(WriteFileTool(workspace=workspace, allowed_dir=write_allowed_dir))
    registry.register(EditFileTool(workspace=workspace, allowed_dir=write_allowed_dir))
    registry.register(
        ExecTool(
            working_dir=workspace,
            restrict_to_workspace=restrict_exec,
            env_provider=env_provider,
        )
    )
    registry.register(
        WebSearchTool(
            api_key=web_search_api_key,
            max_results=web_search_max_results,
            endpoint=web_search_endpoint,
            env_provider=env_provider,
        )
    )
    registry.register(WebFetchTool())
    if restrict_exec:
        # Project agents get long_exec for tasks that may exceed the 60s default.
        registry.register(
            LongExecTool(
                working_dir=workspace,
                restrict_to_workspace=True,
                env_provider=env_provider,
            )
        )
