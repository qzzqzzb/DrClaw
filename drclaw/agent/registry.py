"""AgentRegistry — centralized agent lifecycle management."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from loguru import logger

from drclaw.agent.activation_state import ProjectActivationStateStore
from drclaw.agent.debug import DebugLogger
from drclaw.agent.loop import AgentLoop
from drclaw.agent.main_agent import MainAgent
from drclaw.agent.project_agent import ProjectAgent
from drclaw.bus.queue import MessageBus
from drclaw.claude_code.manager import ClaudeCodeSessionManager
from drclaw.config.env_store import EnvStore
from drclaw.config.schema import DrClawConfig
from drclaw.cron.service import CronService
from drclaw.equipment.manager import EquipmentRuntimeManager
from drclaw.equipment.prototypes import EquipmentPrototypeStore
from drclaw.external_agents.bridge import ExternalAgentBridge
from drclaw.models.project import Project, ProjectStore
from drclaw.providers.base import LLMProvider
from drclaw.usage.store import AgentUsageStore


class AgentStatus(Enum):
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class AgentHandle:
    agent_id: str
    agent_type: Literal["main", "project"]
    label: str
    project: Project | None
    loop: AgentLoop
    task: asyncio.Task[None] | None = None
    status: AgentStatus = AgentStatus.RUNNING
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


class AgentRegistry:
    """Manages all live agent instances with status tracking."""

    def __init__(
        self,
        config: DrClawConfig,
        provider: LLMProvider,
        bus: MessageBus,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
        cron_service: CronService | None = None,
        usage_store: AgentUsageStore | None = None,
        external_agent_bridge: ExternalAgentBridge | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.bus = bus
        self.on_tool_call = on_tool_call
        self.cron_service = cron_service
        self.usage_store = usage_store
        self.external_agent_bridge = external_agent_bridge
        self._handles: dict[str, AgentHandle] = {}
        self._activation_state = ProjectActivationStateStore(
            config.data_path / "runtime" / "project-activation.json"
        )
        self._activated_project_ids: set[str] = self._activation_state.load()
        self.env_store = EnvStore(
            config=config,
            config_path=config.data_path / "config.json",
        )
        runtime_root = config.data_path / "runtime" / "equipments"
        runtime_root.mkdir(parents=True, exist_ok=True)
        self.equipment_manager = EquipmentRuntimeManager(
            prototype_store=EquipmentPrototypeStore(),
            runtime_root=runtime_root,
            provider=provider,
            config=config,
            bus=bus,
            env_store=self.env_store,
            usage_store=self.usage_store,
        )
        cc = config.claude_code
        self.claude_code_manager = ClaudeCodeSessionManager(
            bus=bus,
            max_concurrent=cc.max_concurrent_sessions,
            idle_timeout_seconds=cc.idle_timeout_seconds,
            default_max_budget_usd=cc.max_budget_usd,
            default_max_turns=cc.max_turns,
            default_permission_mode=cc.permission_mode,
            default_allowed_tools=list(cc.allowed_tools),
        )

    def start_main(self, debug_logger: DebugLogger | None = None) -> AgentHandle:
        """Create and start the MainAgent on the bus.

        Idempotent: returns the existing handle if the main agent is RUNNING.
        """
        existing = self._handles.get("main")
        if existing and existing.status == AgentStatus.RUNNING:
            return existing
        if existing:
            del self._handles["main"]

        agent = MainAgent(
            self.config,
            self.provider,
            debug_logger=debug_logger,
            on_project_create=lambda p: self.activate_project(
                p, interactive=True, debug_logger=debug_logger,
            ),
            on_project_remove=self.remove_project_agent,
            equipment_manager=self.equipment_manager,
            env_store=self.env_store,
            cron_service=self.cron_service,
            external_agent_bridge=self.external_agent_bridge,
        )
        agent.loop.bus = self.bus
        agent.loop.agent_id = "main"
        agent.loop.usage_store = self.usage_store
        if self.on_tool_call:
            agent.loop.on_tool_call = self.on_tool_call

        self.bus.subscribe("main")
        task = asyncio.create_task(agent.loop.run())
        handle = AgentHandle(
            agent_id="main",
            agent_type="main",
            label="Assistant Agent",
            project=None,
            loop=agent.loop,
            task=task,
        )
        task.add_done_callback(lambda t: self._on_task_done("main", t))
        self._handles["main"] = handle
        logger.info("agent {} activates", handle.agent_id)
        return handle

    def spawn_project(
        self,
        project: Project,
        *,
        interactive: bool = False,
        debug_logger: DebugLogger | None = None,
    ) -> AgentHandle:
        """Spawn a ProjectAgent for the given project.

        Idempotent if a RUNNING agent exists for this project.
        Replaces DONE/ERROR agents with a fresh instance.
        """
        agent_id = f"proj:{project.id}"

        existing = self._handles.get(agent_id)
        if existing and existing.status == AgentStatus.RUNNING:
            return existing
        if existing:
            del self._handles[agent_id]

        agent = ProjectAgent(
            self.config,
            self.provider,
            project,
            debug_logger=debug_logger,
            equipment_manager=self.equipment_manager,
            env_store=self.env_store,
            claude_code_manager=self.claude_code_manager,
            external_agent_bridge=self.external_agent_bridge,
        )
        max_iter = self.config.agent.max_iterations if interactive else 15
        agent.loop.max_iterations = max_iter
        agent.loop.bus = self.bus
        agent.loop.agent_id = agent_id
        agent.loop.usage_store = self.usage_store
        if self.on_tool_call:
            agent.loop.on_tool_call = self.on_tool_call

        self.bus.subscribe(agent_id)
        task = asyncio.create_task(agent.loop.run())
        handle = AgentHandle(
            agent_id=agent_id,
            agent_type="project",
            label=project.name,
            project=project,
            loop=agent.loop,
            task=task,
        )
        task.add_done_callback(lambda t, aid=agent_id: self._on_task_done(aid, t))
        self._handles[agent_id] = handle
        self._mark_project_activated(project.id)
        logger.info("agent {} activates", handle.agent_id)
        return handle

    def activate_project(
        self,
        project: Project,
        *,
        interactive: bool = True,
        debug_logger: DebugLogger | None = None,
    ) -> AgentHandle:
        """Activate and spawn a project agent."""
        return self.spawn_project(
            project,
            interactive=interactive,
            debug_logger=debug_logger,
        )

    def spawn_activated_projects(
        self,
        project_store: ProjectStore,
        *,
        debug_logger: DebugLogger | None = None,
    ) -> list[AgentHandle]:
        """Spawn agents only for projects marked as activated in persisted state."""
        projects_by_id = {p.id: p for p in project_store.list_projects()}
        stale = {pid for pid in self._activated_project_ids if pid not in projects_by_id}
        if stale:
            self._activated_project_ids.difference_update(stale)
            self._save_activation_state()

        handles: list[AgentHandle] = []
        for project_id in sorted(self._activated_project_ids):
            project = projects_by_id.get(project_id)
            if project is None:
                continue
            handles.append(self.spawn_project(project, debug_logger=debug_logger))
        return handles

    def spawn_all_projects(
        self,
        project_store: ProjectStore,
        *,
        debug_logger: DebugLogger | None = None,
    ) -> list[AgentHandle]:
        """Spawn agents for all projects in the store."""
        handles = []
        for project in project_store.list_projects():
            handles.append(self.spawn_project(project, debug_logger=debug_logger))
        return handles

    def get(self, agent_id: str) -> AgentHandle | None:
        return self._handles.get(agent_id)

    def list_agents(self) -> list[AgentHandle]:
        return list(self._handles.values())

    async def stop(self, agent_id: str, *, deactivate: bool = True) -> bool:
        """Stop a running agent. Returns False if not found/already stopped."""
        handle = self._handles.get(agent_id)
        if handle is None or handle.status != AgentStatus.RUNNING:
            return False
        handle.loop.stop()
        if handle.task and not handle.task.done():
            handle.task.cancel()
            try:
                await handle.task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("Unexpected error stopping agent {}: {}", agent_id, exc)
        handle.status = AgentStatus.DONE
        if deactivate and handle.agent_type == "project" and handle.project is not None:
            self._mark_project_deactivated(handle.project.id)
        return True

    async def stop_all(self, *, deactivate: bool = False) -> None:
        """Stop all running agents."""
        running = [aid for aid, h in self._handles.items()
                   if h.status == AgentStatus.RUNNING]
        await asyncio.gather(*(self.stop(aid, deactivate=deactivate) for aid in running))

    async def remove_project_agent(self, project_id: str) -> bool:
        """Stop and unregister a project's agent handle, if present."""
        agent_id = f"proj:{project_id}"
        handle = self._handles.get(agent_id)
        self._mark_project_deactivated(project_id)
        if handle is None:
            return False
        await self.stop(agent_id, deactivate=False)
        self._handles.pop(agent_id, None)
        return True

    def find_by_project_name(self, name: str) -> AgentHandle | None:
        """Find a running project agent by project name (case-insensitive)."""
        lower = name.lower()
        for h in self._handles.values():
            if (h.project and h.project.name.lower() == lower
                    and h.status == AgentStatus.RUNNING):
                return h
        return None

    def _on_task_done(self, agent_id: str, task: asyncio.Task[None]) -> None:
        """Update handle status when an agent's asyncio.Task completes."""
        handle = self._handles.get(agent_id)
        if handle is None:
            return
        if task.cancelled():
            handle.status = AgentStatus.DONE
        elif (exc := task.exception()):
            handle.status = AgentStatus.ERROR
            logger.error("Agent {} crashed: {}", agent_id, exc)
        else:
            handle.status = AgentStatus.DONE

    def _save_activation_state(self) -> None:
        try:
            self._activation_state.save(self._activated_project_ids)
        except Exception as exc:
            logger.warning("Failed to persist project activation state: {}", exc)

    def _mark_project_activated(self, project_id: str) -> None:
        if not project_id:
            return
        if project_id in self._activated_project_ids:
            return
        self._activated_project_ids.add(project_id)
        self._save_activation_state()

    def _mark_project_deactivated(self, project_id: str) -> None:
        if not project_id:
            return
        if project_id not in self._activated_project_ids:
            return
        self._activated_project_ids.discard(project_id)
        self._save_activation_state()
