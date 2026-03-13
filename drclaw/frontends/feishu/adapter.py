"""Feishu/Lark frontend adapter using WebSocket long connection."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any

from loguru import logger

from drclaw.daemon.kernel import Kernel
from drclaw.models.messages import InboundMessage


def _load_lark_sdk() -> tuple[Any, Any]:
    """Import lark-oapi lazily so startup errors are explicit and actionable."""
    try:
        import lark_oapi as lark
        from lark_oapi.api.im import v1 as im_v1
    except ImportError as exc:
        raise RuntimeError(
            "Feishu frontend requires 'lark-oapi'. Install dependencies and retry."
        ) from exc
    return lark, im_v1


def _extract_post_text(content_json: dict[str, Any]) -> str:
    """Extract plain text from Feishu post (rich text) message content."""

    def _extract_from_lang(lang_content: Any) -> str:
        if not isinstance(lang_content, dict):
            return ""
        title = lang_content.get("title", "")
        blocks = lang_content.get("content", [])
        if not isinstance(blocks, list):
            return ""

        parts: list[str] = []
        if isinstance(title, str) and title.strip():
            parts.append(title.strip())

        for row in blocks:
            if not isinstance(row, list):
                continue
            for el in row:
                if not isinstance(el, dict):
                    continue
                tag = el.get("tag")
                if tag in {"text", "a"}:
                    text = el.get("text", "")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
                elif tag == "at":
                    user_name = el.get("user_name", "user")
                    if isinstance(user_name, str) and user_name.strip():
                        parts.append(f"@{user_name.strip()}")

        return " ".join(parts).strip()

    direct = _extract_from_lang(content_json)
    if direct:
        return direct

    for lang_key in ("zh_cn", "en_us", "ja_jp"):
        text = _extract_from_lang(content_json.get(lang_key))
        if text:
            return text

    return ""


class FeishuAdapter:
    """DrClaw frontend adapter for Feishu/Lark long connection mode."""

    adapter_id = "feishu"
    _TABLE_RE = re.compile(
        r"((?:^[ \t]*\|.+\|[ \t]*\n)(?:^[ \t]*\|[-:\s|]+\|[ \t]*\n)(?:^[ \t]*\|.+\|[ \t]*\n?)+)",
        re.MULTILINE,
    )
    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    _CODE_BLOCK_RE = re.compile(r"(```[\s\S]*?```)", re.MULTILINE)
    _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
    _AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".opus", ".amr"}
    _FILE_TYPE_MAP = {
        ".pdf": "pdf",
        ".doc": "doc",
        ".docx": "docx",
        ".xls": "xls",
        ".xlsx": "xlsx",
        ".ppt": "ppt",
        ".pptx": "pptx",
        ".zip": "zip",
        ".rar": "rar",
        ".csv": "csv",
        ".txt": "stream",
    }

    def __init__(self) -> None:
        self._kernel: Kernel | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

        self._lark: Any = None
        self._im_v1: Any = None
        self._client: Any = None

        self._ws_thread: threading.Thread | None = None
        self._outbound_task: asyncio.Task[None] | None = None
        self._outbound_queue: asyncio.Queue | None = None

        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()

    async def start(self, kernel: Kernel) -> None:
        if self._running:
            return

        cfg = kernel.config.feishu
        if not cfg.app_id or not cfg.app_secret:
            raise RuntimeError("Feishu app_id and app_secret must be configured")

        self._kernel = kernel
        self._loop = asyncio.get_running_loop()
        self._lark, self._im_v1 = _load_lark_sdk()

        self._client = (
            self._lark.Client.builder()
            .app_id(cfg.app_id)
            .app_secret(cfg.app_secret)
            .log_level(self._lark.LogLevel.INFO)
            .build()
        )

        self._running = True
        self._outbound_queue = kernel.bus.subscribe_outbound("feishu")
        self._start_ws_thread()
        self._outbound_task = asyncio.create_task(self._drain_outbound())
        logger.info("Feishu adapter started (WebSocket long connection)")

    async def stop(self) -> None:
        self._running = False

        if self._outbound_task and not self._outbound_task.done():
            self._outbound_task.cancel()
            try:
                await self._outbound_task
            except asyncio.CancelledError:
                pass

        if self._kernel is not None:
            self._kernel.bus.unsubscribe_outbound("feishu")

        # lark.ws.Client has no explicit stop method; process shutdown closes it.
        self._kernel = None
        self._loop = None
        self._outbound_queue = None
        logger.info("Feishu adapter stopped")

    def _start_ws_thread(self) -> None:
        self._ws_thread = threading.Thread(
            target=self._run_ws_loop,
            daemon=True,
            name="drclaw-feishu-ws",
        )
        self._ws_thread.start()

    def _run_ws_loop(self) -> None:
        # lark-oapi websocket client binds to the current event loop.
        # Run it on a dedicated thread-local loop to avoid conflicting with
        # the daemon's already-running asyncio loop.
        ws_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(ws_loop)
        ws_client_mod = importlib.import_module("lark_oapi.ws.client")
        ws_client_mod.loop = ws_loop

        while self._running:
            try:
                kernel = self._kernel
                if kernel is None:
                    break

                cfg = kernel.config.feishu
                event_handler = (
                    self._lark.EventDispatcherHandler.builder(
                        cfg.encrypt_key or "",
                        cfg.verification_token or "",
                    )
                    .register_p2_im_message_receive_v1(self._on_message_sync)
                    .build()
                )
                ws_client = self._lark.ws.Client(
                    cfg.app_id,
                    cfg.app_secret,
                    event_handler=event_handler,
                    log_level=self._lark.LogLevel.INFO,
                )

                ws_client.start()
            except Exception as exc:  # pragma: no cover - defensive path
                logger.warning("Feishu WebSocket error: {}", exc)
            if self._running and kernel is not None:
                time.sleep(kernel.config.feishu.reconnect_interval_seconds)

        try:
            ws_loop.stop()
            ws_loop.close()
        except Exception:  # pragma: no cover - defensive path
            pass

    async def _drain_outbound(self) -> None:
        kernel = self._kernel
        if kernel is None:
            raise RuntimeError("FeishuAdapter._drain_outbound called before start()")

        queue = self._outbound_queue
        if queue is None:
            queue = kernel.bus.subscribe_outbound("feishu")
        while True:
            try:
                msg = await queue.get()
            except asyncio.CancelledError:
                return

            if msg.channel != self.adapter_id or msg.msg_type != "chat":
                continue

            text = msg.text.strip()
            if not text and not msg.media:
                continue

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._send_chat_sync, msg.chat_id, text, msg.media)

    def _send_chat_sync(self, receive_id: str, text: str, media: list[str] | None = None) -> None:
        self._send_media_sync(receive_id, media or [])

        if not text:
            return

        if self._send_interactive_sync(receive_id, text):
            return

        logger.warning("Interactive card send failed; falling back to text message")
        if not self._send_text_sync(receive_id, text):
            logger.error("Feishu text fallback send failed")

    def _send_media_sync(self, receive_id: str, media: list[str]) -> None:
        for file_path in media:
            if not os.path.isfile(file_path):
                logger.warning("Feishu media file not found: {}", file_path)
                continue

            ext = os.path.splitext(file_path)[1].lower()
            if ext in self._IMAGE_EXTS:
                image_key = self._upload_image_sync(file_path)
                if image_key:
                    self._send_message_sync(
                        receive_id,
                        "image",
                        json.dumps({"image_key": image_key}, ensure_ascii=False),
                    )
                continue

            file_key = self._upload_file_sync(file_path)
            if not file_key:
                continue

            media_type = "audio" if ext in self._AUDIO_EXTS else "file"
            self._send_message_sync(
                receive_id,
                media_type,
                json.dumps({"file_key": file_key}, ensure_ascii=False),
            )

    def _upload_image_sync(self, file_path: str) -> str | None:
        if not self._client or not self._im_v1:
            return None

        try:
            with open(file_path, "rb") as f:
                request = (
                    self._im_v1.CreateImageRequest.builder()
                    .request_body(
                        self._im_v1.CreateImageRequestBody.builder()
                        .image_type("message")
                        .image(f)
                        .build()
                    )
                    .build()
                )
                response = self._client.im.v1.image.create(request)
        except Exception as exc:
            logger.error("Error uploading Feishu image {}: {}", file_path, exc)
            return None

        if response.success():
            image_key = getattr(getattr(response, "data", None), "image_key", None)
            return image_key if isinstance(image_key, str) else None

        logger.error(
            "Failed to upload Feishu image: code={}, msg={}, log_id={}",
            getattr(response, "code", ""),
            getattr(response, "msg", ""),
            getattr(response, "get_log_id", lambda: "")(),
        )
        return None

    def _upload_file_sync(self, file_path: str) -> str | None:
        if not self._client or not self._im_v1:
            return None

        ext = os.path.splitext(file_path)[1].lower()
        file_type = self._FILE_TYPE_MAP.get(ext, "stream")
        file_name = os.path.basename(file_path)
        try:
            with open(file_path, "rb") as f:
                request = (
                    self._im_v1.CreateFileRequest.builder()
                    .request_body(
                        self._im_v1.CreateFileRequestBody.builder()
                        .file_type(file_type)
                        .file_name(file_name)
                        .file(f)
                        .build()
                    )
                    .build()
                )
                response = self._client.im.v1.file.create(request)
        except Exception as exc:
            logger.error("Error uploading Feishu file {}: {}", file_path, exc)
            return None

        if response.success():
            file_key = getattr(getattr(response, "data", None), "file_key", None)
            return file_key if isinstance(file_key, str) else None

        logger.error(
            "Failed to upload Feishu file: code={}, msg={}, log_id={}",
            getattr(response, "code", ""),
            getattr(response, "msg", ""),
            getattr(response, "get_log_id", lambda: "")(),
        )
        return None

    @staticmethod
    def _parse_md_table(table_text: str) -> dict[str, Any] | None:
        """Parse a markdown table into a Feishu table card element."""

        lines = [line.strip() for line in table_text.strip().split("\n") if line.strip()]
        if len(lines) < 3:
            return None

        def _split_row(row: str) -> list[str]:
            return [cell.strip() for cell in row.strip("|").split("|")]

        headers = _split_row(lines[0])
        rows = [_split_row(row) for row in lines[2:]]
        columns = [
            {"tag": "column", "name": f"c{idx}", "display_name": header, "width": "auto"}
            for idx, header in enumerate(headers)
        ]
        table_rows = [
            {f"c{idx}": row[idx] if idx < len(row) else "" for idx in range(len(headers))}
            for row in rows
        ]
        return {"tag": "table", "page_size": len(rows) + 1, "columns": columns, "rows": table_rows}

    def _split_headings(self, content: str) -> list[dict[str, Any]]:
        """Split content by markdown headings while preserving fenced code blocks."""

        protected = content
        code_blocks: list[str] = []
        for match in self._CODE_BLOCK_RE.finditer(content):
            code_blocks.append(match.group(1))
            protected = protected.replace(match.group(1), f"\x00CODE{len(code_blocks)-1}\x00", 1)

        elements: list[dict[str, Any]] = []
        last_end = 0
        for match in self._HEADING_RE.finditer(protected):
            before = protected[last_end:match.start()].strip()
            if before:
                elements.append({"tag": "markdown", "content": before})

            heading_text = match.group(2).strip()
            elements.append(
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**{heading_text}**"},
                }
            )
            last_end = match.end()

        remaining = protected[last_end:].strip()
        if remaining:
            elements.append({"tag": "markdown", "content": remaining})

        for idx, code_block in enumerate(code_blocks):
            placeholder = f"\x00CODE{idx}\x00"
            for element in elements:
                if element.get("tag") == "markdown" and isinstance(element.get("content"), str):
                    element["content"] = element["content"].replace(placeholder, code_block)

        return elements or [{"tag": "markdown", "content": content}]

    def _build_card_elements(self, content: str) -> list[dict[str, Any]]:
        """Build Feishu card elements from markdown-like text."""

        elements: list[dict[str, Any]] = []
        last_end = 0
        for match in self._TABLE_RE.finditer(content):
            before = content[last_end:match.start()]
            if before.strip():
                elements.extend(self._split_headings(before))

            table = self._parse_md_table(match.group(1))
            elements.append(table or {"tag": "markdown", "content": match.group(1)})
            last_end = match.end()

        remaining = content[last_end:]
        if remaining.strip():
            elements.extend(self._split_headings(remaining))

        return elements or [{"tag": "markdown", "content": content}]

    def _send_interactive_sync(self, receive_id: str, text: str) -> bool:
        card = {"config": {"wide_screen_mode": True}, "elements": self._build_card_elements(text)}
        return self._send_message_sync(
            receive_id,
            "interactive",
            json.dumps(card, ensure_ascii=False),
        )

    def _send_text_sync(self, receive_id: str, text: str) -> bool:
        return self._send_message_sync(
            receive_id,
            "text",
            json.dumps({"text": text}, ensure_ascii=False),
        )

    def _send_message_sync(self, receive_id: str, msg_type: str, content: str) -> bool:
        if not self._client or not self._im_v1:
            return False

        receive_id_type = "chat_id" if receive_id.startswith("oc_") else "open_id"
        request = (
            self._im_v1.CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                self._im_v1.CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type(msg_type)
                .content(content)
                .build()
            )
            .build()
        )

        try:
            response = self._client.im.v1.message.create(request)
        except Exception as exc:
            logger.error("Error sending Feishu {} message: {}", msg_type, exc)
            return False

        if response.success():
            return True

        logger.error(
            "Failed to send Feishu {} message: code={}, msg={}, log_id={}",
            msg_type,
            getattr(response, "code", ""),
            getattr(response, "msg", ""),
            getattr(response, "get_log_id", lambda: "")(),
        )
        return False

    def _on_message_sync(self, data: Any) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return

        future = asyncio.run_coroutine_threadsafe(self._on_message(data), loop)

        def _log_exception(fut: Any) -> None:
            try:
                fut.result()
            except Exception as exc:  # pragma: no cover - defensive path
                logger.error("Error processing Feishu message: {}", exc)

        future.add_done_callback(_log_exception)

    def _is_allowed(self, sender_open_id: str) -> bool:
        kernel = self._kernel
        if kernel is None:
            return False

        allow_from = kernel.config.feishu.allow_from
        return not allow_from or sender_open_id in allow_from

    @staticmethod
    def _extract_text(msg_type: str, raw_content: str) -> str:
        try:
            content_json = json.loads(raw_content) if raw_content else {}
        except json.JSONDecodeError:
            content_json = {}

        if msg_type == "text":
            text = content_json.get("text", "")
            return text.strip() if isinstance(text, str) else ""

        if msg_type == "post":
            return _extract_post_text(content_json)

        return ""

    def _track_message_id(self, message_id: str) -> bool:
        if message_id in self._processed_message_ids:
            return False

        self._processed_message_ids[message_id] = None
        while len(self._processed_message_ids) > 1000:
            self._processed_message_ids.popitem(last=False)
        return True

    async def _on_message(self, data: Any) -> None:
        kernel = self._kernel
        if not self._running or kernel is None:
            return

        event = getattr(data, "event", None)
        message = getattr(event, "message", None)
        sender = getattr(event, "sender", None)
        if message is None or sender is None:
            return

        message_id = getattr(message, "message_id", "")
        if not isinstance(message_id, str) or not message_id:
            return
        if not self._track_message_id(message_id):
            return

        if getattr(sender, "sender_type", "") == "bot":
            return

        sender_id = getattr(sender, "sender_id", None)
        sender_open_id = getattr(sender_id, "open_id", "") if sender_id else ""
        if not isinstance(sender_open_id, str) or not sender_open_id:
            return

        if not self._is_allowed(sender_open_id):
            logger.warning("Feishu sender not in allowlist: {}", sender_open_id)
            return

        chat_id = getattr(message, "chat_id", "")
        chat_type = getattr(message, "chat_type", "")
        msg_type = getattr(message, "message_type", "")
        raw_content = getattr(message, "content", "")

        text = self._extract_text(msg_type, raw_content)
        if not text:
            return

        reply_to = chat_id if chat_type == "group" and chat_id else sender_open_id
        await kernel.bus.publish_inbound(
            InboundMessage(
                channel="feishu",
                chat_id=reply_to,
                text=text,
                source=f"feishu:{sender_open_id}",
            ),
            topic="main",
        )


Adapter = FeishuAdapter

__all__ = ["Adapter", "FeishuAdapter"]
