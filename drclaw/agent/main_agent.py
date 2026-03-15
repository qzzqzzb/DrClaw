"""Main Agent orchestrator — top-level routing agent."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from drclaw.agent.common import register_common_tools
from drclaw.agent.context import ContextBuilder
from drclaw.agent.loop import AgentLoop
from drclaw.agent.memory import MemoryStore
from drclaw.agent.skills import SkillsLoader
from drclaw.bus.queue import MessageBus
from drclaw.config.env_store import EnvStore
from drclaw.config.schema import DrClawConfig
from drclaw.cron.service import CronService
from drclaw.equipment.manager import EquipmentRuntimeManager
from drclaw.equipment.prototypes import EquipmentPrototypeStore
from drclaw.frontends.web.chat_files import ChatFileStore
from drclaw.models.messages import InboundMessage, OutboundMessage
from drclaw.models.project import (
    AmbiguousProjectNameError,
    JsonProjectStore,
    Project,
    ProjectStore,
)
from drclaw.providers.base import LLMProvider
from drclaw.sandbox.backends import DockerSandboxBackend
from drclaw.sandbox.manager import SandboxJobManager
from drclaw.session.manager import SessionManager
from drclaw.skills.local_hub import LocalSkillHubStore
from drclaw.soul import load_main_soul
from drclaw.tools.background_tasks import (
    BackgroundToolTaskManager,
    CancelBackgroundToolTaskTool,
    GetBackgroundToolTaskStatusTool,
    ListBackgroundToolTasksTool,
)
from drclaw.tools.cron import CronTool
from drclaw.tools.env_admin_tools import (
    ListEnvScopesTool,
    ListEnvVarsTool,
    SetEnvVarTool,
    UnsetEnvVarTool,
)
from drclaw.tools.equipment_admin_tools import (
    AddEquipmentTool,
    AddLocalHubSkillsToEquipmentTool,
    AddLocalHubSkillsToProjectTool,
    ImportSkillToLocalHubTool,
    ListEquipmentsTool,
    ListLocalSkillHubCategoriesTool,
    ListLocalSkillHubSkillsTool,
    RemoveEquipmentTool,
    SetLocalSkillHubCategoryMetadataTool,
)
from drclaw.tools.equipment_tools import (
    GetEquipmentRunStatusTool,
    ListActiveEquipmentRunsTool,
    UseEquipmentTool,
)
from drclaw.tools.external_agent_tools import CallExternalAgentTool
from drclaw.tools.message import MessageTool
from drclaw.tools.project_tools import (
    CreateProjectTool,
    ListProjectsTool,
    RemoveProjectTool,
    RouteToProjectTool,
)
from drclaw.tools.registry import ToolRegistry
from drclaw.utils.helpers import ensure_default_skill_dirs

if TYPE_CHECKING:
    from drclaw.agent.debug import DebugLogger
    from drclaw.external_agents.bridge import ExternalAgentBridge


def _build_identity(
    data_dir: Path,
    project_store: ProjectStore,
    response_language: str = "",
) -> str:
    """Build MainAgent identity with a live project summary.

    Called on every build_system_prompt() invocation (up to 40x per turn in
    tool loops). Reads projects.json from disk each time — acceptable for
    small registry files.
    """
    base = load_main_soul(data_dir)
    base += (
        "\n\n## Skill Grant Policy\n"
        "- If a request is to grant a specific student/project access to a local-hub skill, "
        "prefer add_local_hub_skills_to_project.\n"
        "- Use equipment provisioning tools only when the user asks for shared equipment access."
    )
    projects = project_store.list_projects()
    if not projects:
        identity = base + "\n\n(No projects yet.)"
    else:
        lines = ["\n\n## Active Students (Project-Backed)"]
        for p in projects:
            desc = f" — {p.description}" if p.description else ""
            lines.append(f"- [{p.id}] {p.name}{desc} ({p.status})")
        identity = base + "\n".join(lines)
    if response_language == "zh":
        identity += (
            "\n\n## Response Language\n"
            "- Organize your response in Chinese.\n"
            "- Keep professional terms in their original language when needed for semantic "
            "accuracy and smoothness."
        )
    elif response_language == "en":
        identity += (
            "\n\n## Response Language\n"
            "- Organize your response in English.\n"
            "- Keep professional terms in their original language when needed for semantic "
            "accuracy and smoothness."
        )
    return identity


class MainAgent:
    """Top-level orchestrator that wires components for the main routing agent."""

    def __init__(
        self,
        config: DrClawConfig,
        provider: LLMProvider,
        debug_logger: DebugLogger | None = None,
        on_project_create: Callable[[Project], Any] | None = None,
        on_project_remove: Callable[[str], Awaitable[Any] | Any] | None = None,
        equipment_manager: EquipmentRuntimeManager | None = None,
        sandbox_job_manager: SandboxJobManager | None = None,
        env_store: EnvStore | None = None,
        cron_service: CronService | None = None,
        external_agent_bridge: ExternalAgentBridge | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.env_store = env_store or EnvStore(
            config=config,
            config_path=config.data_path / "config.json",
        )

        data_dir = config.data_path
        ensure_default_skill_dirs(data_dir)
        runtime_root = data_dir / "runtime" / "equipments"
        runtime_root.mkdir(parents=True, exist_ok=True)

        prototype_store = EquipmentPrototypeStore()
        local_skill_hub = LocalSkillHubStore(data_dir / "local-skill-hub")
        self.equipment_manager = equipment_manager or EquipmentRuntimeManager(
            prototype_store=prototype_store,
            runtime_root=runtime_root,
            provider=provider,
            config=config,
            env_store=self.env_store,
        )
        sandbox_runtime_root = data_dir / "runtime" / "sandbox_jobs"
        sandbox_runtime_root.mkdir(parents=True, exist_ok=True)
        self.sandbox_job_manager = sandbox_job_manager or SandboxJobManager(
            runtime_root=sandbox_runtime_root,
            backend=DockerSandboxBackend(),
        )

        self.project_store = JsonProjectStore(data_dir)
        self.cron_service = cron_service or CronService(data_dir / "cron" / "jobs.json")
        self.memory_store = MemoryStore(data_dir)
        self.session_manager = SessionManager(data_dir / "sessions")

        def env_provider() -> dict[str, str]:
            return self.env_store.get_effective_env("main")

        skills_loader = SkillsLoader(
            workspace=data_dir,
            global_skills_dir=data_dir / "skills",
            env_provider=env_provider,
        )
        context_builder = ContextBuilder(
            lambda: _build_identity(
                data_dir,
                self.project_store,
                self._current_webui_language(),
            ),
            self.memory_store,
            skills_loader=skills_loader,
        )

        registry = ToolRegistry()
        register_common_tools(
            registry,
            workspace=data_dir,
            write_allowed_dir=data_dir,
            restrict_exec=False,
            web_search_api_key=config.tools.web.serper.api_key,
            web_search_max_results=config.tools.web.serper.max_results,
            web_search_endpoint=config.tools.web.serper.endpoint,
            env_provider=env_provider,
        )
        registry.register(
            MessageTool(
                send_callback=self._send_message_outbound,
                workspace=data_dir,
                allowed_dir=data_dir,
            )
        )
        registry.register(ListProjectsTool(self.project_store))
        registry.register(
            CreateProjectTool(
                self.project_store,
                projects_dir=data_dir / "projects",
                on_create=on_project_create,
            )
        )
        registry.register(SetEnvVarTool(self.env_store))
        registry.register(UnsetEnvVarTool(self.env_store))
        registry.register(ListEnvVarsTool(self.env_store))
        registry.register(ListEnvScopesTool(self.env_store))
        registry.register(
            RemoveProjectTool(
                self.project_store,
                projects_dir=data_dir / "projects",
                on_remove=on_project_remove,
            )
        )
        registry.register(RouteToProjectTool(self.project_store, self._route_to_project))
        registry.register(
            AddEquipmentTool(
                self.equipment_manager.prototype_store,
                local_skill_hub=local_skill_hub,
            )
        )
        registry.register(
            ListEquipmentsTool(
                self.equipment_manager.prototype_store,
                runtime_manager=self.equipment_manager,
            )
        )
        registry.register(ListLocalSkillHubSkillsTool(local_skill_hub))
        registry.register(ListLocalSkillHubCategoriesTool(local_skill_hub))
        registry.register(SetLocalSkillHubCategoryMetadataTool(local_skill_hub))
        registry.register(ImportSkillToLocalHubTool(local_skill_hub))
        registry.register(
            AddLocalHubSkillsToEquipmentTool(
                self.equipment_manager.prototype_store,
                local_skill_hub,
            )
        )
        registry.register(
            AddLocalHubSkillsToProjectTool(
                self.project_store,
                data_dir / "projects",
                local_skill_hub,
            )
        )
        registry.register(
            RemoveEquipmentTool(
                self.equipment_manager.prototype_store,
                runtime_manager=self.equipment_manager,
            )
        )
        registry.register(
            UseEquipmentTool(self.equipment_manager, caller_agent_id="main")
        )
        registry.register(
            ListActiveEquipmentRunsTool(self.equipment_manager, caller_agent_id="main")
        )
        registry.register(GetEquipmentRunStatusTool(self.equipment_manager))
        if external_agent_bridge is not None:
            registry.register(
                CallExternalAgentTool(
                    external_agent_bridge,
                    caller_agent_id="main",
                )
            )
        self.background_task_manager = BackgroundToolTaskManager(caller_agent_id="main")
        registry.register(ListBackgroundToolTasksTool(self.background_task_manager))
        registry.register(GetBackgroundToolTaskStatusTool(self.background_task_manager))
        registry.register(CancelBackgroundToolTaskTool(self.background_task_manager))
        registry.register(
            CronTool(
                self.cron_service,
                default_target="main",
                resolve_target=self._resolve_cron_target,
                context_provider=self._cron_delivery_context,
            )
        )

        self.loop = AgentLoop(
            provider=provider,
            tool_registry=registry,
            context_builder=context_builder,
            session_manager=self.session_manager,
            memory_store=self.memory_store,
            max_iterations=config.agent.max_iterations,
            memory_window=config.agent.memory_window,
            max_history=config.agent.max_history,
            debug_logger=debug_logger,
            background_task_manager=self.background_task_manager,
            tool_detach_timeout_seconds=config.agent.tool_detach_timeout_seconds,
        )

    async def _send_message_outbound(self, msg: OutboundMessage) -> None:
        """Send a message-tool outbound via the shared bus."""
        bus = self.loop.bus
        if bus is None:
            raise RuntimeError("message tool unavailable: message bus is not configured")

        attachments: list[dict[str, Any]] = []
        if msg.media:
            file_store = ChatFileStore(self.config.data_path)
            for media_path in msg.media:
                try:
                    rec = file_store.ingest_outbound_media(Path(media_path))
                except (OSError, ValueError):
                    continue
                attachments.append(
                    {
                        "id": rec.file_id,
                        "name": rec.name,
                        "mime": rec.mime,
                        "size": rec.size,
                        "path": str(rec.path),
                        "download_url": file_store.download_url(rec.file_id),
                    }
                )

        msg.source = self.loop.agent_id
        msg.topic = self.loop.agent_id
        if attachments:
            msg.metadata = dict(msg.metadata)
            msg.metadata["attachments"] = attachments

            inbound = self.loop._current_inbound
            if (
                inbound is not None
                and msg.channel == inbound.channel
                and msg.chat_id == inbound.chat_id
            ):
                session = self.loop._active_session
                if session is None:
                    session = self.session_manager.load("main")
                session.messages.append(
                    {
                        "role": "assistant",
                        "content": msg.text,
                        "source": self.loop.agent_id,
                        "attachments": attachments,
                    }
                )
                self.session_manager.save(session)
        await bus.publish_outbound(msg)

    def _current_webui_language(self) -> str:
        """Return normalized WebUI language hint for the current inbound message."""
        msg = self.loop._current_inbound
        if msg is None or msg.channel != "web":
            return ""
        raw = msg.metadata.get("webui_language")
        if not isinstance(raw, str):
            return ""
        lang = raw.strip().lower()
        return lang if lang in {"zh", "en"} else ""

    def _cron_delivery_context(self) -> tuple[str, str] | None:
        msg = self.loop._current_inbound
        if msg is None:
            return None
        return msg.channel, msg.chat_id

    def _resolve_cron_target(self, target_ref: str) -> str:
        ref = target_ref.strip()
        if not ref or ref.lower() == "main":
            return "main"
        if ref.startswith("proj:"):
            project_id = ref.split(":", 1)[1]
            project = self.project_store.get_project(project_id)
            if project is None:
                raise ValueError(f"project not found: {project_id}")
            return f"proj:{project_id}"
        try:
            project = self.project_store.find_by_name(ref)
        except AmbiguousProjectNameError as exc:
            raise ValueError(str(exc)) from None
        if project is None:
            project = self.project_store.get_project(ref)
        if project is None:
            raise ValueError(f"project not found: {ref}")
        return f"proj:{project.id}"

    async def _route_to_project(self, project: Project, message: str) -> str:
        """Route *message* to the ProjectAgent for *project*.

        When the bus is available (interactive mode), publish to the agent's
        topic — the already-running ProjectAgent picks it up.  When there is
        no bus (single-message mode), fall back to synchronous execution.
        """
        bus = self.loop.bus
        self.equipment_manager.bus = bus
        self.sandbox_job_manager.bus = bus
        if bus is not None:
            origin = self.loop._current_inbound
            channel = origin.channel if origin else "cli"
            chat_id = origin.chat_id if origin else f"proj-{project.id}"
            metadata: dict[str, Any] = {
                "delegated": True,
                "delegated_by": "main",
                "reply_to_topic": "main",
                "reply_channel": channel,
                "reply_chat_id": chat_id,
                "webui_language": (
                    origin.metadata.get("webui_language")
                    if origin is not None
                    else None
                ),
            }
            delegated_attachments = self._prepare_project_attachments(project, origin)
            if delegated_attachments:
                metadata["attachments"] = delegated_attachments
            msg = InboundMessage(
                channel=channel,
                chat_id=chat_id,
                text=message,
                source="main",
                hop_count=(origin.hop_count + 1) if origin else 0,
                metadata=metadata,
            )
            await bus.publish_inbound(msg, topic=f"proj:{project.id}")
            return (
                f"Routed to {project.name}. "
                f"You must now wait for the response from {project.name}. "
                "Do NOT check the project agent's workspace, poll for status, or use "
                "exec/sleep to monitor progress. The student will report back to you "
                "automatically when done. Proceed to other work or wait."
            )

        from drclaw.agent.project_agent import ProjectAgent

        agent = ProjectAgent(
            self.config,
            self.provider,
            project,
            debug_logger=self.loop.debug_logger,
            equipment_manager=self.equipment_manager,
            sandbox_job_manager=self.sandbox_job_manager,
            env_store=self.env_store,
        )
        return await agent.process_direct(message)

    def _prepare_project_attachments(
        self,
        project: Project,
        origin: InboundMessage | None,
    ) -> list[dict[str, Any]]:
        if origin is None:
            return []
        raw = origin.metadata.get("attachments")
        if not isinstance(raw, list):
            return []

        project_workspace = self.config.data_path / "projects" / project.id / "workspace"
        incoming_dir = project_workspace / "incoming_files"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        incoming_root = incoming_dir.resolve()
        data_root = self.config.data_path.resolve()

        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            file_id_raw = item.get("id")
            file_id = file_id_raw.strip().lower() if isinstance(file_id_raw, str) else ""
            if file_id and file_id in seen:
                continue
            path_raw = item.get("path")
            source_path = (
                Path(path_raw).resolve()
                if isinstance(path_raw, str) and path_raw
                else None
            )
            copied_path = source_path
            if (
                source_path is not None
                and source_path.is_file()
                and source_path.is_relative_to(data_root)
            ):
                src_name = source_path.name
                requested_name = item.get("name")
                safe_name = (
                    Path(str(requested_name)).name
                    if requested_name is not None and str(requested_name).strip()
                    else src_name
                )
                target_name = f"{file_id}_{safe_name}" if file_id else safe_name
                candidate = (incoming_dir / target_name).resolve()
                if candidate.is_relative_to(incoming_root):
                    try:
                        shutil.copy2(source_path, candidate)
                        copied_path = candidate
                    except OSError:
                        copied_path = source_path

            if copied_path is None:
                continue
            att: dict[str, Any] = {
                "id": file_id or copied_path.stem,
                "name": str(item.get("name") or copied_path.name),
                "mime": str(item.get("mime") or "application/octet-stream"),
                "size": (
                    max(0, int(item.get("size")))
                    if isinstance(item.get("size"), (int, float))
                    else max(0, int(copied_path.stat().st_size))
                ),
                "path": str(copied_path),
            }
            download_url = item.get("download_url")
            if isinstance(download_url, str) and download_url.strip():
                att["download_url"] = download_url
            out.append(att)
            if file_id:
                seen.add(file_id)
        return out

    async def process_direct(self, message: str) -> str:
        """Process a single message in direct mode (no bus)."""
        return await self.loop.process_direct(message, session_key="main")

    async def run(self, bus: MessageBus) -> None:
        """Run the bus-based interactive loop.

        The bus is set here rather than at construction time because
        the caller typically creates it after the agent is built.
        """
        self.loop.bus = bus
        self.equipment_manager.bus = bus
        self.sandbox_job_manager.bus = bus
        await self.loop.run()
