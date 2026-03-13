"""Tests for the Feishu frontend adapter."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from drclaw.bus.queue import MessageBus
from drclaw.config.schema import DrClawConfig, FeishuConfig
from drclaw.daemon.frontend import FrontendAdapter
from drclaw.frontends.feishu.adapter import FeishuAdapter
from drclaw.models.messages import OutboundMessage


class _FakeResponse:
    def __init__(self, ok: bool = True, data: SimpleNamespace | None = None) -> None:
        self._ok = ok
        self.code = 0
        self.msg = ""
        self.data = data or SimpleNamespace()

    def success(self) -> bool:
        return self._ok

    def get_log_id(self) -> str:
        return "log"


class _FakeMessageService:
    def __init__(self) -> None:
        self.requests: list[SimpleNamespace] = []
        self.outcomes: list[bool] = []

    def create(self, request: SimpleNamespace) -> _FakeResponse:
        self.requests.append(request)
        ok = self.outcomes.pop(0) if self.outcomes else True
        return _FakeResponse(ok=ok)


class _FakeImageService:
    def __init__(self) -> None:
        self.requests: list[SimpleNamespace] = []
        self.outcomes: list[bool] = []
        self.keys: list[str] = []

    def create(self, request: SimpleNamespace) -> _FakeResponse:
        self.requests.append(request)
        ok = self.outcomes.pop(0) if self.outcomes else True
        key = self.keys.pop(0) if self.keys else "img_key_default"
        return _FakeResponse(ok=ok, data=SimpleNamespace(image_key=key))


class _FakeFileService:
    def __init__(self) -> None:
        self.requests: list[SimpleNamespace] = []
        self.outcomes: list[bool] = []
        self.keys: list[str] = []

    def create(self, request: SimpleNamespace) -> _FakeResponse:
        self.requests.append(request)
        ok = self.outcomes.pop(0) if self.outcomes else True
        key = self.keys.pop(0) if self.keys else "file_key_default"
        return _FakeResponse(ok=ok, data=SimpleNamespace(file_key=key))


class _ReqBuilder:
    def __init__(self) -> None:
        self._receive_id_type = ""
        self._body: SimpleNamespace | None = None

    def receive_id_type(self, value: str) -> _ReqBuilder:
        self._receive_id_type = value
        return self

    def request_body(self, body: SimpleNamespace) -> _ReqBuilder:
        self._body = body
        return self

    def build(self) -> SimpleNamespace:
        return SimpleNamespace(receive_id_type=self._receive_id_type, body=self._body)


class _BodyBuilder:
    def __init__(self) -> None:
        self._receive_id = ""
        self._msg_type = ""
        self._content = ""

    def receive_id(self, value: str) -> _BodyBuilder:
        self._receive_id = value
        return self

    def msg_type(self, value: str) -> _BodyBuilder:
        self._msg_type = value
        return self

    def content(self, value: str) -> _BodyBuilder:
        self._content = value
        return self

    def build(self) -> SimpleNamespace:
        return SimpleNamespace(
            receive_id=self._receive_id,
            msg_type=self._msg_type,
            content=self._content,
        )


class _FakeCreateMessageRequest:
    @staticmethod
    def builder() -> _ReqBuilder:
        return _ReqBuilder()


class _FakeCreateMessageRequestBody:
    @staticmethod
    def builder() -> _BodyBuilder:
        return _BodyBuilder()


class _ImageReqBuilder:
    def __init__(self) -> None:
        self._body: SimpleNamespace | None = None

    def request_body(self, body: SimpleNamespace) -> _ImageReqBuilder:
        self._body = body
        return self

    def build(self) -> SimpleNamespace:
        return SimpleNamespace(body=self._body)


class _ImageBodyBuilder:
    def __init__(self) -> None:
        self._image_type = ""
        self._image = None

    def image_type(self, value: str) -> _ImageBodyBuilder:
        self._image_type = value
        return self

    def image(self, value) -> _ImageBodyBuilder:  # noqa: ANN001
        self._image = value
        return self

    def build(self) -> SimpleNamespace:
        return SimpleNamespace(image_type=self._image_type, image=self._image)


class _FakeCreateImageRequest:
    @staticmethod
    def builder() -> _ImageReqBuilder:
        return _ImageReqBuilder()


class _FakeCreateImageRequestBody:
    @staticmethod
    def builder() -> _ImageBodyBuilder:
        return _ImageBodyBuilder()


class _FileReqBuilder:
    def __init__(self) -> None:
        self._body: SimpleNamespace | None = None

    def request_body(self, body: SimpleNamespace) -> _FileReqBuilder:
        self._body = body
        return self

    def build(self) -> SimpleNamespace:
        return SimpleNamespace(body=self._body)


class _FileBodyBuilder:
    def __init__(self) -> None:
        self._file_type = ""
        self._file_name = ""
        self._file = None

    def file_type(self, value: str) -> _FileBodyBuilder:
        self._file_type = value
        return self

    def file_name(self, value: str) -> _FileBodyBuilder:
        self._file_name = value
        return self

    def file(self, value) -> _FileBodyBuilder:  # noqa: ANN001
        self._file = value
        return self

    def build(self) -> SimpleNamespace:
        return SimpleNamespace(
            file_type=self._file_type,
            file_name=self._file_name,
            file=self._file,
        )


class _FakeCreateFileRequest:
    @staticmethod
    def builder() -> _FileReqBuilder:
        return _FileReqBuilder()


class _FakeCreateFileRequestBody:
    @staticmethod
    def builder() -> _FileBodyBuilder:
        return _FileBodyBuilder()


class _FakeEventBuilder:
    def __init__(self) -> None:
        self._handler = None

    def register_p2_im_message_receive_v1(self, handler):
        self._handler = handler
        return self

    def build(self) -> SimpleNamespace:
        return SimpleNamespace(handler=self._handler)


class _FakeEventDispatcherHandler:
    @staticmethod
    def builder(_encrypt_key: str, _verification_token: str) -> _FakeEventBuilder:
        return _FakeEventBuilder()


class _FakeWsClient:
    def __init__(
        self,
        _app_id: str,
        _app_secret: str,
        *,
        event_handler: SimpleNamespace,
        log_level: int,
    ) -> None:
        self.event_handler = event_handler
        self.log_level = log_level

    def start(self) -> None:
        return


class _FakeClientBuilder:
    def __init__(
        self,
        message_service: _FakeMessageService,
        image_service: _FakeImageService,
        file_service: _FakeFileService,
    ) -> None:
        self._message_service = message_service
        self._image_service = image_service
        self._file_service = file_service

    def app_id(self, _value: str) -> _FakeClientBuilder:
        return self

    def app_secret(self, _value: str) -> _FakeClientBuilder:
        return self

    def log_level(self, _value: int) -> _FakeClientBuilder:
        return self

    def build(self) -> SimpleNamespace:
        return SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(
                    message=self._message_service,
                    image=self._image_service,
                    file=self._file_service,
                )
            ),
        )


class _FakeClient:
    message_service = _FakeMessageService()
    image_service = _FakeImageService()
    file_service = _FakeFileService()

    @classmethod
    def builder(cls) -> _FakeClientBuilder:
        return _FakeClientBuilder(cls.message_service, cls.image_service, cls.file_service)


def _build_fake_sdk() -> tuple[
    SimpleNamespace, SimpleNamespace, _FakeMessageService, _FakeImageService, _FakeFileService
]:
    message_service = _FakeMessageService()
    image_service = _FakeImageService()
    file_service = _FakeFileService()
    _FakeClient.message_service = message_service
    _FakeClient.image_service = image_service
    _FakeClient.file_service = file_service

    lark = SimpleNamespace(
        Client=_FakeClient,
        EventDispatcherHandler=_FakeEventDispatcherHandler,
        ws=SimpleNamespace(Client=_FakeWsClient),
        LogLevel=SimpleNamespace(INFO=1),
    )
    im_v1 = SimpleNamespace(
        CreateMessageRequest=_FakeCreateMessageRequest,
        CreateMessageRequestBody=_FakeCreateMessageRequestBody,
        CreateImageRequest=_FakeCreateImageRequest,
        CreateImageRequestBody=_FakeCreateImageRequestBody,
        CreateFileRequest=_FakeCreateFileRequest,
        CreateFileRequestBody=_FakeCreateFileRequestBody,
    )
    return lark, im_v1, message_service, image_service, file_service


def _make_kernel(feishu: FeishuConfig | None = None) -> SimpleNamespace:
    cfg = DrClawConfig(feishu=feishu or FeishuConfig(app_id="cli_x", app_secret="sec_x"))
    return SimpleNamespace(config=cfg, bus=MessageBus())


def _make_event(
    *,
    message_id: str = "m1",
    chat_id: str = "oc_group",
    chat_type: str = "group",
    msg_type: str = "text",
    content: str = '{"text":"hello"}',
    sender_type: str = "user",
    sender_open_id: str = "ou_user",
) -> SimpleNamespace:
    return SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_id=message_id,
                chat_id=chat_id,
                chat_type=chat_type,
                message_type=msg_type,
                content=content,
            ),
            sender=SimpleNamespace(
                sender_type=sender_type,
                sender_id=SimpleNamespace(open_id=sender_open_id),
            ),
        )
    )


def test_feishu_adapter_implements_frontend_protocol():
    adapter = FeishuAdapter()
    assert isinstance(adapter, FrontendAdapter)


@pytest.mark.asyncio
async def test_start_requires_credentials():
    adapter = FeishuAdapter()
    kernel = _make_kernel(FeishuConfig())
    with pytest.raises(RuntimeError, match="app_id and app_secret"):
        await adapter.start(kernel)


@pytest.mark.asyncio
async def test_inbound_group_message_publishes_to_main(monkeypatch):
    lark, im_v1, _, _, _ = _build_fake_sdk()
    monkeypatch.setattr(
        "drclaw.frontends.feishu.adapter._load_lark_sdk",
        lambda: (lark, im_v1),
    )

    adapter = FeishuAdapter()
    monkeypatch.setattr(adapter, "_start_ws_thread", lambda: None)
    kernel = _make_kernel()

    await adapter.start(kernel)
    await adapter._on_message(_make_event())

    msg = await asyncio.wait_for(kernel.bus.consume_inbound("main"), timeout=0.2)
    assert msg.channel == "feishu"
    assert msg.chat_id == "oc_group"
    assert msg.text == "hello"
    assert msg.source == "feishu:ou_user"

    await adapter.stop()


@pytest.mark.asyncio
async def test_inbound_dm_replies_to_sender_open_id(monkeypatch):
    lark, im_v1, _, _, _ = _build_fake_sdk()
    monkeypatch.setattr(
        "drclaw.frontends.feishu.adapter._load_lark_sdk",
        lambda: (lark, im_v1),
    )

    adapter = FeishuAdapter()
    monkeypatch.setattr(adapter, "_start_ws_thread", lambda: None)
    kernel = _make_kernel()

    await adapter.start(kernel)
    await adapter._on_message(
        _make_event(
            chat_id="oc_dm_chat",
            chat_type="p2p",
            sender_open_id="ou_dm_user",
        )
    )

    msg = await asyncio.wait_for(kernel.bus.consume_inbound("main"), timeout=0.2)
    assert msg.chat_id == "ou_dm_user"
    assert msg.source == "feishu:ou_dm_user"

    await adapter.stop()


@pytest.mark.asyncio
async def test_allowlist_blocks_unapproved_sender(monkeypatch):
    lark, im_v1, _, _, _ = _build_fake_sdk()
    monkeypatch.setattr(
        "drclaw.frontends.feishu.adapter._load_lark_sdk",
        lambda: (lark, im_v1),
    )

    adapter = FeishuAdapter()
    monkeypatch.setattr(adapter, "_start_ws_thread", lambda: None)
    kernel = _make_kernel(
        FeishuConfig(
            app_id="cli_x",
            app_secret="sec_x",
            allow_from=["ou_allowed"],
        )
    )

    await adapter.start(kernel)
    await adapter._on_message(_make_event(sender_open_id="ou_blocked"))

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(kernel.bus.consume_inbound("main"), timeout=0.1)

    await adapter.stop()


@pytest.mark.asyncio
async def test_dedup_ignores_same_message_id(monkeypatch):
    lark, im_v1, _, _, _ = _build_fake_sdk()
    monkeypatch.setattr(
        "drclaw.frontends.feishu.adapter._load_lark_sdk",
        lambda: (lark, im_v1),
    )

    adapter = FeishuAdapter()
    monkeypatch.setattr(adapter, "_start_ws_thread", lambda: None)
    kernel = _make_kernel()

    await adapter.start(kernel)
    event = _make_event(message_id="dedup-id")
    await adapter._on_message(event)
    await adapter._on_message(event)

    msg = await asyncio.wait_for(kernel.bus.consume_inbound("main"), timeout=0.2)
    assert msg.text == "hello"
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(kernel.bus.consume_inbound("main"), timeout=0.1)

    await adapter.stop()


@pytest.mark.asyncio
async def test_outbound_sends_feishu_chat_messages_only(monkeypatch):
    lark, im_v1, message_service, _, _ = _build_fake_sdk()
    monkeypatch.setattr(
        "drclaw.frontends.feishu.adapter._load_lark_sdk",
        lambda: (lark, im_v1),
    )

    adapter = FeishuAdapter()
    monkeypatch.setattr(adapter, "_start_ws_thread", lambda: None)
    kernel = _make_kernel()

    await adapter.start(kernel)

    await kernel.bus.publish_outbound(
        OutboundMessage(channel="web", chat_id="oc_group", text="ignore"),
    )
    await kernel.bus.publish_outbound(
        OutboundMessage(
            channel="feishu",
            chat_id="oc_group",
            text="ignore-tool",
            msg_type="tool_call",
        )
    )
    await kernel.bus.publish_outbound(
        OutboundMessage(channel="feishu", chat_id="oc_group", text="hello feishu"),
    )

    await asyncio.sleep(0.1)
    assert len(message_service.requests) == 1
    request = message_service.requests[0]
    assert request.receive_id_type == "chat_id"
    assert request.body.receive_id == "oc_group"
    assert request.body.msg_type == "interactive"
    content = json.loads(request.body.content)
    assert content["config"]["wide_screen_mode"] is True
    assert content["elements"]

    await adapter.stop()


@pytest.mark.asyncio
async def test_outbound_falls_back_to_text_when_interactive_fails(monkeypatch):
    lark, im_v1, message_service, _, _ = _build_fake_sdk()
    monkeypatch.setattr(
        "drclaw.frontends.feishu.adapter._load_lark_sdk",
        lambda: (lark, im_v1),
    )

    adapter = FeishuAdapter()
    monkeypatch.setattr(adapter, "_start_ws_thread", lambda: None)
    kernel = _make_kernel()

    await adapter.start(kernel)
    message_service.outcomes = [False, True]

    await kernel.bus.publish_outbound(
        OutboundMessage(channel="feishu", chat_id="oc_group", text="fallback me"),
    )

    await asyncio.sleep(0.1)
    assert len(message_service.requests) == 2
    first, second = message_service.requests
    assert first.body.msg_type == "interactive"
    assert second.body.msg_type == "text"
    assert json.loads(second.body.content)["text"] == "fallback me"

    await adapter.stop()


@pytest.mark.asyncio
async def test_outbound_uploads_media_before_text(monkeypatch, tmp_path):
    lark, im_v1, message_service, image_service, file_service = _build_fake_sdk()
    monkeypatch.setattr(
        "drclaw.frontends.feishu.adapter._load_lark_sdk",
        lambda: (lark, im_v1),
    )

    image_service.keys = ["img_key_1"]
    file_service.keys = ["file_key_1"]

    image_path = tmp_path / "photo.png"
    image_path.write_bytes(b"png-bytes")
    file_path = tmp_path / "report.txt"
    file_path.write_text("hello", encoding="utf-8")

    adapter = FeishuAdapter()
    monkeypatch.setattr(adapter, "_start_ws_thread", lambda: None)
    kernel = _make_kernel()
    await adapter.start(kernel)

    await kernel.bus.publish_outbound(
        OutboundMessage(
            channel="feishu",
            chat_id="oc_group",
            text="with media",
            media=[str(image_path), str(file_path)],
        )
    )

    await asyncio.sleep(0.1)
    assert len(image_service.requests) == 1
    assert len(file_service.requests) == 1
    assert len(message_service.requests) == 3
    assert [req.body.msg_type for req in message_service.requests] == [
        "image",
        "file",
        "interactive",
    ]
    assert json.loads(message_service.requests[0].body.content)["image_key"] == "img_key_1"
    assert json.loads(message_service.requests[1].body.content)["file_key"] == "file_key_1"

    await adapter.stop()


@pytest.mark.asyncio
async def test_outbound_missing_media_file_still_sends_text(monkeypatch, tmp_path):
    lark, im_v1, message_service, image_service, file_service = _build_fake_sdk()
    monkeypatch.setattr(
        "drclaw.frontends.feishu.adapter._load_lark_sdk",
        lambda: (lark, im_v1),
    )

    adapter = FeishuAdapter()
    monkeypatch.setattr(adapter, "_start_ws_thread", lambda: None)
    kernel = _make_kernel()
    await adapter.start(kernel)

    await kernel.bus.publish_outbound(
        OutboundMessage(
            channel="feishu",
            chat_id="oc_group",
            text="text survives",
            media=[str(tmp_path / "does-not-exist.bin")],
        )
    )

    await asyncio.sleep(0.1)
    assert len(image_service.requests) == 0
    assert len(file_service.requests) == 0
    assert len(message_service.requests) == 1
    assert message_service.requests[0].body.msg_type == "interactive"

    await adapter.stop()


def test_build_card_elements_supports_heading_table_and_code():
    adapter = FeishuAdapter()
    text = (
        "# Title\n"
        "Paragraph intro.\n\n"
        "| Col A | Col B |\n"
        "| --- | --- |\n"
        "| 1 | 2 |\n\n"
        "```python\n"
        "# not-a-heading\n"
        "print('ok')\n"
        "```\n"
    )
    elements = adapter._build_card_elements(text)
    tags = [element.get("tag") for element in elements]

    assert "div" in tags
    assert "table" in tags
    markdown_contents = [
        element.get("content", "")
        for element in elements
        if element.get("tag") == "markdown"
    ]
    assert any("```python" in content for content in markdown_contents)
