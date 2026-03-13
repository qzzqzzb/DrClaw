"""Tools for calling and operating equipment runtimes."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from drclaw.tools.base import Tool

if TYPE_CHECKING:
    from drclaw.equipment.manager import EquipmentRuntimeManager


class UseEquipmentTool(Tool):
    """Spawn an equipment runtime for the caller agent."""

    def __init__(
        self,
        manager: EquipmentRuntimeManager,
        *,
        caller_agent_id: str,
    ) -> None:
        self._manager = manager
        self._caller_agent_id = caller_agent_id

    @property
    def name(self) -> str:
        return "use_equipment"

    @property
    def description(self) -> str:
        return (
            "Run an equipment agent prototype with an instruction. "
            "Use this when you need specialized skill/tool execution."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prototype_name": {
                    "type": "string",
                    "description": (
                        "Equipment prototype name under ~/.drclaw/equipments/<name>/skills."
                    ),
                },
                "instruction": {
                    "type": "string",
                    "description": "Task instruction sent to the equipment runtime.",
                },
                "await_result": {
                    "type": "boolean",
                    "description": "If true, wait until completion; else return immediately.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional timeout when await_result=true.",
                },
            },
            "required": ["prototype_name", "instruction"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        prototype_name: str = params["prototype_name"]
        instruction: str = params["instruction"]
        await_result: bool = params.get("await_result", False)
        timeout_seconds: int | None = params.get("timeout_seconds")
        try:
            runtime = await self._manager.start_run(
                prototype_name=prototype_name,
                instruction=instruction,
                caller_agent_id=self._caller_agent_id,
                notify_on_completion=not await_result,
            )
        except Exception as exc:
            return f"Error: failed to start equipment run: {exc}"

        if not await_result:
            return (
                f"Started equipment '{runtime.prototype_name}' "
                f"(runtime_id={runtime.runtime_id}, request_id={runtime.request_id})."
            )

        try:
            runtime = await self._manager.wait_for_terminal(
                runtime.runtime_id, timeout_seconds=timeout_seconds,
            )
        except asyncio.TimeoutError:
            live = self._manager.get_runtime(runtime.runtime_id)
            if live is None:
                return (
                    "Error: waiting for equipment result timed out, and runtime is no longer "
                    "available."
                )
            return (
                "Error: waiting for equipment result timed out. "
                f"Run is still active (request_id={live.request_id}, runtime_id={live.runtime_id}, "
                f"status={live.status}, msg={live.status_message}). "
                "Do not start a duplicate run yet; check with "
                "get_equipment_run_status/list_active_equipment_runs."
            )
        except Exception as exc:
            return f"Error: waiting for equipment result failed: {exc}"

        return _format_runtime_terminal(runtime)


class GetEquipmentRunStatusTool(Tool):
    """Read the current status of an equipment runtime."""

    def __init__(self, manager: EquipmentRuntimeManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "get_equipment_run_status"

    @property
    def description(self) -> str:
        return "Get status for one equipment run by request_id or runtime_id."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "runtime_id": {"type": "string"},
            },
        }

    async def execute(self, params: dict[str, Any]) -> str:
        request_id: str | None = params.get("request_id")
        runtime_id: str | None = params.get("runtime_id")
        runtime = None
        if request_id:
            runtime = self._manager.get_by_request(request_id)
        elif runtime_id:
            runtime = self._manager.get_runtime(runtime_id)
        else:
            return "Error: one of request_id or runtime_id is required."

        if runtime is None:
            return "No matching equipment run."

        return _format_runtime_status(runtime)


class ListActiveEquipmentRunsTool(Tool):
    """List active equipment runs for a caller agent."""

    def __init__(
        self,
        manager: EquipmentRuntimeManager,
        *,
        caller_agent_id: str,
    ) -> None:
        self._manager = manager
        self._caller_agent_id = caller_agent_id

    @property
    def name(self) -> str:
        return "list_active_equipment_runs"

    @property
    def description(self) -> str:
        return "List active (non-terminal) equipment runs for this agent."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, params: dict[str, Any]) -> str:
        runs = self._manager.list_for_caller(self._caller_agent_id, include_terminal=False)
        if not runs:
            return "No active equipment runs."
        lines = ["Active equipment runs:"]
        for r in runs:
            lines.append(
                f"- request_id={r.request_id} runtime_id={r.runtime_id} "
                f"prototype={r.prototype_name} status={r.status} msg={r.status_message}"
            )
        return "\n".join(lines)


class UpdateEquipmentStatusTool(Tool):
    """Equipment runtime tool: send non-terminal status updates."""

    def __init__(self, manager: EquipmentRuntimeManager, runtime_id: str) -> None:
        self._manager = manager
        self._runtime_id = runtime_id

    @property
    def name(self) -> str:
        return "update_status"

    @property
    def description(self) -> str:
        return "Update caller-visible status for this long-running equipment task."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status_message": {"type": "string"},
                "progress_pct": {"type": "number"},
            },
            "required": ["status_message"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        status_message: str = params["status_message"]
        progress_pct = params.get("progress_pct")
        runtime = await self._manager.update_status(
            runtime_id=self._runtime_id,
            status_message=status_message,
            progress_pct=progress_pct,
        )
        return f"Status updated: {runtime.status} ({runtime.status_message})"


class ReportToCallerTool(Tool):
    """Equipment runtime tool: send terminal completion to caller."""

    def __init__(self, manager: EquipmentRuntimeManager, runtime_id: str) -> None:
        self._manager = manager
        self._runtime_id = runtime_id

    @property
    def name(self) -> str:
        return "report_to_caller"

    @property
    def description(self) -> str:
        return "Finalize this equipment task and notify the caller agent."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Terminal status: success|failed|cancelled|timed_out",
                },
                "summary": {"type": "string"},
                "artifacts": {"type": "object"},
            },
            "required": ["status", "summary"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        status: str = params["status"]
        summary: str = params["summary"]
        artifacts = params.get("artifacts")
        runtime = await self._manager.report_completion(
            runtime_id=self._runtime_id,
            status=status,
            summary=summary,
            artifacts=artifacts,
        )
        return f"Reported to caller: {runtime.status} ({runtime.request_id})"


def _format_runtime_status(runtime: Any) -> str:
    base = (
        f"request_id={runtime.request_id} runtime_id={runtime.runtime_id} "
        f"prototype={runtime.prototype_name} caller={runtime.caller_agent_id} "
        f"status={runtime.status} msg={runtime.status_message} "
        f"workspace_path={runtime.workspace_path}"
    )
    if runtime.artifacts:
        artifacts = json.dumps(runtime.artifacts, ensure_ascii=False, default=str)
        return f"{base}\nartifacts: {artifacts}"
    return base


def _format_runtime_terminal(runtime: Any) -> str:
    base = _format_runtime_status(runtime)
    if runtime.summary:
        return f"{base}\nsummary: {runtime.summary}"
    return base
