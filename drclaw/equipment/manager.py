"""Runtime manager for equipment prototypes and spawned equipment agents."""

from __future__ import annotations

import asyncio
import json
import secrets
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from drclaw.bus.queue import MessageBus
from drclaw.config.env_store import EnvStore
from drclaw.equipment.models import TERMINAL_STATES, EquipmentRuntime, RuntimeState
from drclaw.equipment.prototypes import EquipmentPrototypeStore
from drclaw.models.messages import InboundMessage, OutboundMessage
from drclaw.usage.store import AgentUsageStore


class EquipmentRuntimeManager:
    """Manages equipment runtime lifecycle and caller tracking."""

    def __init__(
        self,
        *,
        prototype_store: EquipmentPrototypeStore,
        runtime_root: Path,
        provider: Any,
        config: Any,
        bus: MessageBus | None = None,
        env_store: EnvStore | None = None,
        usage_store: AgentUsageStore | None = None,
        heartbeat_timeout_seconds: int = 300,
        max_total_active: int = 16,
        max_per_caller_active: int = 4,
    ) -> None:
        self.prototype_store = prototype_store
        self.runtime_root = runtime_root
        self.provider = provider
        self.config = config
        self.bus = bus
        self.env_store = env_store or EnvStore(
            config=config,
            config_path=config.data_path / "config.json",
        )
        self.usage_store = usage_store
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.max_total_active = max_total_active
        self.max_per_caller_active = max_per_caller_active

        self._runtimes: dict[str, EquipmentRuntime] = {}
        self._request_to_runtime: dict[str, str] = {}
        self._active_by_caller: dict[str, set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_run(
        self,
        *,
        prototype_name: str,
        instruction: str,
        caller_agent_id: str,
        notify_on_completion: bool = True,
        notify_channel: str = "system",
        notify_chat_id: str | None = None,
    ) -> EquipmentRuntime:
        proto = self.prototype_store.get(prototype_name)
        if proto is None:
            raise ValueError(
                f"Equipment prototype {prototype_name!r} not found at "
                f"{self.prototype_store.root_dir}/<name>/skills/"
            )

        async with self._lock:
            active_total = sum(
                1 for r in self._runtimes.values() if r.status not in TERMINAL_STATES
            )
            if active_total >= self.max_total_active:
                raise RuntimeError("Too many active equipment runs; try again later.")

            caller_active = len(
                [rid for rid in self._active_by_caller[caller_agent_id]
                 if self._runtimes.get(rid) and self._runtimes[rid].status not in TERMINAL_STATES]
            )
            if caller_active >= self.max_per_caller_active:
                raise RuntimeError(
                    f"Caller {caller_agent_id!r} already has {caller_active} active equipment runs."
                )

            runtime_id = secrets.token_hex(4)
            request_id = secrets.token_hex(6)
            workspace = self.runtime_root / f"{proto.name}-{runtime_id}"
            workspace.mkdir(parents=True, exist_ok=True)

            runtime = EquipmentRuntime(
                runtime_id=runtime_id,
                request_id=request_id,
                prototype_name=proto.name,
                caller_agent_id=caller_agent_id,
                workspace_path=workspace,
                notify_on_completion=notify_on_completion,
            )
            self._runtimes[runtime_id] = runtime
            self._request_to_runtime[request_id] = runtime_id
            self._active_by_caller[caller_agent_id].add(runtime_id)

            runtime.task = asyncio.create_task(
                self._run_runtime(
                    runtime_id=runtime_id,
                    instruction=instruction,
                    notify_channel=notify_channel,
                    notify_chat_id=notify_chat_id or caller_agent_id,
                )
            )
            runtime.task.add_done_callback(lambda t, rid=runtime_id: self._on_task_done(rid, t))
            return runtime

    async def stop_all(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        async with self._lock:
            for runtime in self._runtimes.values():
                if runtime.task and not runtime.task.done():
                    runtime.task.cancel()
                    tasks.append(runtime.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_runtime(self, runtime_id: str) -> EquipmentRuntime | None:
        runtime = self._runtimes.get(runtime_id)
        if runtime is not None:
            self._mark_timed_out_if_stale(runtime)
        return runtime

    def get_by_request(self, request_id: str) -> EquipmentRuntime | None:
        runtime_id = self._request_to_runtime.get(request_id)
        if runtime_id is None:
            return None
        return self.get_runtime(runtime_id)

    def list_for_caller(
        self,
        caller_agent_id: str,
        *,
        include_terminal: bool = False,
    ) -> list[EquipmentRuntime]:
        runtimes: list[EquipmentRuntime] = []
        for rid in sorted(self._active_by_caller.get(caller_agent_id, set())):
            runtime = self._runtimes.get(rid)
            if runtime is None:
                continue
            self._mark_timed_out_if_stale(runtime)
            if not include_terminal and runtime.status in TERMINAL_STATES:
                continue
            runtimes.append(runtime)
        return runtimes

    def list_runtime_agents(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for runtime in self._runtimes.values():
            if runtime.status in TERMINAL_STATES:
                continue
            ui_status = (
                "running"
                if runtime.status in {"queued", "running", "waiting_input"}
                else "error"
            )
            out.append(
                {
                    "id": runtime.agent_id,
                    "label": f"Equipment {runtime.prototype_name} ({runtime.runtime_id})",
                    "type": "equipment",
                    "status": ui_status,
                }
            )
        return sorted(out, key=lambda x: x["id"])

    def count_active_for_prototype(self, prototype_name: str) -> int:
        """Count non-terminal runtimes for one equipment prototype."""
        try:
            normalized = self.prototype_store.normalize_name(prototype_name)
        except ValueError:
            return 0
        return sum(
            1
            for runtime in self._runtimes.values()
            if runtime.prototype_name == normalized and runtime.status not in TERMINAL_STATES
        )

    async def wait_for_terminal(
        self,
        runtime_id: str,
        *,
        timeout_seconds: int | None = None,
    ) -> EquipmentRuntime:
        runtime = self.get_runtime(runtime_id)
        if runtime is None:
            raise ValueError(f"Unknown runtime id: {runtime_id}")
        if runtime.status in TERMINAL_STATES:
            return runtime
        if timeout_seconds is None:
            await runtime.done_event.wait()
        else:
            await asyncio.wait_for(runtime.done_event.wait(), timeout=timeout_seconds)
        return runtime

    # ------------------------------------------------------------------
    # Status / callback paths (called from runtime tools)
    # ------------------------------------------------------------------

    async def update_status(
        self,
        *,
        runtime_id: str,
        status_message: str,
        progress_pct: float | None = None,
        status: RuntimeState | None = None,
    ) -> EquipmentRuntime:
        async with self._lock:
            runtime = self._runtimes.get(runtime_id)
            if runtime is None:
                raise ValueError(f"Unknown runtime id: {runtime_id}")
            if runtime.status in TERMINAL_STATES:
                return runtime
            runtime.mark_heartbeat(status_message, progress_pct)
            if status is not None and status not in TERMINAL_STATES:
                runtime.status = status

        await self._publish_outbound(
            msg_type="equipment_status",
            runtime=runtime,
            text=status_message,
        )
        return runtime

    async def report_completion(
        self,
        *,
        runtime_id: str,
        status: str,
        summary: str,
        artifacts: dict[str, Any] | None = None,
        notify_channel: str = "system",
        notify_chat_id: str | None = None,
    ) -> EquipmentRuntime:
        normalized = _normalize_terminal_status(status)
        async with self._lock:
            runtime = self._runtimes.get(runtime_id)
            if runtime is None:
                raise ValueError(f"Unknown runtime id: {runtime_id}")
            if runtime.status in TERMINAL_STATES:
                return runtime

            runtime.status = normalized
            runtime.status_message = summary or normalized
            runtime.summary = summary
            runtime.artifacts = dict(artifacts or {})
            runtime.artifacts.setdefault("workspace_path", str(runtime.workspace_path))
            runtime.ended_at = datetime.now(tz=timezone.utc)
            runtime.last_heartbeat_at = runtime.ended_at
            runtime.done_event.set()

        await self._publish_outbound(
            msg_type="equipment_report",
            runtime=runtime,
            text=(
                runtime.summary
                or f"equipment run {runtime.runtime_id} finished: {runtime.status}"
            ),
        )
        if runtime.notify_on_completion:
            await self._notify_caller_inbound(
                runtime=runtime,
                channel=notify_channel,
                chat_id=notify_chat_id or runtime.caller_agent_id,
            )
        return runtime

    # ------------------------------------------------------------------
    # Runtime execution
    # ------------------------------------------------------------------

    async def _run_runtime(
        self,
        *,
        runtime_id: str,
        instruction: str,
        notify_channel: str,
        notify_chat_id: str,
    ) -> None:
        runtime = self._runtimes[runtime_id]
        runtime.mark_started()
        await self._publish_outbound(
            msg_type="equipment_status",
            runtime=runtime,
            text="running",
        )

        proto = self.prototype_store.get(runtime.prototype_name)
        if proto is None:
            await self.report_completion(
                runtime_id=runtime_id,
                status="failed",
                summary=f"Prototype {runtime.prototype_name!r} disappeared before execution.",
                notify_channel=notify_channel,
                notify_chat_id=notify_chat_id,
            )
            return

        from drclaw.agent.equipment_agent import EquipmentAgent

        agent = EquipmentAgent(
            config=self.config,
            provider=self.provider,
            prototype=proto,
            runtime=runtime,
            manager=self,
            env_store=self.env_store,
        )
        agent.loop.usage_store = self.usage_store

        try:
            _ = await agent.process_direct(instruction)
            latest = self._runtimes.get(runtime_id)
            if latest and latest.status not in TERMINAL_STATES:
                # Give the runtime one explicit recovery chance to send the required terminal
                # callback before failing the run.
                _ = await agent.process_direct(
                    "You finished without calling report_to_caller. "
                    "Call report_to_caller exactly once now with final status and summary."
                )
                latest = self._runtimes.get(runtime_id)
            if latest and latest.status not in TERMINAL_STATES:
                await self.report_completion(
                    runtime_id=runtime_id,
                    status="failed",
                    summary=(
                        "Equipment run finished without calling report_to_caller. "
                        "Call report_to_caller before exiting."
                    ),
                    notify_channel=notify_channel,
                    notify_chat_id=notify_chat_id,
                )
        except asyncio.CancelledError:
            await self.report_completion(
                runtime_id=runtime_id,
                status="cancelled",
                summary="Equipment run was cancelled.",
                notify_channel=notify_channel,
                notify_chat_id=notify_chat_id,
            )
            raise
        except Exception as exc:
            logger.exception("Equipment runtime {} crashed", runtime_id)
            await self.report_completion(
                runtime_id=runtime_id,
                status="failed",
                summary=f"Equipment run crashed: {exc}",
                notify_channel=notify_channel,
                notify_chat_id=notify_chat_id,
            )
        finally:
            logger.info(
                "Equipment runtime {} completed; preserved workspace at {}",
                runtime.runtime_id,
                runtime.workspace_path,
            )

    def _on_task_done(self, runtime_id: str, task: asyncio.Task[None]) -> None:
        runtime = self._runtimes.get(runtime_id)
        if runtime is None:
            return
        if task.cancelled() and runtime.status not in TERMINAL_STATES:
            runtime.status = "cancelled"
            runtime.done_event.set()
            runtime.ended_at = datetime.now(tz=timezone.utc)
        elif (exc := task.exception()) is not None and runtime.status not in TERMINAL_STATES:
            logger.error("Equipment runtime {} failed: {}", runtime_id, exc)
            runtime.status = "failed"
            runtime.status_message = str(exc)
            runtime.done_event.set()
            runtime.ended_at = datetime.now(tz=timezone.utc)

    def _mark_timed_out_if_stale(self, runtime: EquipmentRuntime) -> None:
        if runtime.status in TERMINAL_STATES:
            return
        now = datetime.now(tz=timezone.utc)
        delta = (now - runtime.last_heartbeat_at).total_seconds()
        if delta > self.heartbeat_timeout_seconds:
            runtime.status = "timed_out"
            runtime.status_message = "timed out waiting for heartbeat"
            runtime.ended_at = now
            runtime.done_event.set()

    # ------------------------------------------------------------------
    # Bus notifications
    # ------------------------------------------------------------------

    async def _publish_outbound(
        self, *, msg_type: str, runtime: EquipmentRuntime, text: str
    ) -> None:
        if self.bus is None:
            return
        await self.bus.publish_outbound(
            OutboundMessage(
                channel="system",
                chat_id=runtime.caller_agent_id,
                text=text,
                source=runtime.agent_id,
                topic=runtime.caller_agent_id,
                msg_type=msg_type,
                metadata={
                    "request_id": runtime.request_id,
                    "runtime_id": runtime.runtime_id,
                    "caller_agent_id": runtime.caller_agent_id,
                    "status": runtime.status,
                    "status_message": runtime.status_message,
                    "progress_pct": runtime.progress_pct,
                    "workspace_path": str(runtime.workspace_path),
                    "artifacts": runtime.artifacts,
                },
            )
        )

    async def _notify_caller_inbound(
        self,
        *,
        runtime: EquipmentRuntime,
        channel: str,
        chat_id: str,
    ) -> None:
        if self.bus is None:
            return
        text = (
            f"[equipment:{runtime.prototype_name}] "
            f"request {runtime.request_id} {runtime.status}: {runtime.summary}"
        )
        if runtime.artifacts:
            text += (
                "\nartifacts: "
                + json.dumps(runtime.artifacts, ensure_ascii=False, default=str)
            )
        await self.bus.publish_inbound(
            InboundMessage(
                channel=channel,
                chat_id=chat_id,
                text=text,
                source=runtime.agent_id,
                metadata={
                    "request_id": runtime.request_id,
                    "runtime_id": runtime.runtime_id,
                    "caller_agent_id": runtime.caller_agent_id,
                    "status": runtime.status,
                    "status_message": runtime.status_message,
                    "workspace_path": str(runtime.workspace_path),
                    "artifacts": runtime.artifacts,
                },
            ),
            topic=runtime.caller_agent_id,
        )


def _normalize_terminal_status(status: str) -> RuntimeState:
    s = status.strip().lower()
    if s in {"success", "succeeded", "done", "ok"}:
        return "succeeded"
    if s in {"fail", "failed", "error"}:
        return "failed"
    if s in {"cancel", "cancelled", "canceled"}:
        return "cancelled"
    if s in {"timeout", "timed_out"}:
        return "timed_out"
    return "failed"
