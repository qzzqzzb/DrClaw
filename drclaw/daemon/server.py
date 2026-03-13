"""Daemon — boots the Kernel, loads frontends, blocks until signal."""

from __future__ import annotations

import asyncio
import importlib
import signal
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from rich.console import Console

from drclaw.daemon.frontend import FrontendAdapter
from drclaw.daemon.kernel import Kernel
from drclaw.models.messages import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from drclaw.agent.debug import DebugLogger
    from drclaw.config.schema import DrClawConfig
    from drclaw.providers.base import LLMProvider

console = Console()


class Daemon:
    def __init__(
        self,
        config: DrClawConfig,
        provider: LLMProvider,
        *,
        frontend_overrides: list[str] | None = None,
        debug_logger: DebugLogger | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self._frontend_overrides = frontend_overrides
        self._debug_logger = debug_logger
        self._kernel: Kernel | None = None
        self._frontends: list[FrontendAdapter] = []
        self._monitor_tasks: list[asyncio.Task[None]] = []

    async def run(self) -> None:
        self._kernel = Kernel.boot(
            self.config, self.provider, debug_logger=self._debug_logger,
        )
        self._kernel.cron.on_job = self._on_cron_job

        agents = self._kernel.registry.list_agents()
        console.print(f"[bold]DrClaw daemon[/bold] — {len(agents)} agent(s) started:")
        for h in agents:
            tag = f"[dim]{h.agent_id}[/dim]"
            if h.project:
                desc = h.project.description or "no description"
                console.print(f"  • {h.label} ({tag}) — {desc}")
            else:
                console.print(f"  • {h.label} ({tag})")

        self._frontends = self._load_frontends()
        for fe in self._frontends:
            await fe.start(self._kernel)
        if self._frontends:
            names = ", ".join(fe.adapter_id for fe in self._frontends)
            console.print(f"Frontends loaded: {names}")

        self._start_bus_monitor()
        await self._kernel.cron.start()
        cron_status = self._kernel.cron.status()
        if cron_status["jobs"] > 0:
            console.print(f"Cron jobs loaded: {cron_status['jobs']}")

        console.print("[dim]Press Ctrl+C to stop.[/dim]")

        loop = asyncio.get_running_loop()
        stop: asyncio.Future[None] = loop.create_future()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set_result, None)

        await stop
        console.print("\n[bold]Shutting down…[/bold]")
        await self.shutdown()

    async def shutdown(self) -> None:
        if self._kernel is not None:
            self._kernel.cron.stop()
        for t in self._monitor_tasks:
            t.cancel()
        for fe in reversed(self._frontends):
            await fe.stop()
        if self._kernel is not None:
            await self._kernel.shutdown()

    async def _on_cron_job(self, job) -> str | None:  # noqa: ANN001
        if self._kernel is None:
            raise RuntimeError("kernel not initialized")

        target = (job.payload.target or "main").strip()
        if not target:
            target = "main"

        if target == "main":
            self._kernel.registry.start_main(debug_logger=self._debug_logger)
        elif target.startswith("proj:"):
            project_id = target.split(":", 1)[1]
            project = self._kernel.project_store.get_project(project_id)
            if project is None:
                raise RuntimeError(f"target project not found: {project_id}")
            self._kernel.registry.spawn_project(
                project, interactive=False, debug_logger=self._debug_logger,
            )
        else:
            raise RuntimeError(f"unsupported cron target {target!r}")

        channel = (job.payload.channel or "system").strip() or "system"
        chat_id = (job.payload.chat_id or target).strip() or target

        await self._kernel.bus.publish_inbound(
            InboundMessage(
                channel=channel,
                chat_id=chat_id,
                text=job.payload.message,
                source="cron",
                metadata={"cron_job_id": job.id},
            ),
            topic=target,
        )
        return "queued"

    # -- Bus monitor --------------------------------------------------------

    def _start_bus_monitor(self) -> None:
        assert self._kernel is not None
        bus = self._kernel.bus

        inbound_q = bus.subscribe_inbound_display("__daemon_monitor__")
        outbound_q = bus.subscribe_outbound("__daemon_monitor__")

        self._monitor_tasks.append(
            asyncio.create_task(self._monitor_inbound(inbound_q))
        )
        self._monitor_tasks.append(
            asyncio.create_task(self._monitor_outbound(outbound_q))
        )

    @staticmethod
    def _ts() -> str:
        return datetime.now(tz=timezone.utc).strftime("%H:%M:%S")

    @staticmethod
    def _outbound_dest(msg: OutboundMessage) -> str:
        if msg.channel == "system":
            return msg.topic or msg.chat_id
        return f"{msg.channel}:{msg.chat_id}"

    async def _monitor_inbound(
        self, q: asyncio.Queue[InboundMessage],
    ) -> None:
        while True:
            try:
                msg = await q.get()
            except asyncio.CancelledError:
                break
            preview = msg.text[:120].replace("\n", "\\n")
            console.print(
                f"[dim]{self._ts()}[/dim] "
                f"[cyan]IN[/cyan]  {msg.source} → [bold]{msg.topic}[/bold]  {preview}"
            )

    async def _monitor_outbound(
        self, q: asyncio.Queue[OutboundMessage],
    ) -> None:
        while True:
            try:
                msg = await q.get()
            except asyncio.CancelledError:
                break
            preview = msg.text[:120].replace("\n", "\\n")
            dest = self._outbound_dest(msg)
            if msg.msg_type == "tool_call":
                console.print(
                    f"[dim]{self._ts()}[/dim] "
                    f"[yellow]TOOL[/yellow] {msg.source} → [bold]{dest}[/bold]  {preview}"
                )
            else:
                console.print(
                    f"[dim]{self._ts()}[/dim] "
                    f"[green]OUT[/green] {msg.source} → [bold]{dest}[/bold] "
                    f"[dim]({msg.channel}/{msg.msg_type})[/dim]  {preview}"
                )

    def _load_frontends(self) -> list[FrontendAdapter]:
        names = self._frontend_overrides or self.config.daemon.frontends
        adapters: list[FrontendAdapter] = []
        for name in names:
            try:
                mod = importlib.import_module(f"drclaw.frontends.{name}")
            except ImportError as exc:
                raise RuntimeError(
                    f"Cannot load frontend '{name}': {exc}"
                ) from exc
            cls = getattr(mod, "Adapter", None)
            if cls is None:
                raise RuntimeError(
                    f"Frontend module 'drclaw.frontends.{name}' has no 'Adapter' attribute"
                )
            adapter = cls()
            if not isinstance(adapter, FrontendAdapter):
                raise RuntimeError(
                    f"Frontend '{name}': Adapter does not implement FrontendAdapter protocol"
                )
            adapters.append(adapter)
        return adapters
