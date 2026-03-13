"""Tests for web tools (Serper search + URL fetch extraction)."""

from __future__ import annotations

import json

import pytest

import drclaw.tools.web as web_tools
from drclaw.tools.web import WebFetchTool, WebSearchTool


@pytest.mark.asyncio
async def test_web_search_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    tool = WebSearchTool(api_key="")
    result = await tool.execute({"query": "drclaw"})
    assert result.startswith("Error: Serper API key not configured")


@pytest.mark.asyncio
async def test_web_search_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_post_json(**kwargs):
        assert kwargs["headers"]["X-API-KEY"] == "env-serper-key"
        assert kwargs["payload"]["q"] == "best llm frameworks"
        assert kwargs["payload"]["num"] == 2
        return 200, {
            "answerBox": {"answer": "Use tools when needed.", "link": "https://example.com/answer"},
            "organic": [
                {"title": "First", "link": "https://example.com/1", "snippet": "One"},
                {"title": "Second", "link": "https://example.com/2", "snippet": "Two"},
            ],
        }, ""

    monkeypatch.setenv("SERPER_API_KEY", "env-serper-key")
    monkeypatch.setattr(web_tools, "_http_post_json", _fake_post_json)
    tool = WebSearchTool(api_key=None, max_results=5)
    result = await tool.execute({"query": "best llm frameworks", "count": 2})
    assert "Results for: best llm frameworks" in result
    assert "Answer: Use tools when needed." in result
    assert "1. First" in result
    assert "2. Second" in result


@pytest.mark.asyncio
async def test_web_search_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_post_json(**kwargs):
        return 503, None, "upstream unavailable"

    monkeypatch.setattr(web_tools, "_http_post_json", _fake_post_json)
    tool = WebSearchTool(api_key="k")
    result = await tool.execute({"query": "q"})
    assert result.startswith("Error: Serper request failed with HTTP 503")


@pytest.mark.asyncio
async def test_web_search_prefers_scoped_env_over_init_key(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_post_json(**kwargs):
        assert kwargs["headers"]["X-API-KEY"] == "scoped-key"
        return 200, {"organic": []}, ""

    monkeypatch.setattr(web_tools, "_http_post_json", _fake_post_json)
    tool = WebSearchTool(
        api_key="configured-key",
        env_provider=lambda: {"SERPER_API_KEY": "scoped-key"},
    )
    result = await tool.execute({"query": "q"})
    assert "No organic results." in result


@pytest.mark.asyncio
async def test_web_fetch_rejects_invalid_url() -> None:
    tool = WebFetchTool()
    result = await tool.execute({"url": "file:///etc/passwd"})
    assert result.startswith("Error: URL validation failed")


@pytest.mark.asyncio
async def test_web_fetch_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_get_text(**kwargs):
        return 200, "https://api.example.com/data", {"content-type": "application/json"}, '{"x": 1}'

    monkeypatch.setattr(web_tools, "_http_get_text", _fake_get_text)
    tool = WebFetchTool()
    result = await tool.execute({"url": "https://api.example.com/data"})
    payload = json.loads(result)
    assert payload["extractor"] == "json"
    assert payload["status"] == 200
    assert '"x": 1' in payload["text"]


@pytest.mark.asyncio
async def test_web_fetch_html_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    html = (
        "<html><head><title>Demo</title></head><body>"
        "<h1>Heading</h1><p>Hello <a href='https://example.com'>world</a>.</p></body></html>"
    )

    async def _fake_get_text(**kwargs):
        return 200, "https://example.com", {"content-type": "text/html; charset=utf-8"}, html

    monkeypatch.setattr(web_tools, "_http_get_text", _fake_get_text)
    tool = WebFetchTool()
    result = await tool.execute({"url": "https://example.com", "extractMode": "markdown"})
    payload = json.loads(result)
    assert payload["extractor"] == "html-markdown"
    assert payload["text"].startswith("# Demo")
    assert "Heading" in payload["text"]


@pytest.mark.asyncio
async def test_web_fetch_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_get_text(**kwargs):
        return 200, "https://example.com/txt", {"content-type": "text/plain"}, "x" * 200

    monkeypatch.setattr(web_tools, "_http_get_text", _fake_get_text)
    tool = WebFetchTool(max_chars=500)
    result = await tool.execute({"url": "https://example.com/txt", "maxChars": 120})
    payload = json.loads(result)
    assert payload["truncated"] is True
    assert payload["length"] == 120
