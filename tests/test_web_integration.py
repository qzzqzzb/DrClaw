"""Integration tests for the web frontend using aiohttp test client."""

from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from drclaw.agent.registry import AgentHandle, AgentStatus
from drclaw.bus.queue import MessageBus
from drclaw.config.loader import load_config
from drclaw.config.schema import DrClawConfig
from drclaw.equipment.models import EquipmentPrototype
from drclaw.frontends.web.adapter import WebAdapter, _cors_middleware, _local_only_middleware
from drclaw.frontends.web.routes import (
    _normalize_avatar_for_web,
    handle_activate_agent,
    handle_activate_hub_template,
    handle_agent_history,
    handle_agents,
    handle_asset,
    handle_download_chat_file,
    handle_external_callback,
    handle_get_config,
    handle_get_publish_draft,
    handle_get_hub_template,
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
from drclaw.models.project import Project
from drclaw.session.manager import SessionManager


def _make_mock_kernel(tmp_path):
    from drclaw.usage.store import AgentUsageStore

    kernel = MagicMock()
    kernel.bus = MessageBus()
    kernel.config = DrClawConfig(data_dir=str(tmp_path))
    kernel.usage_store = AgentUsageStore(kernel.config.data_path / "runtime" / "agent-usage.json")
    kernel.equipment_manager.list_runtime_agents.return_value = []
    kernel.project_store.list_projects.return_value = []
    kernel.equipment_manager.prototype_store.list.return_value = []
    kernel.agent_hub_store.list.return_value = []
    kernel.external_agent_bridge.list_agent_payloads.return_value = []
    kernel.external_agent_bridge.is_external_agent.return_value = False

    handle = MagicMock(spec=AgentHandle)
    handle.agent_id = "main"
    handle.label = "Assistant Agent"
    handle.agent_type = "main"
    handle.status = AgentStatus.RUNNING
    kernel.registry.list_agents.return_value = [handle]
    kernel.registry.get.return_value = handle
    return kernel


def _make_app(kernel, adapter):
    app = web.Application(middlewares=[_local_only_middleware, _cors_middleware])
    app["kernel"] = kernel
    app["adapter"] = adapter
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
    app.router.add_get("/api/agent-hub", handle_list_hub_templates)
    app.router.add_get("/api/agent-hub/{name}", handle_get_hub_template)
    app.router.add_post("/api/agent-hub/{name}/activate", handle_activate_hub_template)
    app.router.add_post("/api/chat/files/upload", handle_upload_chat_files)
    app.router.add_get("/api/chat/files/{file_id}", handle_download_chat_file)
    app.router.add_post("/api/external/callback", handle_external_callback)
    app.router.add_get("/ws", handle_ws)
    return app


@pytest.fixture
def kernel(tmp_path):
    return _make_mock_kernel(tmp_path)


@pytest.fixture
def adapter(kernel):
    a = WebAdapter()
    a._kernel = kernel
    return a


def _zip_bytes(entries: dict[str, str]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, content in entries.items():
            zf.writestr(path, content)
    buf.seek(0)
    return buf


# -- GET / ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_returns_html(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/")
        assert resp.status == 200
        text = await resp.text()
        assert "<title>DrClaw</title>" in text
        assert 'data-route="#/monitor"' not in text
        assert 'id="team-workspace"' in text
        assert 'id="team-chat-panel"' in text
        assert 'id="floating-chat-publish"' in text
        assert 'id="add-agent-btn"' in text
        assert 'id="add-agent-from-scratch"' in text
        assert 'id="add-agent-import-hub"' in text
        assert 'id="publish-modal-overlay"' in text
        assert 'id="publish-soul"' in text
        assert 'id="publish-skills-card"' in text
        assert 'id="lang-switch"' in text
        assert 'id="lang-switch-zh"' in text
        assert 'id="lang-switch-en"' in text
        assert 'id="nav-statistics"' in text
        assert '<th data-i18n="statistics_all_time">All-time</th>' in text
        assert '<th data-i18n="statistics_this_run">This run</th>' in text


@pytest.mark.asyncio
async def test_asset_route_supports_nested_paths(kernel, adapter, tmp_path):
    assets_root = kernel.config.data_path / "assets"
    nested = assets_root / "avatars"
    nested.mkdir(parents=True)
    (nested / "17.png").write_bytes(b"png")

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/assets/avatars/17.png")
        assert resp.status == 200
        assert "no-store" in resp.headers.get("Cache-Control", "")


@pytest.mark.asyncio
async def test_rejects_non_loopback_host_header(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/", headers={"Host": "example.com"})
        assert resp.status == 403
        body = await resp.json()
        assert "Localhost" in body["error"]


@pytest.mark.asyncio
async def test_options_allows_loopback_origin(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.options(
            "/api/agents",
            headers={"Origin": "http://127.0.0.1:5173"},
        )
        assert resp.status == 204
        assert resp.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:5173"


@pytest.mark.asyncio
async def test_options_rejects_remote_origin(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.options(
            "/api/agents",
            headers={"Origin": "https://example.com"},
        )
        assert resp.status == 403
        body = await resp.json()
        assert "Origin not allowed" in body["error"]


# -- POST/GET /api/chat/files --------------------------------------------------


@pytest.mark.asyncio
async def test_chat_file_upload_and_download(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        form = FormData()
        form.add_field(
            "files",
            b"hello attachment",
            filename="note.txt",
            content_type="text/plain",
        )
        upload = await client.post("/api/chat/files/upload", data=form)
        assert upload.status == 201
        body = await upload.json()
        assert isinstance(body.get("files"), list)
        assert len(body["files"]) == 1
        uploaded = body["files"][0]
        assert uploaded["name"] == "note.txt"
        assert uploaded["download_url"].startswith("/api/chat/files/")

        download = await client.get(uploaded["download_url"])
        assert download.status == 200
        assert await download.read() == b"hello attachment"


@pytest.mark.asyncio
async def test_chat_file_upload_accepts_docx(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        form = FormData()
        form.add_field(
            "files",
            b"fake docx bytes",
            filename="draft.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        upload = await client.post("/api/chat/files/upload", data=form)
        assert upload.status == 201
        body = await upload.json()
        assert isinstance(body.get("files"), list)
        assert len(body["files"]) == 1
        uploaded = body["files"][0]
        assert uploaded["name"] == "draft.docx"
        assert uploaded["download_url"].startswith("/api/chat/files/")


@pytest.mark.asyncio
async def test_chat_file_upload_rejects_unsupported_extension(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        form = FormData()
        form.add_field(
            "files",
            b"hello",
            filename="run.exe",
            content_type="application/octet-stream",
        )
        resp = await client.post("/api/chat/files/upload", data=form)
        assert resp.status == 400
        body = await resp.json()
        assert "Unsupported file type" in body["error"]


# -- GET /api/agents -----------------------------------------------------------


@pytest.mark.asyncio
async def test_agents_endpoint(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents")
        assert resp.status == 200
        data = await resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "main"
        assert data[0]["label"] == "Assistant Agent"
        assert data[0]["name"] == "虾秘"
        assert data[0]["role"] == "Main orchestrator agent"
        assert data[0]["type"] == "assistant"
        assert data[0]["status"] == "idle"


@pytest.mark.asyncio
async def test_agents_endpoint_zh_locale_uses_display_name(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-zh",
        name="Template Project",
        created_at=now,
        updated_at=now,
        hub_template="cat",
    )
    kernel.project_store.list_projects.return_value = [project]
    template = MagicMock()
    template.name = "cat"
    template.avatar = "/assets/avatar_cat.png"
    template.i18n = {"zh": {"name": "猫咪智能体"}}
    kernel.agent_hub_store.list.return_value = [template]

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents?lang=zh")
        assert resp.status == 200
        data = await resp.json()
        main = next(a for a in data if a["id"] == "main")
        idle = next(a for a in data if a["id"] == f"proj:{project.id}")
        assert main["name"] == "虾秘"
        assert main["display_name"] == "虾秘"
        assert main["display_role"] == "主控协调智能体"
        assert idle["name"] == "Template Project"
        assert idle["display_name"] == "猫咪智能体"
        assert idle["display_role"] == "项目执行智能体"


@pytest.mark.asyncio
async def test_agents_endpoint_includes_idle_projects(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-1",
        name="Dormant Project",
        created_at=now,
        updated_at=now,
    )
    kernel.project_store.list_projects.return_value = [project]

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents")
        assert resp.status == 200
        data = await resp.json()
        ids = {a["id"] for a in data}
        assert "main" in ids
        assert f"proj:{project.id}" in ids
        idle = next(a for a in data if a["id"] == f"proj:{project.id}")
        assert idle["status"] == "idle"
        assert idle["type"] == "student"
        assert idle["label"] == "Dormant Project"


@pytest.mark.asyncio
async def test_agents_endpoint_includes_template_avatar(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-cat",
        name="Cat Project",
        created_at=now,
        updated_at=now,
        hub_template="cat",
    )
    kernel.project_store.list_projects.return_value = [project]
    template = MagicMock()
    template.name = "cat"
    template.avatar = "/assets/avatar_cat.png"
    kernel.agent_hub_store.list.return_value = [template]

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents")
        assert resp.status == 200
        data = await resp.json()
        idle = next(a for a in data if a["id"] == f"proj:{project.id}")
        assert idle["avatar"] == "/assets/avatar_cat.png"


@pytest.mark.asyncio
async def test_agents_endpoint_normalizes_template_avatar_fs_path(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-cat-fs",
        name="Cat Project FS",
        created_at=now,
        updated_at=now,
        hub_template="cat",
    )
    kernel.project_store.list_projects.return_value = [project]
    template = MagicMock()
    template.name = "cat"
    template.avatar = str(kernel.config.data_path / "assets" / "avatars" / "19.png")
    kernel.agent_hub_store.list.return_value = [template]

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents")
        assert resp.status == 200
        data = await resp.json()
        idle = next(a for a in data if a["id"] == f"proj:{project.id}")
        assert idle["avatar"] == "/assets/avatars/19.png"


@pytest.mark.asyncio
async def test_agents_endpoint_keeps_project_error_status(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-error",
        name="Broken Project",
        created_at=now,
        updated_at=now,
    )
    kernel.project_store.list_projects.return_value = [project]

    main_handle = kernel.registry.list_agents.return_value[0]
    project_handle = MagicMock(spec=AgentHandle)
    project_handle.agent_id = f"proj:{project.id}"
    project_handle.label = project.name
    project_handle.agent_type = "project"
    project_handle.status = AgentStatus.ERROR
    project_handle.project = project
    kernel.registry.list_agents.return_value = [main_handle, project_handle]

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents")
        assert resp.status == 200
        data = await resp.json()
        matches = [a for a in data if a["id"] == f"proj:{project.id}"]
        assert len(matches) == 1
        assert matches[0]["status"] == "error"


@pytest.mark.asyncio
async def test_agents_endpoint_includes_external_agents(kernel, adapter):
    kernel.external_agent_bridge.list_agent_payloads.return_value = [
        {
            "id": "ext:chem",
            "label": "Chem External",
            "name": "Chem External",
            "role": "External provider agent",
            "type": "external",
            "status": "idle",
            "avatar": "/assets/avatars/1.png",
        }
    ]

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents")
        assert resp.status == 200
        data = await resp.json()
        ext = next(a for a in data if a["id"] == "ext:chem")
        assert ext["type"] == "external"
        assert ext["label"] == "Chem External"
        assert ext["status"] == "idle"


@pytest.mark.asyncio
async def test_agents_endpoint_normalizes_external_avatar_fs_path(kernel, adapter):
    kernel.external_agent_bridge.list_agent_payloads.return_value = [
        {
            "id": "ext:chem",
            "label": "Chem External",
            "name": "Chem External",
            "role": "External provider agent",
            "type": "external",
            "status": "idle",
            "avatar": str(kernel.config.data_path / "assets" / "avatars" / "1.png"),
        }
    ]

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents")
        assert resp.status == 200
        data = await resp.json()
        ext = next(a for a in data if a["id"] == "ext:chem")
        assert ext["avatar"] == "/assets/avatars/1.png"


def test_normalize_avatar_for_web_expands_user_path():
    data_dir = Path("~/.drclaw").expanduser()
    avatar = "~/.drclaw/assets/avatars/1.png"
    assert _normalize_avatar_for_web(avatar, data_dir) == "/assets/avatars/1.png"


# -- POST /api/agents/{agent_id}/activate ------------------------------------


@pytest.mark.asyncio
async def test_agent_history_main_returns_persisted_session(kernel, adapter):
    manager = SessionManager(kernel.config.data_path / "sessions")
    session = manager.load("main")
    session.messages = [
        {"role": "user", "content": "hello", "source": "user"},
        {"role": "assistant", "content": "hi there", "source": "main"},
    ]
    manager.save(session)

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents/main/history")
        assert resp.status == 200
        body = await resp.json()
        assert body["agent_id"] == "main"
        assert body["messages"] == [
            {"role": "user", "text": "hello", "source": "user", "visible": True},
            {"role": "assistant", "text": "hi there", "source": "main", "visible": True},
        ]


@pytest.mark.asyncio
async def test_agent_history_includes_user_attachments(kernel, adapter):
    manager = SessionManager(kernel.config.data_path / "sessions")
    session = manager.load("main")
    session.messages = [
        {
            "role": "user",
            "content": "",
            "source": "user",
            "attachments": [
                {
                    "id": "1234567890abcdef1234567890abcdef",
                    "name": "note.txt",
                    "mime": "text/plain",
                    "size": 12,
                    "path": "/tmp/note.txt",
                    "download_url": "/api/chat/files/1234567890abcdef1234567890abcdef",
                }
            ],
        }
    ]
    manager.save(session)

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents/main/history")
        assert resp.status == 200
        body = await resp.json()
        assert len(body["messages"]) == 1
        msg = body["messages"][0]
        assert msg["role"] == "user"
        assert msg["text"] == ""
        assert isinstance(msg.get("files"), list)
        assert msg["files"][0]["name"] == "note.txt"
        assert msg["files"][0]["download_url"].startswith("/api/chat/files/")


@pytest.mark.asyncio
async def test_agent_history_legacy_attachment_path_gets_download_url(kernel, adapter):
    image_path = kernel.config.data_path / "projects" / "demo" / "workspace" / "history.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6360606060000000050001a5f645400000000049454e44ae426082"
        )
    )

    manager = SessionManager(kernel.config.data_path / "sessions")
    session = manager.load("main")
    session.messages = [
        {
            "role": "user",
            "content": "",
            "source": "user",
            "attachments": [
                {
                    "name": "history.png",
                    "mime": "image/png",
                    "size": image_path.stat().st_size,
                    "path": str(image_path),
                }
            ],
        }
    ]
    manager.save(session)

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents/main/history")
        assert resp.status == 200
        body = await resp.json()
        assert len(body["messages"]) == 1
        msg = body["messages"][0]
        assert isinstance(msg.get("files"), list)
        assert msg["files"][0]["name"] == "history.png"
        assert msg["files"][0]["download_url"].startswith("/api/chat/files/")

        download = await client.get(msg["files"][0]["download_url"])
        assert download.status == 200
        assert download.headers["Content-Type"] == "image/png"


@pytest.mark.asyncio
async def test_agent_history_project_inactive_reads_from_disk(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-history",
        name="History Project",
        created_at=now,
        updated_at=now,
    )
    kernel.project_store.get_project.return_value = project

    manager = SessionManager(kernel.config.data_path / "projects" / project.id / "sessions")
    session = manager.load(f"proj:{project.id}")
    session.messages = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "structured"}],
            "source": f"proj:{project.id}",
        },
        {"role": "tool", "content": "tool output", "source": f"proj:{project.id}"},
    ]
    manager.save(session)

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(f"/api/agents/proj:{project.id}/history")
        assert resp.status == 200
        body = await resp.json()
        assert body["agent_id"] == f"proj:{project.id}"
        assert body["messages"][0]["role"] == "assistant"
        assert body["messages"][0]["source"] == f"proj:{project.id}"
        assert body["messages"][0]["visible"] is True
        assert '"type": "text"' in body["messages"][0]["text"]
        assert body["messages"][1] == {
            "role": "tool",
            "text": "tool output",
            "source": f"proj:{project.id}",
            "visible": True,
        }


@pytest.mark.asyncio
async def test_agent_history_rejects_unsupported_agent_id(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents/equip:abc/history")
        assert resp.status == 400
        body = await resp.json()
        assert "Unsupported agent_id" in body["error"]


@pytest.mark.asyncio
async def test_agent_history_includes_assistant_tool_calls(kernel, adapter):
    manager = SessionManager(kernel.config.data_path / "sessions")
    session = manager.load("main")
    session.messages = [
        {
            "role": "assistant",
            "content": None,
            "source": "main",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "route_to_project",
                        "arguments": "{\"project\":\"alpha\",\"message\":\"run\"}",
                    },
                }
            ],
        }
    ]
    manager.save(session)

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents/main/history")
        assert resp.status == 200
        body = await resp.json()
        assert body["messages"] == [
            {
                "role": "tool_call",
                "text": 'route_to_project({"project":"alpha","message":"run"})',
                "source": "main",
                "visible": True,
            }
        ]


@pytest.mark.asyncio
async def test_agent_history_concise_mode_hides_tool_and_cross_agent_messages(kernel, adapter):
    kernel.config.daemon.verbose_chat = False
    kernel.config.daemon.show_tool_calls = True

    manager = SessionManager(kernel.config.data_path / "sessions")
    session = manager.load("main")
    session.messages = [
        {"role": "user", "content": "my question", "source": "user"},
        {"role": "user", "content": "student report", "source": "proj:demo"},
        {"role": "assistant", "content": "agent answer", "source": "main"},
        {"role": "tool", "content": "tool output", "source": "main"},
        {
            "role": "assistant",
            "content": None,
            "source": "main",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "echo", "arguments": "{\"text\":\"x\"}"},
                }
            ],
        },
    ]
    manager.save(session)

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents/main/history")
        assert resp.status == 200
        body = await resp.json()
        assert body["messages"] == [
            {"role": "user", "text": "my question", "source": "user", "visible": True},
            {"role": "assistant", "text": "agent answer", "source": "main", "visible": True},
        ]


@pytest.mark.asyncio
async def test_agent_history_project_not_found(kernel, adapter):
    kernel.project_store.get_project.return_value = None
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents/proj:nope/history")
        assert resp.status == 404
        body = await resp.json()
        assert "Project not found" in body["error"]


@pytest.mark.asyncio
async def test_agent_history_preserves_non_user_source_for_user_role(kernel, adapter):
    manager = SessionManager(kernel.config.data_path / "sessions")
    session = manager.load("main")
    session.messages = [
        {
            "role": "user",
            "content": "Student report from proj:tree-a1d720e4:\nAll done.",
            "source": "proj:tree-a1d720e4",
        }
    ]
    manager.save(session)

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents/main/history")
        assert resp.status == 200
        body = await resp.json()
        assert body["messages"] == [
            {
                "role": "user",
                "text": "Student report from proj:tree-a1d720e4:\nAll done.",
                "source": "proj:tree-a1d720e4",
                "visible": True,
            }
        ]


@pytest.mark.asyncio
async def test_agent_history_missing_source_is_reported_as_unknown(kernel, adapter):
    manager = SessionManager(kernel.config.data_path / "sessions")
    session = manager.load("main")
    session.messages = [
        {
            "role": "user",
            "content": "Student report from proj:legacy123:\nComparison finished.",
        }
    ]
    manager.save(session)

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/agents/main/history")
        assert resp.status == 200
        body = await resp.json()
        assert body["messages"][0]["source"] == "unknown"


@pytest.mark.asyncio
async def test_statistics_endpoint_returns_usage(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="stats-proj-1",
        name="Stats Project",
        created_at=now,
        updated_at=now,
    )
    kernel.project_store.list_projects.return_value = [project]
    kernel.usage_store.record(
        agent_id="main",
        input_tokens=120,
        output_tokens=30,
        input_cost_usd=0.0012,
        output_cost_usd=0.0008,
        total_cost_usd=0.002,
    )
    kernel.usage_store.record(
        agent_id=f"proj:{project.id}",
        input_tokens=50,
        output_tokens=10,
        input_cost_usd=0.0005,
        output_cost_usd=0.0002,
        total_cost_usd=0.0007,
    )

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/statistics")
        assert resp.status == 200
        data = await resp.json()
        assert isinstance(data["agents"], list)
        by_id = {row["agent_id"]: row for row in data["agents"]}
        assert by_id["main"]["agent_name"] == "虾秘"
        assert by_id["main"]["all_time"]["total_tokens"] == 150
        assert by_id["main"]["this_run"]["total_tokens"] == 150
        assert by_id[f"proj:{project.id}"]["agent_name"] == "Stats Project"
        assert data["totals"]["all_time"]["total_tokens"] == 210


@pytest.mark.asyncio
async def test_statistics_endpoint_zh_locale_uses_display_agent_name(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="stats-proj-zh",
        name="Stats Project",
        created_at=now,
        updated_at=now,
        hub_template="cat",
    )
    kernel.project_store.list_projects.return_value = [project]
    template = MagicMock()
    template.name = "cat"
    template.avatar = "/assets/avatar_cat.png"
    template.i18n = {"zh": {"name": "猫咪智能体"}}
    kernel.agent_hub_store.list.return_value = [template]

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/statistics?lang=zh")
        assert resp.status == 200
        data = await resp.json()
        by_id = {row["agent_id"]: row for row in data["agents"]}
        assert by_id["main"]["display_agent_name"] == "虾秘"
        assert by_id[f"proj:{project.id}"]["agent_name"] == "Stats Project"
        assert by_id[f"proj:{project.id}"]["display_agent_name"] == "猫咪智能体"


@pytest.mark.asyncio
async def test_statistics_endpoint_empty(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/statistics")
        assert resp.status == 200
        data = await resp.json()
        by_id = {row["agent_id"]: row for row in data["agents"]}
        assert "main" in by_id
        assert by_id["main"]["agent_name"] == "虾秘"
        assert by_id["main"]["all_time"]["total_tokens"] == 0
        assert by_id["main"]["this_run"]["total_tokens"] == 0
        assert data["totals"]["all_time"]["total_tokens"] == 0
        assert data["totals"]["this_run"]["total_tokens"] == 0


@pytest.mark.asyncio
async def test_statistics_endpoint_empty_includes_known_project(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="stats-proj-empty",
        name="No Usage Project",
        created_at=now,
        updated_at=now,
    )
    kernel.project_store.list_projects.return_value = [project]

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/statistics")
        assert resp.status == 200
        data = await resp.json()
        by_id = {row["agent_id"]: row for row in data["agents"]}
        assert "main" in by_id
        assert f"proj:{project.id}" in by_id
        assert by_id[f"proj:{project.id}"]["agent_name"] == "No Usage Project"
        assert by_id[f"proj:{project.id}"]["all_time"]["total_tokens"] == 0
        assert by_id[f"proj:{project.id}"]["this_run"]["total_tokens"] == 0


@pytest.mark.asyncio
async def test_activate_agent_endpoint_project(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-activate",
        name="Demo Activate",
        created_at=now,
        updated_at=now,
        hub_template="cat",
    )
    kernel.project_store.get_project.return_value = project
    template = MagicMock()
    template.name = "cat"
    template.avatar = "/assets/avatar_cat.png"
    kernel.agent_hub_store.list.return_value = [template]
    handle = MagicMock(spec=AgentHandle)
    handle.agent_id = f"proj:{project.id}"
    handle.label = project.name
    handle.agent_type = "project"
    handle.status = AgentStatus.RUNNING
    kernel.registry.activate_project.return_value = handle

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(f"/api/agents/proj:{project.id}/activate")
        assert resp.status == 200
        data = await resp.json()
        assert data["agent"]["id"] == f"proj:{project.id}"
        assert data["agent"]["status"] == "idle"
        assert data["agent"]["hub_template"] == "cat"
        assert data["agent"]["avatar"] == "/assets/avatar_cat.png"
        kernel.registry.activate_project.assert_called_once_with(project, interactive=True)


@pytest.mark.asyncio
async def test_activate_agent_endpoint_project_not_found(kernel, adapter):
    kernel.project_store.get_project.return_value = None
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/agents/proj:nope/activate")
        assert resp.status == 404
        data = await resp.json()
        assert "not found" in data["error"].lower()


@pytest.mark.asyncio
async def test_activate_agent_endpoint_invalid_agent_id(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/agents/bad-agent/activate")
        assert resp.status == 400
        data = await resp.json()
        assert "unsupported agent_id" in data["error"].lower()


@pytest.mark.asyncio
async def test_activate_agent_endpoint_main(kernel, adapter):
    handle = MagicMock(spec=AgentHandle)
    handle.agent_id = "main"
    handle.label = "Assistant Agent"
    handle.agent_type = "main"
    handle.status = AgentStatus.RUNNING
    kernel.registry.start_main.return_value = handle

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/agents/main/activate")
        assert resp.status == 200
        data = await resp.json()
        assert data["agent"]["id"] == "main"
        kernel.registry.start_main.assert_called_once_with()


# -- /api/agents/{agent_id}/publish-* ----------------------------------------


@pytest.mark.asyncio
async def test_publish_draft_endpoint_project(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-publish",
        name="Demo Publish",
        description="Draft description",
        goals="Goal A\nGoal B",
        tags=["tag-a", "tag-b"],
        created_at=now,
        updated_at=now,
    )
    kernel.project_store.get_project.return_value = project
    workspace = kernel.config.data_path / "projects" / project.id / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "SOUL.md").write_text("# Draft Soul\n- keep it concise\n", encoding="utf-8")

    handle = MagicMock(spec=AgentHandle)
    handle.agent_id = f"proj:{project.id}"
    handle.agent_type = "project"
    handle.status = AgentStatus.RUNNING
    kernel.registry.get.return_value = handle

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(f"/api/agents/proj:{project.id}/publish-draft")
        assert resp.status == 200
        data = await resp.json()
        assert data["agent_id"] == f"proj:{project.id}"
        assert data["template"]["name"] == "Demo Publish"
        assert data["template"]["description"] == "Draft description"
        assert data["template"]["tags"] == ["tag-a", "tag-b"]
        assert data["template"]["goals"] == ["Goal A", "Goal B"]
        assert str(data["template"]["soul"]).strip() == "# Draft Soul\n- keep it concise"
        assert data["template"]["skills"] == []


@pytest.mark.asyncio
async def test_publish_draft_endpoint_includes_merged_skills(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-skill-merge",
        name="Demo Skill Merge",
        created_at=now,
        updated_at=now,
    )
    kernel.project_store.get_project.return_value = project

    handle = MagicMock(spec=AgentHandle)
    handle.agent_id = f"proj:{project.id}"
    handle.agent_type = "project"
    handle.status = AgentStatus.RUNNING
    kernel.registry.get.return_value = handle

    global_fetch = kernel.config.data_path / "skills" / "fetch"
    global_fetch.mkdir(parents=True, exist_ok=True)
    (global_fetch / "SKILL.md").write_text("# global fetch", encoding="utf-8")

    global_shared = kernel.config.data_path / "skills" / "shared"
    global_shared.mkdir(parents=True, exist_ok=True)
    (global_shared / "SKILL.md").write_text("# global shared", encoding="utf-8")

    project_shared = (
        kernel.config.data_path
        / "projects"
        / project.id
        / "workspace"
        / "skills"
        / "shared"
    )
    project_shared.mkdir(parents=True, exist_ok=True)
    (project_shared / "SKILL.md").write_text("# project shared", encoding="utf-8")

    project_local = (
        kernel.config.data_path
        / "projects"
        / project.id
        / "workspace"
        / "skills"
        / "local-only"
    )
    project_local.mkdir(parents=True, exist_ok=True)
    (project_local / "SKILL.md").write_text("# project local", encoding="utf-8")

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(f"/api/agents/proj:{project.id}/publish-draft")
        assert resp.status == 200
        data = await resp.json()
        assert data["template"]["skills"] == [
            {"name": "fetch", "source": "global"},
            {"name": "local-only", "source": "project"},
            {"name": "shared", "source": "project"},
        ]


@pytest.mark.asyncio
async def test_publish_to_hub_endpoint_project_success(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-publish-ok",
        name="Demo Publish OK",
        description="project description",
        created_at=now,
        updated_at=now,
    )
    kernel.project_store.get_project.return_value = project

    handle = MagicMock(spec=AgentHandle)
    handle.agent_id = f"proj:{project.id}"
    handle.agent_type = "project"
    handle.status = AgentStatus.RUNNING
    kernel.registry.get.return_value = handle

    published_template = MagicMock()
    published_template.name = "Published Demo"
    published_template.description = "published desc"
    published_template.tags = ["science"]
    published_template.goals = ["goal one"]
    published_template.equipment_refs = []
    published_template.avatar = None
    skill_dir = kernel.config.data_path / "agent-hub" / "published-demo" / "skills" / "x"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# skill", encoding="utf-8")
    published_template.skills_dir = skill_dir.parent
    kernel.agent_hub_store.publish_from_project.return_value = published_template
    workspace = kernel.config.data_path / "projects" / project.id / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "SOUL.md").write_text("# Existing Soul", encoding="utf-8")

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            f"/api/agents/proj:{project.id}/publish-to-hub",
            json={
                "name": "Published Demo",
                "description": "published desc",
                "tags": ["science"],
                "goals": ["goal one"],
                "soul": "# Published Soul\n- shared persona",
            },
        )
        assert resp.status == 201
        data = await resp.json()
        assert data["template"]["name"] == "Published Demo"
        assert data["template"]["skill_count"] == 1
        assert (
            workspace / "SOUL.md"
        ).read_text(encoding="utf-8") == "# Published Soul\n- shared persona"
        kernel.agent_hub_store.publish_from_project.assert_called_once_with(
            project,
            kernel.config.data_path,
            template_name="Published Demo",
            description="published desc",
            tags=["science"],
            goals=["goal one"],
            equipment_refs=[],
        )


@pytest.mark.asyncio
async def test_publish_to_hub_endpoint_rejects_main(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/agents/main/publish-to-hub", json={})
        assert resp.status == 400
        data = await resp.json()
        assert "cannot be published" in data["error"].lower()


@pytest.mark.asyncio
async def test_publish_to_hub_endpoint_conflict(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-publish-conflict",
        name="Demo Publish Conflict",
        created_at=now,
        updated_at=now,
    )
    kernel.project_store.get_project.return_value = project

    handle = MagicMock(spec=AgentHandle)
    handle.agent_id = f"proj:{project.id}"
    handle.agent_type = "project"
    handle.status = AgentStatus.RUNNING
    kernel.registry.get.return_value = handle
    kernel.agent_hub_store.publish_from_project.side_effect = ValueError("already exists")

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(f"/api/agents/proj:{project.id}/publish-to-hub", json={})
        assert resp.status == 409
        data = await resp.json()
        assert "already exists" in data["error"].lower()


@pytest.mark.asyncio
async def test_publish_to_hub_endpoint_requires_active_project(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-publish-inactive",
        name="Demo Publish Inactive",
        created_at=now,
        updated_at=now,
    )
    kernel.project_store.get_project.return_value = project
    kernel.registry.get.return_value = None

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(f"/api/agents/proj:{project.id}/publish-to-hub", json={})
        assert resp.status == 409
        data = await resp.json()
        assert "active project agents" in data["error"].lower()


# -- /api/config --------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_get_endpoint(kernel, adapter):
    kernel.config.providers[kernel.config.active_provider].reasoning_effort = "medium"
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/config")
        assert resp.status == 200
        data = await resp.json()
        assert data["providers"][kernel.config.active_provider]["model"] == (
            kernel.config.active_provider_config.model
        )
        assert data["active_provider"] == kernel.config.active_provider
        assert data["providers"][kernel.config.active_provider]["reasoning_effort"] == "medium"
        assert data["agent"]["max_iterations"] == kernel.config.agent.max_iterations
        assert data["daemon"]["web_in_docker"] is False


@pytest.mark.asyncio
async def test_config_put_endpoint_persists(kernel, adapter):
    app = _make_app(kernel, adapter)
    payload = kernel.config.model_dump(by_alias=True)
    payload["providers"][payload["active_provider"]]["model"] = "openai/gpt-4.1-mini"
    payload["providers"][payload["active_provider"]]["reasoning_effort"] = "high"
    payload["daemon"]["verbose_chat"] = False
    payload["daemon"]["show_tool_calls"] = False
    payload["daemon"]["web_in_docker"] = True

    async with TestClient(TestServer(app)) as client:
        resp = await client.put("/api/config", json=payload)
        assert resp.status == 200
        data = await resp.json()
        assert data["restart_required"] is True
        assert data["config"]["providers"][data["config"]["active_provider"]]["model"] == (
            "openai/gpt-4.1-mini"
        )
        assert data["config"]["providers"][data["config"]["active_provider"]]["reasoning_effort"] == (
            "high"
        )
        assert kernel.config.active_provider_config.model == "openai/gpt-4.1-mini"
        assert kernel.config.active_provider_config.reasoning_effort == "high"
        assert kernel.config.daemon.verbose_chat is False
        assert kernel.config.daemon.show_tool_calls is False
        assert kernel.config.daemon.web_in_docker is True

    config_path = kernel.config.data_path / "config.json"
    loaded = load_config(config_path)
    assert loaded.active_provider_config.model == "openai/gpt-4.1-mini"
    assert loaded.active_provider_config.reasoning_effort == "high"
    assert loaded.daemon.verbose_chat is False
    assert loaded.daemon.show_tool_calls is False
    assert loaded.daemon.web_in_docker is True


@pytest.mark.asyncio
async def test_config_put_endpoint_validation_error(kernel, adapter):
    app = _make_app(kernel, adapter)
    payload = kernel.config.model_dump(by_alias=True)
    payload["agent"]["max_iterations"] = "bad-value"

    async with TestClient(TestServer(app)) as client:
        resp = await client.put("/api/config", json=payload)
        assert resp.status == 400
        data = await resp.json()
        assert "Invalid configuration" in data["error"]
        assert "details" in data


# -- /api/skills --------------------------------------------------------------


@pytest.mark.asyncio
async def test_skills_list_and_get_content(kernel, adapter):
    skills_dir = kernel.config.data_path / "skills" / "reader"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\ndescription: Read docs\n---\n# Reader",
        encoding="utf-8",
    )

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        list_resp = await client.get("/api/skills")
        assert list_resp.status == 200
        skills = (await list_resp.json())["skills"]
        names = {s["name"] for s in skills}
        assert "reader" in names

        reader_summary = next(
            s for s in skills if s["name"] == "reader" and s["source"] == "global"
        )
        assert reader_summary["id"] == "global:reader"
        assert reader_summary["source_label"] == "global"
        assert reader_summary["used_by_agent"] is False
        assert reader_summary["used_by_agents"] == []
        assert "path" not in reader_summary
        assert "owner_id" not in reader_summary
        assert "owner_name" not in reader_summary

        detail_resp = await client.get(f"/api/skills/{reader_summary['id']}")
        assert detail_resp.status == 200
        detail = await detail_resp.json()
        assert detail["name"] == "reader"
        assert detail["id"] == "global:reader"
        assert detail["used_by_agent"] is False
        assert detail["used_by_agents"] == []
        assert "# Reader" in detail["content"]
        assert "path" not in detail
        assert "owner_id" not in detail
        assert "owner_name" not in detail


@pytest.mark.asyncio
async def test_skills_list_and_get_content_zh_locale(kernel, adapter):
    skills_dir = kernel.config.data_path / "skills" / "reader"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        (
            "---\n"
            "name: reader\n"
            "description: Read docs\n"
            "i18n:\n"
            "  zh:\n"
            "    name: 阅读器\n"
            "    description: 阅读文档\n"
            "---\n"
            "# Reader"
        ),
        encoding="utf-8",
    )

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        list_resp = await client.get("/api/skills?lang=zh")
        assert list_resp.status == 200
        skills = (await list_resp.json())["skills"]
        reader_summary = next(
            s for s in skills if s["name"] == "reader" and s["source"] == "global"
        )
        assert reader_summary["display_name"] == "阅读器"
        assert reader_summary["display_description"] == "阅读文档"
        assert reader_summary["source_label"] == "全局"

        detail_resp = await client.get(f"/api/skills/{reader_summary['id']}?lang=zh")
        assert detail_resp.status == 200
        detail = await detail_resp.json()
        assert detail["name"] == "reader"
        assert detail["display_name"] == "阅读器"
        assert detail["display_description"] == "阅读文档"


@pytest.mark.asyncio
async def test_skill_get_missing_returns_404(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/skills/missing")
        assert resp.status == 404
        body = await resp.json()
        assert "Skill not found" in body["error"]


@pytest.mark.asyncio
async def test_skill_upload_accepts_single_root_skill(kernel, adapter):
    app = _make_app(kernel, adapter)
    archive = _zip_bytes({
        "SKILL.md": "---\ndescription: Uploaded skill\n---\n# Uploaded",
        "scripts/run.sh": "echo hi",
    })
    form = FormData()
    form.add_field("file", archive, filename="uploaded-skill.zip", content_type="application/zip")

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/skills/upload", data=form)
        assert resp.status == 201
        body = await resp.json()
        assert body["skill"]["name"] == "uploaded-skill"
        assert body["skill"]["id"] == "global:uploaded-skill"
        assert body["skill"]["source"] == "global"

    skill_file = kernel.config.data_path / "skills" / "uploaded-skill" / "SKILL.md"
    assert skill_file.is_file()
    assert "# Uploaded" in skill_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_skill_upload_accepts_single_top_level_dir(kernel, adapter):
    app = _make_app(kernel, adapter)
    archive = _zip_bytes({
        "__MACOSX/._junk": "",
        "data-reader/SKILL.md": "# Data Reader",
    })
    form = FormData()
    form.add_field("file", archive, filename="data-reader.zip", content_type="application/zip")

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/skills/upload", data=form)
        assert resp.status == 201
        body = await resp.json()
        assert body["skill"]["name"] == "data-reader"


@pytest.mark.asyncio
async def test_skill_upload_conflict_returns_409(kernel, adapter):
    existing = kernel.config.data_path / "skills" / "uploaded-skill"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("# Existing", encoding="utf-8")

    app = _make_app(kernel, adapter)
    archive = _zip_bytes({"SKILL.md": "# New"})
    form = FormData()
    form.add_field("file", archive, filename="uploaded-skill.zip", content_type="application/zip")

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/skills/upload", data=form)
        assert resp.status == 409
        body = await resp.json()
        assert "already exists" in body["error"]


@pytest.mark.asyncio
async def test_skill_upload_rejects_invalid_shape(kernel, adapter):
    app = _make_app(kernel, adapter)
    archive = _zip_bytes({"notes/readme.md": "not a skill"})
    form = FormData()
    form.add_field("file", archive, filename="bad-skill.zip", content_type="application/zip")

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/skills/upload", data=form)
        assert resp.status == 400
        body = await resp.json()
        assert "SKILL.md" in body["error"]


@pytest.mark.asyncio
async def test_skill_upload_rejects_path_traversal(kernel, adapter):
    app = _make_app(kernel, adapter)
    archive = _zip_bytes({
        "../evil.txt": "nope",
        "SKILL.md": "# Evil",
    })
    form = FormData()
    form.add_field("file", archive, filename="evil-skill.zip", content_type="application/zip")

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/skills/upload", data=form)
        assert resp.status == 400
        body = await resp.json()
        assert "unsafe path traversal" in body["error"]


@pytest.mark.asyncio
async def test_skills_list_includes_project_and_equipment_skill_roots(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-1",
        name="Demo Project",
        created_at=now,
        updated_at=now,
    )
    kernel.project_store.list_projects.return_value = [project]

    project_skill_dir = (
        kernel.config.data_path / "projects" / project.id / "workspace" / "skills" / "proj-skill"
    )
    project_skill_dir.mkdir(parents=True)
    (project_skill_dir / "SKILL.md").write_text("# Project Skill", encoding="utf-8")

    equipment_root = kernel.config.data_path / "equipments" / "analyzer"
    equipment_skills = equipment_root / "skills"
    equipment_skill_dir = equipment_skills / "equip-skill"
    equipment_skill_dir.mkdir(parents=True)
    (equipment_skill_dir / "SKILL.md").write_text("# Equipment Skill", encoding="utf-8")
    kernel.equipment_manager.prototype_store.list.return_value = [
        EquipmentPrototype(name="analyzer", root_dir=equipment_root, skills_dir=equipment_skills)
    ]

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        list_resp = await client.get("/api/skills")
        assert list_resp.status == 200
        skills = (await list_resp.json())["skills"]
        refs = {s["id"] for s in skills}
        assert "agent:proj-skill" in refs
        assert "agent:equip-skill" in refs

        proj_detail = await client.get("/api/skills/agent:proj-skill")
        assert proj_detail.status == 200
        proj_body = await proj_detail.json()
        assert proj_body["source"] == "agent"
        assert proj_body["source_label"] == "Agent"
        assert proj_body["used_by_agent"] is True
        assert proj_body["used_by_agents"] == ["Demo Project"]
        assert "# Project Skill" in proj_body["content"]

        # Backward-compat for old source-scoped refs.
        proj_legacy = await client.get("/api/skills/project:demo-proj-1:proj-skill")
        assert proj_legacy.status == 200
        proj_legacy_body = await proj_legacy.json()
        assert proj_legacy_body["id"] == "agent:proj-skill"

        equip_detail = await client.get("/api/skills/agent:equip-skill")
        assert equip_detail.status == 200
        equip_body = await equip_detail.json()
        assert equip_body["source"] == "agent"
        assert equip_body["source_label"] == "Agent"
        assert equip_body["used_by_agent"] is True
        assert equip_body["used_by_agents"] == ["analyzer"]
        assert "# Equipment Skill" in equip_body["content"]


@pytest.mark.asyncio
async def test_skills_dedupe_prefers_global_over_agent(kernel, adapter):
    global_skill = kernel.config.data_path / "skills" / "shared"
    global_skill.mkdir(parents=True)
    (global_skill / "SKILL.md").write_text("# Global", encoding="utf-8")

    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-2",
        name="Demo Project 2",
        created_at=now,
        updated_at=now,
    )
    kernel.project_store.list_projects.return_value = [project]
    project_skill = (
        kernel.config.data_path / "projects" / project.id / "workspace" / "skills" / "shared"
    )
    project_skill.mkdir(parents=True)
    (project_skill / "SKILL.md").write_text("# Project Copy", encoding="utf-8")

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        list_resp = await client.get("/api/skills")
        skills = (await list_resp.json())["skills"]
        shared = [s for s in skills if s["name"] == "shared"]
        assert len(shared) == 1
        assert shared[0]["source"] == "global"
        assert shared[0]["id"] == "global:shared"
        assert shared[0]["used_by_agent"] is True
        assert shared[0]["used_by_agents"] == ["Demo Project 2"]

        detail_resp = await client.get("/api/skills/global:shared")
        assert detail_resp.status == 200
        detail = await detail_resp.json()
        assert detail["source"] == "global"
        assert detail["used_by_agent"] is True
        assert detail["used_by_agents"] == ["Demo Project 2"]
        assert "# Global" in detail["content"]
        assert "warning" not in detail


@pytest.mark.asyncio
async def test_skills_dedupe_prefers_hub_over_agent(kernel, adapter):
    hub_skill_dir = kernel.config.data_path / "local-skill-hub" / "science" / "shared"
    hub_skill_dir.mkdir(parents=True)
    (hub_skill_dir / "SKILL.md").write_text("# Hub Copy", encoding="utf-8")

    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-3",
        name="Demo Project 3",
        created_at=now,
        updated_at=now,
    )
    kernel.project_store.list_projects.return_value = [project]
    project_skill = (
        kernel.config.data_path / "projects" / project.id / "workspace" / "skills" / "shared"
    )
    project_skill.mkdir(parents=True)
    (project_skill / "SKILL.md").write_text("# Project Copy", encoding="utf-8")

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        list_resp = await client.get("/api/skills")
        skills = (await list_resp.json())["skills"]
        shared = [s for s in skills if s["name"] == "shared"]
        assert len(shared) == 1
        assert shared[0]["source"] == "hub"
        assert shared[0]["id"] == "hub:shared"
        assert shared[0]["used_by_agent"] is True
        assert shared[0]["used_by_agents"] == ["Demo Project 3"]

        detail_resp = await client.get("/api/skills/hub:shared")
        assert detail_resp.status == 200
        detail = await detail_resp.json()
        assert detail["source"] == "hub"
        assert detail["used_by_agent"] is True
        assert detail["used_by_agents"] == ["Demo Project 3"]
        assert "# Hub Copy" in detail["content"]
        assert "warning" not in detail


@pytest.mark.asyncio
async def test_skills_dedupe_agent_variants_are_deterministic_with_warning(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-4",
        name="Demo Project 4",
        created_at=now,
        updated_at=now,
    )
    kernel.project_store.list_projects.return_value = [project]

    project_skill_dir = (
        kernel.config.data_path / "projects" / project.id / "workspace" / "skills" / "shared"
    )
    project_skill_dir.mkdir(parents=True)
    project_skill_file = project_skill_dir / "SKILL.md"
    project_skill_file.write_text("# Project Variant", encoding="utf-8")

    equipment_root = kernel.config.data_path / "equipments" / "analyzer"
    equipment_skills = equipment_root / "skills"
    equipment_skill_dir = equipment_skills / "shared"
    equipment_skill_dir.mkdir(parents=True)
    equipment_skill_file = equipment_skill_dir / "SKILL.md"
    equipment_skill_file.write_text("# Equipment Variant", encoding="utf-8")
    kernel.equipment_manager.prototype_store.list.return_value = [
        EquipmentPrototype(name="analyzer", root_dir=equipment_root, skills_dir=equipment_skills)
    ]

    expected_winner = min(
        [
            str(project_skill_file),
            str(equipment_skill_file),
        ]
    )

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        list_resp = await client.get("/api/skills")
        skills = (await list_resp.json())["skills"]
        shared = [s for s in skills if s["name"] == "shared"]
        assert len(shared) == 1
        assert shared[0]["source"] == "agent"
        assert shared[0]["id"] == "agent:shared"
        assert shared[0]["used_by_agent"] is True
        assert shared[0]["used_by_agents"] == ["Demo Project 4", "analyzer"]

        detail_resp = await client.get("/api/skills/agent:shared")
        assert detail_resp.status == 200
        detail = await detail_resp.json()
        assert detail["source"] == "agent"
        assert detail["source_label"] == "Agent"
        assert detail["used_by_agent"] is True
        assert detail["used_by_agents"] == ["Demo Project 4", "analyzer"]
        assert detail["warning"] == (
            "Multiple agent copies exist and may differ; showing one deterministic copy."
        )
        if expected_winner == str(project_skill_file):
            assert "# Project Variant" in detail["content"]
        else:
            assert "# Equipment Variant" in detail["content"]


@pytest.mark.asyncio
async def test_hub_endpoints_zh_locale_return_display_fields(kernel, adapter):
    template_root = kernel.config.data_path / "agent-hub" / "cat"
    (template_root / "skills" / "kitten").mkdir(parents=True, exist_ok=True)
    (template_root / "SOUL.md").write_text("# Cat Soul", encoding="utf-8")
    (template_root / "skills" / "kitten" / "SKILL.md").write_text(
        (
            "---\n"
            "name: kitten\n"
            "description: Cat helper\n"
            "i18n:\n"
            "  zh:\n"
            "    name: 猫助手\n"
            "    description: 猫咪辅助技能\n"
            "---\n"
            "# Kitten"
        ),
        encoding="utf-8",
    )

    template = MagicMock()
    template.name = "cat"
    template.description = "Cat template"
    template.tags = []
    template.goals = []
    template.equipment_refs = []
    template.avatar = "/assets/avatar_cat.png"
    template.root_dir = template_root
    template.skills_dir = template_root / "skills"
    template.i18n = {
        "zh": {
            "name": "猫咪模板",
            "description": "猫咪智能体模板",
        }
    }
    kernel.agent_hub_store.list.return_value = [template]
    kernel.agent_hub_store.get.return_value = template
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-cat-1",
        name="Cat Project",
        created_at=now,
        updated_at=now,
        hub_template="cat",
    )
    kernel.project_store.list_projects.return_value = [project]

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        list_resp = await client.get("/api/agent-hub?lang=zh")
        assert list_resp.status == 200
        templates = (await list_resp.json())["templates"]
        item = next(t for t in templates if t["name"] == "cat")
        assert item["display_name"] == "猫咪模板"
        assert item["display_description"] == "猫咪智能体模板"

        detail_resp = await client.get("/api/agent-hub/cat?lang=zh")
        assert detail_resp.status == 200
        detail = await detail_resp.json()
        assert detail["display_name"] == "猫咪模板"
        assert detail["display_description"] == "猫咪智能体模板"
        assert detail["skills"][0]["display_name"] == "猫助手"
        assert detail["skills"][0]["display_description"] == "猫咪辅助技能"
        assert detail["activated_projects"][0]["display_name"] == "猫咪模板"


@pytest.mark.asyncio
async def test_hub_endpoints_normalize_avatar_fs_path(kernel, adapter):
    template_root = kernel.config.data_path / "agent-hub" / "cat"
    (template_root / "skills").mkdir(parents=True, exist_ok=True)
    template = MagicMock()
    template.name = "cat"
    template.description = "Cat template"
    template.tags = []
    template.goals = []
    template.equipment_refs = []
    template.avatar = str(kernel.config.data_path / "assets" / "avatars" / "11.png")
    template.root_dir = template_root
    template.skills_dir = template_root / "skills"
    template.i18n = {}
    kernel.agent_hub_store.list.return_value = [template]
    kernel.agent_hub_store.get.return_value = template
    kernel.project_store.list_projects.return_value = []

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        list_resp = await client.get("/api/agent-hub")
        assert list_resp.status == 200
        templates = (await list_resp.json())["templates"]
        item = next(t for t in templates if t["name"] == "cat")
        assert item["avatar"] == "/assets/avatars/11.png"

        detail_resp = await client.get("/api/agent-hub/cat")
        assert detail_resp.status == 200
        detail = await detail_resp.json()
        assert detail["avatar"] == "/assets/avatars/11.png"


# -- WebSocket round-trip ------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_chat_publishes_inbound(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/ws")

        await ws.send_json({
            "type": "chat",
            "agent_id": "main",
            "text": "hello",
            "metadata": {"webui_language": "zh"},
        })

        # Verify message was published to bus
        msg = await kernel.bus.consume_inbound("main")
        assert msg.channel == "web"
        assert msg.text == "hello"
        assert msg.metadata.get("webui_language") == "zh"

        await ws.close()


@pytest.mark.asyncio
async def test_ws_chat_to_main_includes_active_agents_metadata(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    main_handle = kernel.registry.get.return_value
    project = Project(
        id="demo-proj-active-list",
        name="Cat",
        created_at=now,
        updated_at=now,
    )
    project_handle = MagicMock(spec=AgentHandle)
    project_handle.agent_id = f"proj:{project.id}"
    project_handle.label = project.name
    project_handle.agent_type = "project"
    project_handle.project = project
    project_handle.status = AgentStatus.RUNNING
    kernel.registry.list_agents.return_value = [main_handle, project_handle]

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/ws")

        await ws.send_json({
            "type": "chat",
            "agent_id": "main",
            "text": "hello",
        })

        msg = await kernel.bus.consume_inbound("main")
        active_agents = msg.metadata.get("active_agents")
        assert isinstance(active_agents, list)
        assert active_agents == [
            {
                "id": f"proj:{project.id}",
                "name": project.name,
                "role": "project",
            }
        ]

        await ws.close()


@pytest.mark.asyncio
async def test_ws_chat_publishes_uploaded_file_attachments(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        upload_form = FormData()
        upload_form.add_field(
            "files",
            b"alpha",
            filename="alpha.txt",
            content_type="text/plain",
        )
        upload_resp = await client.post("/api/chat/files/upload", data=upload_form)
        assert upload_resp.status == 201
        upload_body = await upload_resp.json()
        uploaded = upload_body["files"][0]

        ws = await client.ws_connect("/ws")
        await ws.send_json(
            {
                "type": "chat",
                "agent_id": "main",
                "text": "",
                "files": [{"id": uploaded["id"]}],
            }
        )

        msg = await kernel.bus.consume_inbound("main")
        assert msg.text == ""
        assert isinstance(msg.metadata.get("attachments"), list)
        attachment = msg.metadata["attachments"][0]
        assert attachment["id"] == uploaded["id"]
        assert attachment["name"] == "alpha.txt"
        assert attachment["download_url"].startswith("/api/chat/files/")
        assert Path(attachment["path"]).is_file()

        await ws.close()


@pytest.mark.asyncio
async def test_ws_chat_rejects_unknown_file_id(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/ws")
        await ws.send_json(
            {
                "type": "chat",
                "agent_id": "main",
                "text": "",
                "files": [{"id": "1234567890abcdef1234567890abcdef"}],
            }
        )
        resp = await ws.receive_json()
        assert resp["type"] == "error"
        assert "Unknown file id" in resp["text"]
        await ws.close()


@pytest.mark.asyncio
async def test_ws_chat_auto_activates_project_agent(kernel, adapter):
    now = datetime.now(tz=timezone.utc)
    project = Project(
        id="demo-proj-ws-auto",
        name="WS Auto Activate",
        created_at=now,
        updated_at=now,
    )
    inactive_handle = MagicMock(spec=AgentHandle)
    inactive_handle.agent_id = f"proj:{project.id}"
    inactive_handle.label = project.name
    inactive_handle.agent_type = "project"
    inactive_handle.status = AgentStatus.DONE
    active_handle = MagicMock(spec=AgentHandle)
    active_handle.agent_id = f"proj:{project.id}"
    active_handle.label = project.name
    active_handle.agent_type = "project"
    active_handle.status = AgentStatus.RUNNING

    def registry_get(agent_id: str):
        if agent_id == f"proj:{project.id}":
            return inactive_handle
        return None

    kernel.registry.get.side_effect = registry_get
    kernel.project_store.get_project.return_value = project
    kernel.registry.activate_project.return_value = active_handle

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/ws")

        await ws.send_json({
            "type": "chat",
            "agent_id": f"proj:{project.id}",
            "text": "hello project",
        })

        msg = await kernel.bus.consume_inbound(f"proj:{project.id}")
        assert msg.channel == "web"
        assert msg.text == "hello project"
        kernel.registry.activate_project.assert_called_once_with(project, interactive=True)

        await ws.close()


@pytest.mark.asyncio
async def test_ws_chat_external_agent_submits_to_bridge(kernel, adapter):
    kernel.external_agent_bridge.is_external_agent.return_value = True
    kernel.external_agent_bridge.submit_from_web = AsyncMock(return_value="req1")

    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/ws")
        await ws.send_json({"type": "chat", "agent_id": "ext:chem", "text": "hello external"})

        await asyncio.sleep(0.05)
        kernel.external_agent_bridge.submit_from_web.assert_awaited_once()
        kernel.registry.activate_project.assert_not_called()
        await ws.close()


@pytest.mark.asyncio
async def test_external_callback_endpoint_forwards_to_bridge(kernel, adapter):
    kernel.external_agent_bridge.handle_callback = AsyncMock(
        return_value=(202, {"ok": True, "request_id": "r1"})
    )
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/external/callback",
            json={
                "request_id": "r1",
                "agent_id": "ext:chem",
                "text": "done",
            },
        )
        assert resp.status == 202
        body = await resp.json()
        assert body["ok"] is True
        kernel.external_agent_bridge.handle_callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_ws_invalid_json(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/ws")
        await ws.send_str("not json")
        resp = await ws.receive_json()
        assert resp["type"] == "error"
        assert "Invalid JSON" in resp["text"]
        await ws.close()


@pytest.mark.asyncio
async def test_ws_missing_fields(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/ws")
        await ws.send_json({"type": "chat"})
        resp = await ws.receive_json()
        assert resp["type"] == "error"
        assert "Missing" in resp["text"]
        await ws.close()


@pytest.mark.asyncio
async def test_ws_unknown_agent(kernel, adapter):
    kernel.registry.get.return_value = None
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/ws")
        await ws.send_json({"type": "chat", "agent_id": "nonexistent", "text": "hi"})
        resp = await ws.receive_json()
        assert resp["type"] == "error"
        assert "not found" in resp["text"]
        await ws.close()


@pytest.mark.asyncio
async def test_ws_disconnect_cleans_up(kernel, adapter):
    app = _make_app(kernel, adapter)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/ws")
        # At least one connection should exist
        assert len(adapter._connections) == 1
        await ws.close()
        # Give the handler a moment to clean up
        await asyncio.sleep(0.05)
        assert len(adapter._connections) == 0
