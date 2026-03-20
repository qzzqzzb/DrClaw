"""Project-scoped agents: one manager plus optional student agents per project."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
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
from drclaw.models.messages import InboundMessage
from drclaw.models.project import Project, StudentAgentConfig
from drclaw.providers.base import LLMProvider
from drclaw.remote_tmux.store import RemoteTmuxSessionStore
from drclaw.sandbox.backends import DockerSandboxBackend
from drclaw.sandbox.manager import SandboxJobManager
from drclaw.session.manager import SessionManager
from drclaw.soul import PROJECT_SOUL_TEMPLATE, load_project_soul, load_student_soul
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
from drclaw.tools.project_internal_tools import RouteToStudentTool
from drclaw.tools.remote_tmux import (
    GetRemoteTmuxSessionStatusTool,
    ListRemoteTmuxSessionsTool,
    RemoteTmuxManager,
    StartRemoteTmuxSessionTool,
    TerminateRemoteTmuxSessionTool,
)
from drclaw.tools.registry import ToolRegistry
from drclaw.tools.sandbox_jobs import (
    CancelSandboxJobTool,
    CreateJobTool,
    GetSandboxJobStatusTool,
    ListActiveSandboxJobsTool,
    PauseSandboxJobTool,
    ResumeSandboxJobTool,
)
from drclaw.utils.helpers import ensure_dir

if TYPE_CHECKING:
    from drclaw.agent.debug import DebugLogger
    from drclaw.external_agents.bridge import ExternalAgentBridge


def project_manager_agent_id(project_id: str) -> str:
    return f"proj:{project_id}"


def project_student_agent_id(project_id: str, student_id: str) -> str:
    return f"student:{project_id}:{student_id}"


def project_student_state_dir(project_dir: Path, student_id: str) -> Path:
    return project_dir / "agents" / student_id


def project_student_skills_dir(project_dir: Path, student_id: str) -> Path:
    return project_student_state_dir(project_dir, student_id) / "skills"


def _build_acpx_guidance(
    config: DrClawConfig,
    workspace_dir: Path,
    *,
    project_id: str,
) -> str:
    if not config.acpx.enabled:
        return ""

    exec_tool = "long_exec" if config.acpx.prefer_long_exec else "exec"
    fallback_tool = "exec" if exec_tool == "long_exec" else "long_exec"
    cmd = config.acpx.command.strip() or "acpx"
    agent = config.acpx.default_agent.strip() or "codex"
    session_prefix = f"drclaw-proj-{project_id}-"

    return "\n\n".join(
        [
            "## ACPX Access",
            "ACPX access is enabled on this host. There is no dedicated ACPX tool in DrClaw.",
            (
                f"Use the existing `{exec_tool}` tool by default to run standard "
                f"`{cmd}` CLI commands. Use `{fallback_tool}` only when appropriate."
            ),
            f"Default binary: `{cmd}`",
            f"Default ACPX agent: `{agent}`",
            f"Default working directory: `{workspace_dir}`",
            f"Required session prefix for sessions you create: `{session_prefix}`",
            "Recommended command patterns:",
            f"- One-shot task: `{cmd} {agent} exec '<instruction>'`",
            (
                f"- Ensure session: `{cmd} {agent} sessions ensure --name "
                f"{session_prefix}<task-suffix>`"
            ),
            (
                f"- Reuse session: `{cmd} {agent} -s {session_prefix}<task-suffix> "
                f"'<instruction>'`"
            ),
            f"- Status: `{cmd} {agent} -s {session_prefix}<task-suffix> status`",
            f"- Cancel: `{cmd} {agent} -s {session_prefix}<task-suffix> cancel`",
            f"- Close: `{cmd} {agent} sessions close {session_prefix}<task-suffix>`",
            (
                f"Every persistent ACPX session you create for this project must start with "
                f"`{session_prefix}`."
            ),
            "Reuse stable session names for ongoing tasks and do not create duplicate sessions.",
            "If the global `acpx` skill is available, read it before starting a non-trivial Codex workflow.",
        ]
    )


def _manager_delegation_guidance(project: Project) -> str:
    if not project.student_agents:
        return (
            "## Delegation Policy\n"
            "- No student agents are configured for this project right now.\n"
            "- Execute work yourself when it is safe and appropriate."
        )

    available = [
        f"- `{student.id}`: {student.label}"
        for student in project.student_agents
        if student.enabled
    ]
    if not available:
        available = ["- All configured student agents are currently disabled."]

    return "\n\n".join(
        [
            "## Delegation Policy",
            "- You are the project manager for this project.",
            "- Prefer delegating executable work to student agents with `route_to_student`.",
            "- When you call `route_to_student`, use one of the exact student ids listed below. Do not invent placeholder ids or aliases.",
            "- Only do the work yourself when no suitable student exists, all suitable students fail, or the project has no enabled students.",
            "- After routing work to a student, you MUST NOT perform the same task yourself.",
            "- After routing work to a student, do NOT poll for status with `list_background_tool_tasks`, `list_active_jobs`, or `exec`/`sleep` just to monitor progress.",
            "- Your only job after routing is to wait for the student's report, then summarize or relay the result upward.",
            "- When you receive a `Student report from ...` callback, treat it as the student's result and respond directly unless the report explicitly says more work will continue.",
            "- If neither you nor any student can complete the task, report the blocker upward clearly.",
            "## Available Students",
            "\n".join(available),
        ]
    )


def _build_manager_identity(project: Project, workspace_dir: Path, config: DrClawConfig) -> str:
    soul = load_project_soul(workspace_dir)
    lines = [
        soul,
        "## Project Context",
        f"You are currently assigned to project '{project.name}' as its manager agent.",
    ]
    if project.description:
        lines.append(f"Description: {project.description}")
    if project.goals:
        lines.append(f"Goals: {project.goals}")
    if project.tags:
        lines.append(f"Tags: {', '.join(project.tags)}")
    lines.append(_manager_delegation_guidance(project))
    acpx = _build_acpx_guidance(config, workspace_dir, project_id=project.id)
    if acpx:
        lines.append(acpx)
    return "\n\n".join(lines)


def _default_student_soul(student: StudentAgentConfig) -> str:
    role_line = f"- Your role within the project: {student.label}."
    extra = student.soul.strip()
    if not extra:
        return "\n".join([PROJECT_SOUL_TEMPLATE.strip(), role_line])
    return f"{extra.rstrip()}\n\n## Role\n{role_line}\n"


def _build_student_identity(
    project: Project,
    student: StudentAgentConfig,
    agent_dir: Path,
    workspace_dir: Path,
    config: DrClawConfig,
) -> str:
    soul = load_student_soul(agent_dir, fallback=_default_student_soul(student))
    lines = [
        soul,
        "## Project Context",
        f"You are student agent '{student.label}' (`{student.id}`) assigned to project '{project.name}'.",
        f"Project manager topic: `{project_manager_agent_id(project.id)}`",
        "You only accept delegated work from the project manager. Do not treat yourself as directly user-facing.",
        "Send results back through the delegated reply path instead of contacting the main agent directly.",
    ]
    if project.description:
        lines.append(f"Description: {project.description}")
    if project.goals:
        lines.append(f"Goals: {project.goals}")
    if project.tags:
        lines.append(f"Tags: {', '.join(project.tags)}")
    acpx = _build_acpx_guidance(config, workspace_dir, project_id=project.id)
    if acpx:
        lines.append(acpx)
    return "\n\n".join(lines)


class _ProjectScopedAgent:
    def __init__(
        self,
        config: DrClawConfig,
        provider: LLMProvider,
        project: Project,
        *,
        agent_id: str,
        label: str,
        state_dir: Path,
        identity_builder: Callable[[], str],
        debug_logger: DebugLogger | None = None,
        equipment_manager: EquipmentRuntimeManager | None = None,
        sandbox_job_manager: SandboxJobManager | None = None,
        env_store: EnvStore | None = None,
        claude_code_manager: ClaudeCodeSessionManager | None = None,
        external_agent_bridge: ExternalAgentBridge | None = None,
    ) -> None:
        self.project = project
        self.agent_id = agent_id
        self.label = label
        self.state_dir = ensure_dir(state_dir)

        project_dir = config.data_path / "projects" / project.id
        self.project_dir = project_dir
        self.workspace_dir = ensure_dir(project_dir / "workspace")
        load_project_soul(self.workspace_dir)

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
        sandbox_runtime_root = config.data_path / "runtime" / "sandbox_jobs"
        sandbox_runtime_root.mkdir(parents=True, exist_ok=True)
        self.sandbox_job_manager = sandbox_job_manager or SandboxJobManager(
            runtime_root=sandbox_runtime_root,
            backend=DockerSandboxBackend(),
        )

        self.memory_store = MemoryStore(self.state_dir)
        self.remote_tmux_store = RemoteTmuxSessionStore(self.state_dir / "remote_tmux_sessions.json")
        self.remote_tmux_manager = RemoteTmuxManager(self.remote_tmux_store)
        session_manager = SessionManager(self.state_dir / "sessions")

        def env_provider() -> dict[str, str]:
            return self.env_store.get_effective_env(agent_id)

        extra_skill_dirs: list[tuple[str, Path]] = []
        student_skills_dir = self.state_dir / "skills"
        if self.state_dir != self.project_dir:
            extra_skill_dirs.append(("student_private", ensure_dir(student_skills_dir)))

        skills_loader = SkillsLoader(
            workspace=self.workspace_dir,
            global_skills_dir=config.data_path / "skills",
            extra_skill_dirs=extra_skill_dirs,
            env_provider=env_provider,
        )
        context_builder = ContextBuilder(
            identity_builder,
            self.memory_store,
            skills_loader=skills_loader,
            project_notes_file=project_dir / "PROJECT.md",
        )

        tool_registry = ToolRegistry()
        exec_allowed_dirs = [self.workspace_dir]
        if self.state_dir != self.project_dir:
            exec_allowed_dirs.append(self.state_dir)
        register_common_tools(
            tool_registry,
            workspace=self.workspace_dir,
            write_allowed_dir=self.workspace_dir,
            exec_allowed_dirs=exec_allowed_dirs,
            web_search_api_key=config.tools.web.serper.api_key,
            web_search_max_results=config.tools.web.serper.max_results,
            web_search_endpoint=config.tools.web.serper.endpoint,
            env_provider=env_provider,
        )
        tool_registry.register(UseEquipmentTool(self.equipment_manager, caller_agent_id=agent_id))
        tool_registry.register(
            ListActiveEquipmentRunsTool(self.equipment_manager, caller_agent_id=agent_id)
        )
        tool_registry.register(GetEquipmentRunStatusTool(self.equipment_manager))
        tool_registry.register(CreateJobTool(self.sandbox_job_manager, caller_agent_id=agent_id))
        tool_registry.register(
            ListActiveSandboxJobsTool(
                self.sandbox_job_manager,
                caller_agent_id=agent_id,
            )
        )
        tool_registry.register(GetSandboxJobStatusTool(self.sandbox_job_manager))
        tool_registry.register(PauseSandboxJobTool(self.sandbox_job_manager))
        tool_registry.register(ResumeSandboxJobTool(self.sandbox_job_manager))
        tool_registry.register(CancelSandboxJobTool(self.sandbox_job_manager))
        if external_agent_bridge is not None:
            tool_registry.register(
                CallExternalAgentTool(
                    external_agent_bridge,
                    caller_agent_id=agent_id,
                )
            )

        self.background_task_manager = BackgroundToolTaskManager(caller_agent_id=agent_id)
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
                    caller_agent_id=agent_id,
                    default_cwd=str(self.workspace_dir),
                    mcp_tools_provider=_mcp_tools_provider,
                )
            )
            tool_registry.register(
                SendClaudeCodeMessageTool(
                    self.claude_code_manager,
                    caller_agent_id=agent_id,
                )
            )
            tool_registry.register(GetClaudeCodeStatusTool(self.claude_code_manager))
            tool_registry.register(
                ListClaudeCodeSessionsTool(
                    self.claude_code_manager,
                    caller_agent_id=agent_id,
                )
            )
            tool_registry.register(
                CloseClaudeCodeSessionTool(
                    self.claude_code_manager,
                    caller_agent_id=agent_id,
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
            agent_id=agent_id,
            background_task_manager=self.background_task_manager,
            tool_detach_timeout_seconds=config.agent.tool_detach_timeout_seconds,
        )

    async def process_direct(self, message: str) -> str:
        return await self.loop.process_direct(message, self.loop.session_key)


class ProjectAgent(_ProjectScopedAgent):
    """Project manager agent. This is the only project-facing agent seen by main."""

    def __init__(
        self,
        config: DrClawConfig,
        provider: LLMProvider,
        project: Project,
        debug_logger: DebugLogger | None = None,
        equipment_manager: EquipmentRuntimeManager | None = None,
        sandbox_job_manager: SandboxJobManager | None = None,
        env_store: EnvStore | None = None,
        claude_code_manager: ClaudeCodeSessionManager | None = None,
        external_agent_bridge: ExternalAgentBridge | None = None,
        ensure_student_active: Callable[[StudentAgentConfig], Awaitable[None] | None] | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._debug_logger = debug_logger
        self._external_agent_bridge = external_agent_bridge
        self._student_configs = {student.id: student for student in project.student_agents}
        self._ensure_student_active = ensure_student_active
        super().__init__(
            config,
            provider,
            project,
            agent_id=project_manager_agent_id(project.id),
            label=project.name,
            state_dir=config.data_path / "projects" / project.id,
            identity_builder=lambda: _build_manager_identity(project, self.workspace_dir, config),
            debug_logger=debug_logger,
            equipment_manager=equipment_manager,
            sandbox_job_manager=sandbox_job_manager,
            env_store=env_store,
            claude_code_manager=claude_code_manager,
            external_agent_bridge=external_agent_bridge,
        )
        if self._student_configs:
            self.loop.tool_registry.register(
                RouteToStudentTool(project.student_agents, self._route_to_student)
            )

    async def _route_to_student(self, student: StudentAgentConfig, message: str) -> str:
        bus = self.loop.bus
        if self._ensure_student_active is not None:
            maybe = self._ensure_student_active(student)
            if inspect.isawaitable(maybe):
                await maybe

        if bus is not None:
            origin = self.loop._current_inbound
            channel = origin.channel if origin else "cli"
            chat_id = origin.chat_id if origin else f"{self.project.id}:{student.id}"
            metadata: dict[str, object] = {
                "delegated": True,
                "delegated_by": self.loop.agent_id,
                "reply_to_topic": self.loop.agent_id,
                "reply_channel": channel,
                "reply_chat_id": chat_id,
                "webui_language": (
                    origin.metadata.get("webui_language")
                    if origin is not None
                    else None
                ),
            }
            if origin is not None:
                attachments = origin.metadata.get("attachments")
                if isinstance(attachments, list) and attachments:
                    metadata["attachments"] = attachments
            await bus.publish_inbound(
                InboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    text=message,
                    source=self.loop.agent_id,
                    hop_count=(origin.hop_count + 1) if origin else 0,
                    metadata=metadata,
                ),
                topic=project_student_agent_id(self.project.id, student.id),
            )
            return (
                f"Routed to student {student.label}. "
                f"You must now wait for the response from {student.label}. "
                "Do NOT check the student's workspace, poll for status with "
                "list_background_tool_tasks or list_active_jobs, or use exec/sleep "
                "to monitor progress. The student will report back automatically "
                "when done. Proceed to other work or wait."
            )

        agent = StudentProjectAgent(
            self._config,
            self._provider,
            self.project,
            student,
            debug_logger=self._debug_logger,
            equipment_manager=self.equipment_manager,
            sandbox_job_manager=self.sandbox_job_manager,
            env_store=self.env_store,
            claude_code_manager=self.claude_code_manager,
            external_agent_bridge=self._external_agent_bridge,
        )
        return await agent.process_direct(message)


class StudentProjectAgent(_ProjectScopedAgent):
    """Worker agent inside a project. Shares workspace, keeps private memory/history."""

    def __init__(
        self,
        config: DrClawConfig,
        provider: LLMProvider,
        project: Project,
        student: StudentAgentConfig,
        debug_logger: DebugLogger | None = None,
        equipment_manager: EquipmentRuntimeManager | None = None,
        sandbox_job_manager: SandboxJobManager | None = None,
        env_store: EnvStore | None = None,
        claude_code_manager: ClaudeCodeSessionManager | None = None,
        external_agent_bridge: ExternalAgentBridge | None = None,
    ) -> None:
        self.student = student
        agent_dir = project_student_state_dir(config.data_path / "projects" / project.id, student.id)
        super().__init__(
            config,
            provider,
            project,
            agent_id=project_student_agent_id(project.id, student.id),
            label=student.label,
            state_dir=agent_dir,
            identity_builder=lambda: _build_student_identity(
                project,
                student,
                agent_dir,
                self.workspace_dir,
                config,
            ),
            debug_logger=debug_logger,
            equipment_manager=equipment_manager,
            sandbox_job_manager=sandbox_job_manager,
            env_store=env_store,
            claude_code_manager=claude_code_manager,
            external_agent_bridge=external_agent_bridge,
        )
