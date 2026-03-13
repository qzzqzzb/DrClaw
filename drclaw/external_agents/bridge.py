"""Async webhook bridge for external agents."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import aiohttp

from drclaw.config.schema import DrClawConfig, ExternalAgentConfig
from drclaw.models.messages import InboundMessage, OutboundMessage

_EXTERNAL_PREFIX = "ext:"
_CALLBACK_PATH = "/api/external/callback"
_CALLBACK_BASE_URL = "http://127.0.0.1:8080"


def normalize_external_agent_id(agent_id: str) -> str:
    raw = agent_id.strip()
    if raw.startswith(_EXTERNAL_PREFIX):
        return raw
    return f"{_EXTERNAL_PREFIX}{raw}"


@dataclass
class _PendingRequest:
    request_id: str
    agent_id: str
    origin: Literal["web", "agent"]
    chat_id: str
    origin_topic: str
    timeout_task: asyncio.Task[None]


class ExternalAgentBridge:
    """Routes DrClaw messages to configured external providers via webhooks."""

    def __init__(
        self,
        config: DrClawConfig,
        bus,
        *,
        session: aiohttp.ClientSession | None = None,
        callback_base_url: str = _CALLBACK_BASE_URL,
    ) -> None:
        self._bus = bus
        self._session = session
        self._owns_session = session is None
        self._callback_base_url = callback_base_url.rstrip("/")
        self._agents: dict[str, ExternalAgentConfig] = {}
        self._pending: dict[str, _PendingRequest] = {}
        self.reload_config(config)

    def reload_config(self, config: DrClawConfig) -> None:
        agents: dict[str, ExternalAgentConfig] = {}
        for item in config.external_agents:
            normalized = normalize_external_agent_id(item.id)
            if normalized in agents:
                continue
            agents[normalized] = item
        self._agents = agents

    def is_external_agent(self, agent_id: str) -> bool:
        return normalize_external_agent_id(agent_id) in self._agents

    def list_agent_payloads(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for agent_id in sorted(self._agents.keys()):
            cfg = self._agents[agent_id]
            rows.append(
                {
                    "id": agent_id,
                    "label": cfg.label,
                    "name": cfg.label,
                    "role": cfg.description or "External provider agent",
                    "type": "external",
                    "status": "idle",
                    "avatar": cfg.avatar or None,
                }
            )
        return rows

    async def submit_from_web(
        self,
        *,
        agent_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        chat_id: str,
    ) -> str:
        return await self._submit(
            agent_id=agent_id,
            text=text,
            metadata=metadata or {},
            origin="web",
            chat_id=chat_id,
            origin_topic="",
        )

    async def submit_from_agent(
        self,
        *,
        agent_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        origin_topic: str,
    ) -> str:
        return await self._submit(
            agent_id=agent_id,
            text=text,
            metadata=metadata or {},
            origin="agent",
            chat_id=origin_topic,
            origin_topic=origin_topic,
        )

    async def _submit(
        self,
        *,
        agent_id: str,
        text: str,
        metadata: dict[str, Any],
        origin: Literal["web", "agent"],
        chat_id: str,
        origin_topic: str,
    ) -> str:
        normalized_agent_id = normalize_external_agent_id(agent_id)
        cfg = self._agents.get(normalized_agent_id)
        if cfg is None:
            raise ValueError(f"External agent not configured: {agent_id}")

        request_id = uuid.uuid4().hex
        timeout_task = asyncio.create_task(
            self._expire_request(
                request_id=request_id,
                timeout_seconds=cfg.callback_timeout_seconds,
            )
        )
        self._pending[request_id] = _PendingRequest(
            request_id=request_id,
            agent_id=normalized_agent_id,
            origin=origin,
            chat_id=chat_id,
            origin_topic=origin_topic,
            timeout_task=timeout_task,
        )

        callback_url = f"{self._callback_base_url}{_CALLBACK_PATH}"
        payload = {
            "protocol": "drclaw-external-agent-v1",
            "request_id": request_id,
            "agent_id": normalized_agent_id,
            "text": text,
            "metadata": metadata,
            "callback": {
                "url": callback_url,
                "method": "POST",
            },
        }
        timeout = aiohttp.ClientTimeout(total=cfg.request_timeout_seconds)
        try:
            session = self._get_session()
            async with session.post(cfg.request_url, json=payload, timeout=timeout) as response:
                if response.status >= 400:
                    body = (await response.text()).strip()
                    body_text = f" ({body})" if body else ""
                    raise RuntimeError(
                        f"External agent request failed: HTTP {response.status}{body_text}"
                    )
        except Exception:
            pending = self._pending.pop(request_id, None)
            if pending is not None and not pending.timeout_task.done():
                pending.timeout_task.cancel()
            raise

        return request_id

    async def handle_callback(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        request_id_raw = payload.get("request_id")
        request_id = request_id_raw.strip() if isinstance(request_id_raw, str) else ""
        if not request_id:
            return 400, {"error": "Missing request_id"}

        pending = self._pending.get(request_id)
        if pending is None:
            return 404, {"error": f"Unknown request_id: {request_id}"}

        callback_agent_id_raw = payload.get("agent_id")
        callback_agent_id = (
            normalize_external_agent_id(callback_agent_id_raw)
            if isinstance(callback_agent_id_raw, str) and callback_agent_id_raw.strip()
            else pending.agent_id
        )
        if callback_agent_id != pending.agent_id:
            return 400, {"error": "agent_id does not match pending request"}

        text_raw = payload.get("text")
        error_raw = payload.get("error")
        text = text_raw.strip() if isinstance(text_raw, str) else ""
        error = error_raw.strip() if isinstance(error_raw, str) else ""
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["external_request_id"] = request_id

        if not text and not error:
            return 400, {"error": "Callback must include text or error"}

        popped = self._pending.pop(request_id, None)
        if popped is None:
            return 404, {"error": f"Unknown request_id: {request_id}"}
        pending = popped
        if not pending.timeout_task.done():
            pending.timeout_task.cancel()

        if error:
            await self._route_error(
                pending=pending,
                error=error,
                metadata=metadata,
            )
        else:
            await self._route_text(
                pending=pending,
                text=text,
                metadata=metadata,
            )
        return 202, {"ok": True, "request_id": request_id}

    async def _expire_request(self, *, request_id: str, timeout_seconds: int) -> None:
        try:
            await asyncio.sleep(timeout_seconds)
        except asyncio.CancelledError:
            return
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        metadata = {"external_request_id": request_id}
        await self._route_error(
            pending=pending,
            error=(
                f"External agent timeout after {timeout_seconds}s. "
                "No callback received."
            ),
            metadata=metadata,
        )

    async def _route_text(
        self,
        *,
        pending: _PendingRequest,
        text: str,
        metadata: dict[str, Any],
    ) -> None:
        if pending.origin == "web":
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel="web",
                    chat_id=pending.chat_id,
                    text=text,
                    source=pending.agent_id,
                    topic=pending.agent_id,
                    msg_type="chat",
                    metadata=metadata,
                )
            )
            return

        await self._bus.publish_inbound(
            InboundMessage(
                channel="system",
                chat_id=pending.request_id,
                text=text,
                source=pending.agent_id,
                metadata=metadata,
            ),
            topic=pending.origin_topic,
        )

    async def _route_error(
        self,
        *,
        pending: _PendingRequest,
        error: str,
        metadata: dict[str, Any],
    ) -> None:
        if pending.origin == "web":
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel="web",
                    chat_id=pending.chat_id,
                    text=error,
                    source=pending.agent_id,
                    topic=pending.agent_id,
                    msg_type="error",
                    metadata=metadata,
                )
            )
            return

        await self._bus.publish_inbound(
            InboundMessage(
                channel="system",
                chat_id=pending.request_id,
                text=f"[{pending.agent_id}] Error: {error}",
                source=pending.agent_id,
                metadata=metadata,
            ),
            topic=pending.origin_topic,
        )

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        for pending in list(self._pending.values()):
            if not pending.timeout_task.done():
                pending.timeout_task.cancel()
        self._pending.clear()
        if self._session is not None and self._owns_session:
            await self._session.close()
            self._session = None
