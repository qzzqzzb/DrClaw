"""EquipmentAgent: stateless runtime for executing an equipment task."""

from __future__ import annotations

from typing import TYPE_CHECKING

from drclaw.agent.common import register_common_tools
from drclaw.agent.context import ContextBuilder
from drclaw.agent.loop import AgentLoop
from drclaw.agent.skills import SkillsLoader
from drclaw.session.manager import SessionManager
from drclaw.tools.equipment_tools import ReportToCallerTool, UpdateEquipmentStatusTool
from drclaw.tools.registry import ToolRegistry
from drclaw.utils.helpers import ensure_dir

if TYPE_CHECKING:
    from drclaw.config.env_store import EnvStore
    from drclaw.config.schema import DrClawConfig
    from drclaw.equipment.manager import EquipmentRuntimeManager
    from drclaw.equipment.models import EquipmentRuntime
    from drclaw.equipment.prototypes import EquipmentPrototype
    from drclaw.providers.base import LLMProvider


class EquipmentAgent:
    """Stateless worker agent created per runtime request."""

    def __init__(
        self,
        *,
        config: DrClawConfig,
        provider: LLMProvider,
        prototype: EquipmentPrototype,
        runtime: EquipmentRuntime,
        manager: EquipmentRuntimeManager,
        env_store: EnvStore | None = None,
    ) -> None:
        self.runtime = runtime
        self._env_store = env_store

        workspace_dir = ensure_dir(runtime.workspace_path)
        sessions_dir = ensure_dir(workspace_dir / "sessions")
        session_manager = SessionManager(sessions_dir)

        identity = (
            f"You are equipment agent '{prototype.name}'. "
            "You are stateless and run exactly one task for a caller agent.\n"
            "You can use tools and skills available to this equipment prototype.\n"
            "Important: call update_status during long tasks, and call "
            "report_to_caller exactly once before finishing to notify the caller.\n"
            "If you hit an unrecoverable blocker (for example missing API keys, no network, "
            "or no access to required external data sources), stop immediately and call "
            "report_to_caller with failed status and detailed error context. Do not continue "
            "with incomplete or fabricated results."
        )
        env_provider = None
        if self._env_store is not None:
            def env_provider() -> dict[str, str]:
                return self._env_store.get_effective_env(runtime.agent_id)
        skills_loader = SkillsLoader(
            workspace=workspace_dir,
            global_skills_dir=prototype.skills_dir,
            env_provider=env_provider,
        )
        context_builder = ContextBuilder(identity, memory_store=None, skills_loader=skills_loader)

        tool_registry = ToolRegistry()
        register_common_tools(
            tool_registry,
            workspace=workspace_dir,
            write_allowed_dir=workspace_dir,
            read_allowed_dir=workspace_dir,
            web_search_api_key=config.tools.web.serper.api_key,
            web_search_max_results=config.tools.web.serper.max_results,
            web_search_endpoint=config.tools.web.serper.endpoint,
            env_provider=env_provider,
        )
        tool_registry.register(UpdateEquipmentStatusTool(manager, runtime.runtime_id))
        tool_registry.register(ReportToCallerTool(manager, runtime.runtime_id))

        self.loop = AgentLoop(
            provider=provider,
            tool_registry=tool_registry,
            context_builder=context_builder,
            session_manager=session_manager,
            memory_store=None,
            max_iterations=min(config.agent.max_iterations, 20),
            memory_window=config.agent.memory_window,
            max_history=config.agent.max_history,
            agent_id=runtime.agent_id,
            session_key=f"equip:{runtime.runtime_id}",
        )

    async def process_direct(self, message: str) -> str:
        return await self.loop.process_direct(message, self.loop.session_key, channel="system")
