"""WebAdapter — serves a browser UI over HTTP + WebSocket."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web
from loguru import logger

from drclaw.frontends.web.chat_files import ChatFileStore
from drclaw.frontends.web.routes import (
    handle_activate_agent,
    handle_activate_hub_template,
    handle_agent_history,
    handle_agents,
    handle_asset,
    handle_download_chat_file,
    handle_external_callback,
    handle_get_config,
    handle_get_hub_template,
    handle_get_publish_draft,
    handle_get_skill_content,
    handle_index,
    handle_list_hub_templates,
    handle_list_skills,
    handle_publish_agent_to_hub,
    handle_put_config,
    handle_statistics,
    handle_upload_chat_files,
    handle_upload_skill,
    handle_ws,
)

if TYPE_CHECKING:
    from drclaw.daemon.kernel import Kernel


@web.middleware
async def _cors_middleware(request: web.Request, handler):
    """Allow cross-origin requests from Tauri dev webview (localhost:5173)."""
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


class WebAdapter:
    adapter_id = "web"

    def __init__(self, host: str = "127.0.0.1", port: int = 8080) -> None:
        self.host = host
        self.port = port
        self._kernel: Kernel | None = None
        self._connections: dict[str, web.WebSocketResponse] = {}
        self._runner: web.AppRunner | None = None
        self._drain_task: asyncio.Task[None] | None = None
        self._inbound_task: asyncio.Task[None] | None = None

    async def start(self, kernel: Kernel) -> None:
        self._kernel = kernel

        app = web.Application(middlewares=[_cors_middleware])
        app["adapter"] = self
        app["kernel"] = kernel
        app["config_path"] = kernel.config.data_path / "config.json"
        app.router.add_get("/", handle_index)
        app.router.add_get("/assets/{filename:.+}", handle_asset)
        app.router.add_get("/api/agents", handle_agents)
        app.router.add_post("/api/agents/{agent_id}/activate", handle_activate_agent)
        app.router.add_get("/api/agents/{agent_id}/history", handle_agent_history)
        app.router.add_get("/api/agents/{agent_id}/publish-draft", handle_get_publish_draft)
        app.router.add_post("/api/agents/{agent_id}/publish-to-hub", handle_publish_agent_to_hub)
        app.router.add_get("/api/statistics", handle_statistics)
        app.router.add_get("/api/config", handle_get_config)
        app.router.add_put("/api/config", handle_put_config)
        app.router.add_get("/api/skills", handle_list_skills)
        app.router.add_get("/api/skills/{name}", handle_get_skill_content)
        app.router.add_post("/api/skills/upload", handle_upload_skill)
        app.router.add_post("/api/chat/files/upload", handle_upload_chat_files)
        app.router.add_get("/api/chat/files/{file_id}", handle_download_chat_file)
        app.router.add_post("/api/external/callback", handle_external_callback)
        app.router.add_get("/api/agent-hub", handle_list_hub_templates)
        app.router.add_get("/api/agent-hub/{name}", handle_get_hub_template)
        app.router.add_post("/api/agent-hub/{name}/activate", handle_activate_hub_template)
        app.router.add_get("/ws", handle_ws)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info("Web UI listening on http://{}:{}", self.host, self.port)

        self._drain_task = asyncio.create_task(self._drain_outbound())
        self._inbound_task = asyncio.create_task(self._drain_inbound())

    async def stop(self) -> None:
        for task in (self._drain_task, self._inbound_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        for ws in list(self._connections.values()):
            await ws.close()
        self._connections.clear()

        if self._runner:
            await self._runner.cleanup()

    def register_connection(self, ws: web.WebSocketResponse) -> str:
        chat_id = uuid.uuid4().hex[:12]
        self._connections[chat_id] = ws
        return chat_id

    def remove_connection(self, chat_id: str) -> None:
        self._connections.pop(chat_id, None)

    async def _drain_inbound(self) -> None:
        """Forward inbound messages to all WS clients so routed messages are visible."""
        if self._kernel is None:
            raise RuntimeError("WebAdapter._drain_inbound called before start()")
        queue = self._kernel.bus.subscribe_inbound_display("web_inbound")
        try:
            while True:
                msg = await queue.get()
                if msg.source == "user":
                    continue
                if not self._kernel.config.daemon.verbose_chat:
                    continue
                payload = {
                    "type": "inbound",
                    "source": msg.source,
                    "topic": msg.topic,
                    "text": msg.text,
                    "metadata": msg.metadata,
                }
                await self._broadcast(payload)
        except asyncio.CancelledError:
            return

    async def _broadcast(self, payload: dict) -> None:
        for cid, ws in list(self._connections.items()):
            if ws.closed:
                self.remove_connection(cid)
                continue
            try:
                await ws.send_json(payload)
            except (ConnectionError, RuntimeError):
                self.remove_connection(cid)

    async def _drain_outbound(self) -> None:
        if self._kernel is None:
            raise RuntimeError("WebAdapter._drain_outbound called before start()")
        queue = self._kernel.bus.subscribe_outbound("web")
        file_store = ChatFileStore(self._kernel.config.data_path)
        try:
            while True:
                msg = await queue.get()
                verbose_chat = bool(self._kernel.config.daemon.verbose_chat)
                if (
                    msg.msg_type == "tool_call"
                    and (
                        not verbose_chat
                        or not self._kernel.config.daemon.show_tool_calls
                    )
                ):
                    continue
                if msg.msg_type in {"equipment_status", "equipment_report"} and not verbose_chat:
                    continue
                if (
                    msg.msg_type.startswith("tool_task_")
                    and not verbose_chat
                ):
                    continue
                payload = {
                    "type": msg.msg_type,
                    "source": msg.source,
                    "topic": msg.topic,
                    "text": msg.text,
                    "metadata": msg.metadata,
                }
                if msg.media:
                    files: list[dict] = []
                    for media_path in msg.media:
                        try:
                            rec = file_store.ingest_outbound_media(Path(media_path))
                            files.append(file_store.public_descriptor(rec))
                        except (OSError, ValueError) as exc:
                            logger.warning("Skipping outbound media '{}': {}", media_path, exc)
                    if files:
                        payload["files"] = files
                await self._broadcast(payload)
        except asyncio.CancelledError:
            return
