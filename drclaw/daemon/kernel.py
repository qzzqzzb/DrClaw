"""Kernel — boots and owns the core runtime objects."""

from __future__ import annotations

import inspect
from dataclasses import dataclass

from drclaw.agent.debug import DebugLogger
from drclaw.agent.registry import AgentRegistry
from drclaw.agent_hub.store import AgentHubStore
from drclaw.bus.queue import MessageBus
from drclaw.claude_code.manager import ClaudeCodeSessionManager
from drclaw.config.schema import DrClawConfig
from drclaw.cron.service import CronService
from drclaw.equipment.manager import EquipmentRuntimeManager
from drclaw.external_agents.bridge import ExternalAgentBridge
from drclaw.models.project import JsonProjectStore, ProjectStore
from drclaw.providers.base import LLMProvider
from drclaw.usage.store import AgentUsageStore


@dataclass
class Kernel:
    config: DrClawConfig
    bus: MessageBus
    registry: AgentRegistry
    project_store: ProjectStore
    equipment_manager: EquipmentRuntimeManager
    claude_code_manager: ClaudeCodeSessionManager
    cron: CronService
    agent_hub_store: AgentHubStore
    usage_store: AgentUsageStore
    external_agent_bridge: ExternalAgentBridge

    @classmethod
    def boot(
        cls,
        config: DrClawConfig,
        provider: LLMProvider,
        *,
        debug_logger: DebugLogger | None = None,
    ) -> Kernel:
        bus = MessageBus()
        project_store = JsonProjectStore(config.data_path)
        agent_hub_store = AgentHubStore(config.data_path / "agent-hub")
        cron = CronService(config.data_path / "cron" / "jobs.json")
        usage_store = AgentUsageStore(config.data_path / "runtime" / "agent-usage.json")
        external_agent_bridge = ExternalAgentBridge(config, bus)
        registry = AgentRegistry(
            config,
            provider,
            bus,
            cron_service=cron,
            usage_store=usage_store,
            external_agent_bridge=external_agent_bridge,
        )

        async def _auto_activate_on_inbound(topic: str, _message) -> None:  # noqa: ANN001
            if topic == "main":
                registry.start_main(debug_logger=debug_logger)
                return
            if not topic.startswith("proj:"):
                return
            project_id = topic.split(":", 1)[1]
            project = project_store.get_project(project_id)
            if project is None:
                return
            registry.activate_project(
                project,
                interactive=True,
                debug_logger=debug_logger,
            )

        bus.set_on_inbound_publish(_auto_activate_on_inbound)
        registry.start_main(debug_logger=debug_logger)
        registry.spawn_activated_projects(project_store, debug_logger=debug_logger)
        return cls(
            config=config,
            bus=bus,
            registry=registry,
            project_store=project_store,
            equipment_manager=registry.equipment_manager,
            claude_code_manager=registry.claude_code_manager,
            cron=cron,
            agent_hub_store=agent_hub_store,
            usage_store=usage_store,
            external_agent_bridge=external_agent_bridge,
        )

    async def shutdown(self) -> None:
        self.cron.stop()
        await self.claude_code_manager.stop_all()
        maybe = self.registry.equipment_manager.stop_all()
        if inspect.isawaitable(maybe):
            await maybe
        await self.registry.stop_all()
        await self.external_agent_bridge.close()
