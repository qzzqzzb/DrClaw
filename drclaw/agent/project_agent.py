"""ProjectAgent — per-project orchestrator that wires subsystems together."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from drclaw.agent.common import register_common_tools
from drclaw.agent.context import ContextBuilder
from drclaw.agent.loop import AgentLoop
from drclaw.agent.memory import MemoryStore
from drclaw.agent.skills import SkillsLoader
from drclaw.claude_code.manager import ClaudeCodeSessionManager
from drclaw.config.env_store import EnvStore
from drclaw.config.schema import DrClawConfig
from drclaw.equipment.manager import EquipmentRuntimeManager
from drclaw.equipment.prototypes import EquipmentPrototypeStore
from drclaw.models.project import Project
from drclaw.providers.base import LLMProvider
from drclaw.remote_tmux.store import RemoteTmuxSessionStore
from drclaw.session.manager import SessionManager
from drclaw.soul import load_project_soul
from drclaw.tools.background_tasks import (
    BackgroundToolTaskManager,
    CancelBackgroundToolTaskTool,
    GetBackgroundToolTaskStatusTool,
    ListBackgroundToolTasksTool,
)
from drclaw.tools.claude_code_tools import (
    CloseClaudeCodeSessionTool,
    GetClaudeCodeStatusTool,
    ListClaudeCodeSessionsTool,
    SendClaudeCodeMessageTool,
    UseClaudeCodeTool,
)
from drclaw.tools.equipment_tools import (
    GetEquipmentRunStatusTool,
    ListActiveEquipmentRunsTool,
    UseEquipmentTool,
)
from drclaw.tools.external_agent_tools import CallExternalAgentTool
from drclaw.tools.remote_tmux import (
    GetRemoteTmuxSessionStatusTool,
    ListRemoteTmuxSessionsTool,
    RemoteTmuxManager,
    StartRemoteTmuxSessionTool,
    TerminateRemoteTmuxSessionTool,
)
from drclaw.tools.registry import ToolRegistry
from drclaw.utils.helpers import ensure_dir

if TYPE_CHECKING:
    from drclaw.agent.debug import DebugLogger
    from drclaw.external_agents.bridge import ExternalAgentBridge


def _build_identity(project: Project, workspace_dir: Path) -> str:
    soul = load_project_soul(workspace_dir)
    lines = [
        soul,
        "## Project Context",
        f"You are currently assigned to project '{project.name}'.",
    ]
    if project.description:
        lines.append(f"Description: {project.description}")
    if project.goals:
        lines.append(f"Goals: {project.goals}")
    if project.tags:
        lines.append(f"Tags: {', '.join(project.tags)}")
    return "\n\n".join(lines)


class ProjectAgent:
    """Orchestrator for a single research project.

    Wires together memory, sessions, tools, and the agent loop — all scoped
    to one project's directory.  The workspace sandbox (``project_dir/workspace``)
    is the security boundary for all filesystem tools.
    """

    def __init__(
        self,
        config: DrClawConfig,
        provider: LLMProvider,
        project: Project,
        debug_logger: DebugLogger | None = None,
        equipment_manager: EquipmentRuntimeManager | None = None,
        env_store: EnvStore | None = None,
        claude_code_manager: ClaudeCodeSessionManager | None = None,
        external_agent_bridge: ExternalAgentBridge | None = None,
    ) -> None:
        self.project = project

        project_dir = config.data_path / "projects" / project.id
        workspace_dir = ensure_dir(project_dir / "workspace")
        load_project_soul(workspace_dir)
        runtime_root = config.data_path / "runtime" / "equipments"
        runtime_root.mkdir(parents=True, exist_ok=True)
        self.env_store = env_store or EnvStore(
            config=config,
            config_path=config.data_path / "config.json",
        )
        self.equipment_manager = equipment_manager or EquipmentRuntimeManager(
            prototype_store=EquipmentPrototypeStore(),
            runtime_root=runtime_root,
            provider=provider,
            config=config,
            env_store=self.env_store,
        )

        self.memory_store = MemoryStore(project_dir)
        self.remote_tmux_store = RemoteTmuxSessionStore(project_dir / "remote_tmux_sessions.json")
        self.remote_tmux_manager = RemoteTmuxManager(self.remote_tmux_store)

        session_manager = SessionManager(project_dir / "sessions")
        caller_agent_id = f"proj:{project.id}"

        def env_provider() -> dict[str, str]:
            return self.env_store.get_effective_env(caller_agent_id)

        skills_loader = SkillsLoader(
            workspace=workspace_dir,
            global_skills_dir=config.data_path / "skills",
            env_provider=env_provider,
        )
        context_builder = ContextBuilder(
            lambda: _build_identity(project, workspace_dir),
            self.memory_store,
            skills_loader=skills_loader,
            project_notes_file=project_dir / "PROJECT.md",
        )

        tool_registry = ToolRegistry()
        register_common_tools(
            tool_registry,
            workspace=workspace_dir,
            write_allowed_dir=workspace_dir,
            web_search_api_key=config.tools.web.serper.api_key,
            web_search_max_results=config.tools.web.serper.max_results,
            web_search_endpoint=config.tools.web.serper.endpoint,
            env_provider=env_provider,
        )
        tool_registry.register(
            UseEquipmentTool(self.equipment_manager, caller_agent_id=caller_agent_id)
        )
        tool_registry.register(
            ListActiveEquipmentRunsTool(
                self.equipment_manager, caller_agent_id=caller_agent_id
            )
        )
        tool_registry.register(GetEquipmentRunStatusTool(self.equipment_manager))
        if external_agent_bridge is not None:
            tool_registry.register(
                CallExternalAgentTool(
                    external_agent_bridge,
                    caller_agent_id=caller_agent_id,
                )
            )
        self.background_task_manager = BackgroundToolTaskManager(
            caller_agent_id=caller_agent_id
        )
        tool_registry.register(ListBackgroundToolTasksTool(self.background_task_manager))
        tool_registry.register(GetBackgroundToolTaskStatusTool(self.background_task_manager))
        tool_registry.register(CancelBackgroundToolTaskTool(self.background_task_manager))
        tool_registry.register(StartRemoteTmuxSessionTool(self.remote_tmux_manager))
        tool_registry.register(GetRemoteTmuxSessionStatusTool(self.remote_tmux_manager))
        tool_registry.register(ListRemoteTmuxSessionsTool(self.remote_tmux_manager))
        tool_registry.register(TerminateRemoteTmuxSessionTool(self.remote_tmux_manager))

        self.claude_code_manager = claude_code_manager
        if config.claude_code.enabled and self.claude_code_manager is not None:
            expose = config.claude_code.expose_tools_to_sessions

            def _mcp_tools_provider() -> list:
                if not expose:
                    return []
                all_tools = list(tool_registry._tools.values())  # noqa: SLF001
                if expose == ["*"]:
                    return all_tools
                return [t for t in all_tools if t.name in expose]

            tool_registry.register(
                UseClaudeCodeTool(
                    self.claude_code_manager,
                    caller_agent_id=caller_agent_id,
                    default_cwd=str(workspace_dir),
                    mcp_tools_provider=_mcp_tools_provider,
                )
            )
            tool_registry.register(
                SendClaudeCodeMessageTool(
                    self.claude_code_manager, caller_agent_id=caller_agent_id,
                )
            )
            tool_registry.register(GetClaudeCodeStatusTool(self.claude_code_manager))
            tool_registry.register(
                ListClaudeCodeSessionsTool(
                    self.claude_code_manager, caller_agent_id=caller_agent_id,
                )
            )
            tool_registry.register(
                CloseClaudeCodeSessionTool(
                    self.claude_code_manager, caller_agent_id=caller_agent_id,
                )
            )

        self.loop = AgentLoop(
            provider=provider,
            tool_registry=tool_registry,
            context_builder=context_builder,
            session_manager=session_manager,
            memory_store=self.memory_store,
            max_iterations=config.agent.max_iterations,
            memory_window=config.agent.memory_window,
            max_history=config.agent.max_history,
            debug_logger=debug_logger,
            agent_id=caller_agent_id,
            background_task_manager=self.background_task_manager,
            tool_detach_timeout_seconds=config.agent.tool_detach_timeout_seconds,
        )

    async def process_direct(self, message: str) -> str:
        """Process a user message and return the agent's response."""
        session_key = f"proj:{self.project.id}"
        return await self.loop.process_direct(message, session_key)
