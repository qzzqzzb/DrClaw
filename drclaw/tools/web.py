"""Web tools: Serper-backed search and generic URL fetch/extract."""

from __future__ import annotations

import html
import json
import os
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlparse

import aiohttp

from drclaw.tools.base import Tool

_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
_DEFAULT_SERPER_ENDPOINT = "https://google.serper.dev/search"
_MAX_REDIRECTS = 5


def _normalize(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _strip_tags(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _html_to_markdown(raw_html: str) -> str:
    text = re.sub(
        r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
        lambda m: f"[{_strip_tags(m[2])}]({m[1]})",
        raw_html,
        flags=re.I,
    )
    text = re.sub(
        r"<h([1-6])[^>]*>([\s\S]*?)</h\1>",
        lambda m: f'\n{"#" * int(m[1])} {_strip_tags(m[2])}\n',
        text,
        flags=re.I,
    )
    text = re.sub(
        r"<li[^>]*>([\s\S]*?)</li>",
        lambda m: f"\n- {_strip_tags(m[1])}",
        text,
        flags=re.I,
    )
    text = re.sub(r"</(p|div|section|article|tr)>", "\n\n", text, flags=re.I)
    text = re.sub(r"<(br|hr)\s*/?>", "\n", text, flags=re.I)
    return _normalize(_strip_tags(text))


def _validate_url(url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"Only http/https allowed, got '{parsed.scheme or 'none'}'"
        if not parsed.netloc:
            return False, "Missing domain"
        return True, ""
    except Exception as exc:  # pragma: no cover - defensive
        return False, str(exc)


def _extract_html_title(raw_html: str) -> str:
    match = re.search(r"<title[^>]*>([\s\S]*?)</title>", raw_html, flags=re.I)
    if not match:
        return ""
    return _normalize(_strip_tags(match.group(1)))


async def _http_post_json(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: float,
) -> tuple[int, dict[str, Any] | None, str]:
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=headers) as response:
            raw = await response.text(errors="replace")
            parsed: dict[str, Any] | None = None
            try:
                maybe_json = json.loads(raw)
                if isinstance(maybe_json, dict):
                    parsed = maybe_json
            except json.JSONDecodeError:
                parsed = None
            return response.status, parsed, raw


async def _http_get_text(
    *,
    url: str,
    headers: dict[str, str],
    timeout_seconds: float,
    max_redirects: int,
) -> tuple[int, str, dict[str, str], str]:
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            url,
            headers=headers,
            allow_redirects=True,
            max_redirects=max_redirects,
        ) as response:
            body = await response.text(errors="replace")
            return response.status, str(response.url), dict(response.headers), body


class WebSearchTool(Tool):
    """Search the web via Serper API."""

    def __init__(
        self,
        api_key: str | None = None,
        max_results: int = 5,
        endpoint: str = _DEFAULT_SERPER_ENDPOINT,
        env_provider: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self._init_api_key = api_key
        self.max_results = max(1, min(max_results, 10))
        self.endpoint = endpoint.strip() or _DEFAULT_SERPER_ENDPOINT
        self._env_provider = env_provider

    @property
    def api_key(self) -> str:
        if self._env_provider is not None:
            scoped = self._env_provider().get("SERPER_API_KEY", "").strip()
            if scoped:
                return scoped
        if self._init_api_key:
            return self._init_api_key.strip()
        return os.environ.get("SERPER_API_KEY", "").strip()

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web using Serper. Returns titles, URLs, and snippets."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "count": {
                    "type": "integer",
                    "description": "Number of results to return (1-10)",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        query: str = params["query"]
        count: int = max(1, min(int(params.get("count", self.max_results)), 10))

        if not self.api_key:
            return (
                "Error: Serper API key not configured. "
                "Set tools.web.serper.api_key in ~/.drclaw/config.json "
                "or export SERPER_API_KEY."
            )

        try:
            status, data, raw = await _http_post_json(
                url=self.endpoint,
                headers={
                    "X-API-KEY": self.api_key,
                    "Content-Type": "application/json",
                },
                payload={"q": query, "num": count},
                timeout_seconds=15.0,
            )
        except Exception as exc:
            return f"Error: Serper request failed: {exc}"

        if status >= 400:
            return f"Error: Serper request failed with HTTP {status}: {raw[:500]}"
        if data is None:
            return "Error: Serper response is not valid JSON."

        organic = data.get("organic", [])
        lines = [f"Results for: {query}", ""]

        answer_box = data.get("answerBox")
        if isinstance(answer_box, dict):
            answer = answer_box.get("answer") or answer_box.get("snippet")
            if answer:
                lines.append(f"Answer: {answer}")
                link = answer_box.get("link")
                if link:
                    lines.append(f"Source: {link}")
                lines.append("")

        if not organic:
            lines.append("No organic results.")
            return "\n".join(lines)

        for i, item in enumerate(organic[:count], 1):
            title = item.get("title", "")
            link = item.get("link", "")
            snippet = item.get("snippet", "")
            lines.append(f"{i}. {title}")
            lines.append(f"   {link}")
            if snippet:
                lines.append(f"   {snippet}")

        return "\n".join(lines)


class WebFetchTool(Tool):
    """Fetch URL content and extract readable text/markdown."""

    def __init__(self, max_chars: int = 50_000) -> None:
        self.max_chars = max_chars

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return "Fetch URL and extract readable content (HTML -> markdown/text)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "extractMode": {
                    "type": "string",
                    "enum": ["markdown", "text"],
                    "description": "Output mode for HTML content",
                },
                "maxChars": {
                    "type": "integer",
                    "minimum": 100,
                    "description": "Maximum characters in returned text",
                },
                "extract_mode": {
                    "type": "string",
                    "enum": ["markdown", "text"],
                    "description": "Alias of extractMode",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 100,
                    "description": "Alias of maxChars",
                },
            },
            "required": ["url"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        url: str = params["url"]
        extract_mode = params.get("extractMode") or params.get("extract_mode") or "markdown"
        max_chars = params.get("maxChars") or params.get("max_chars") or self.max_chars
        max_chars = max(100, int(max_chars))

        is_valid, error_message = _validate_url(url)
        if not is_valid:
            return f"Error: URL validation failed: {error_message}"

        try:
            status, final_url, headers, body = await _http_get_text(
                url=url,
                headers={"User-Agent": _USER_AGENT},
                timeout_seconds=30.0,
                max_redirects=_MAX_REDIRECTS,
            )
        except Exception as exc:
            return f"Error: fetch failed: {exc}"

        if status >= 400:
            return f"Error: fetch failed with HTTP {status}"

        content_type = (headers.get("Content-Type") or headers.get("content-type") or "").lower()
        extractor = "raw"

        if "application/json" in content_type:
            extractor = "json"
            try:
                content_obj = json.loads(body)
                text = json.dumps(content_obj, indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                text = body
        elif "text/html" in content_type or body[:256].lower().startswith(("<!doctype", "<html")):
            title = _extract_html_title(body)
            extractor = "html-markdown" if extract_mode == "markdown" else "html-text"
            if extract_mode == "markdown":
                content = _html_to_markdown(body)
            else:
                content = _normalize(_strip_tags(body))
            text = f"# {title}\n\n{content}" if title else content
        else:
            text = body

        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]

        return json.dumps(
            {
                "url": url,
                "finalUrl": final_url,
                "status": status,
                "extractor": extractor,
                "truncated": truncated,
                "length": len(text),
                "text": text,
            },
            ensure_ascii=False,
        )
