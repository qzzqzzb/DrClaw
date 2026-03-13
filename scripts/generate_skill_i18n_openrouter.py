#!/usr/bin/env python3
"""Generate concise Chinese skill descriptions via OpenRouter.

Reads SKILL.md YAML frontmatter and fills:
  i18n.zh.description

Default mode is dry-run (no file writes).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error, request

import yaml


DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MAX_CHARS = 32


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate concise zh descriptions for SKILL.md frontmatter via OpenRouter.",
    )
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        help="Root to scan recursively for SKILL.md (repeatable).",
    )
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        help="Specific SKILL.md file (repeatable).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max files to process (0 = no limit).",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="OpenRouter API key. If omitted, use OPENROUTER_API_KEY or ~/.drclaw/config.json.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL),
        help="OpenRouter base URL.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENROUTER_MODEL", ""),
        help="Model ID (defaults to config provider.model, then openrouter/anthropic/claude-opus-4.1).",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help="Max Chinese chars in generated description.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write back SKILL.md (default is dry-run).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing i18n.zh.description.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Sleep seconds between requests.",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=45.0,
        help="HTTP timeout per request in seconds.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=80,
        help="Max tokens for model output.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Retry count on request failure.",
    )
    parser.add_argument(
        "--retry-wait",
        type=float,
        default=1.5,
        help="Seconds to wait between retries.",
    )
    return parser.parse_args()


def load_drclaw_config() -> dict[str, Any]:
    cfg_path = Path.home() / ".drclaw" / "config.json"
    if not cfg_path.is_file():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def resolve_api_key(args: argparse.Namespace, cfg: dict[str, Any]) -> str:
    if args.api_key.strip():
        return args.api_key.strip()
    env_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if env_key:
        return env_key
    provider = cfg.get("provider", {})
    if isinstance(provider, dict):
        key = str(provider.get("api_key", "")).strip()
        if key:
            return key
    return ""


def resolve_model(args: argparse.Namespace, cfg: dict[str, Any]) -> str:
    if args.model.strip():
        return args.model.strip()
    provider = cfg.get("provider", {})
    if isinstance(provider, dict):
        model = str(provider.get("model", "")).strip()
        if model:
            return model
    return "openrouter/anthropic/claude-opus-4.1"


def collect_skill_files(args: argparse.Namespace) -> list[Path]:
    files: set[Path] = set()

    explicit_files = [Path(p).expanduser() for p in args.file]
    for p in explicit_files:
        if p.is_file() and p.name == "SKILL.md":
            files.add(p.resolve())

    roots = [Path(p).expanduser() for p in args.root]
    if not roots and not explicit_files:
        roots = [Path.home() / ".drclaw" / "skills", Path.home() / ".drclaw" / "local-skill-hub"]

    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("SKILL.md"):
            if p.is_file():
                files.add(p.resolve())

    ordered = sorted(files, key=lambda p: str(p))
    if args.limit and args.limit > 0:
        return ordered[: args.limit]
    return ordered


def split_frontmatter(text: str) -> tuple[str, str] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    return text[4:end], text[end + 5 :]


def parse_frontmatter(text: str) -> dict[str, Any] | None:
    parsed = split_frontmatter(text)
    if parsed is None:
        return None
    block, _ = parsed
    data = yaml.safe_load(block)
    if not isinstance(data, dict):
        return None
    return data


def dump_frontmatter(data: dict[str, Any], body: str) -> str:
    fm = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{fm}\n---\n{body}"


def extract_json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        raw = "\n".join(parts)
    if not isinstance(raw, str):
        raw = ""
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object in model output: {raw[:200]!r}")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Model output JSON is not an object")
    return parsed


def normalize_zh_description(text: str, max_chars: int) -> str:
    out = re.sub(r"\s+", " ", text).strip()
    out = out.strip("。；;，, ")
    if not out:
        return out

    # Keep final output length (including punctuation) within max_chars.
    reserve = 1
    if max_chars <= reserve:
        return "。"
    limit = max_chars - reserve
    if len(out) > limit:
        out = out[:limit].rstrip("，。；; ")
    if not out:
        out = "简介"
    result = out + "。"
    if len(result) > max_chars:
        result = result[:max_chars]
        result = result.rstrip("，；; ")
        if not result.endswith("。"):
            if len(result) >= max_chars:
                result = result[: max_chars - 1]
            result += "。"
    return result


def request_openrouter(
    *,
    api_key: str,
    base_url: str,
    model: str,
    skill_name: str,
    skill_desc_en: str,
    skill_path: str,
    max_chars: int,
    request_timeout: float,
    max_tokens: int,
    retries: int,
    retry_wait: float,
) -> str:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    system_prompt = (
        "You localize skill marketplace metadata. "
        "Return only JSON."
    )
    user_prompt = (
        "Generate concise Simplified Chinese description for a skill card.\n"
        "Requirements:\n"
        f"- <= {max_chars} Chinese characters.\n"
        "- Keep meaning faithful but concise.\n"
        "- No markdown, no extra commentary.\n"
        '- Output strictly JSON: {"description":"..."}\n\n'
        f"skill_name: {skill_name}\n"
        f"english_description: {skill_desc_en}\n"
        f"skill_path: {skill_path}\n"
    )
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "include_reasoning": False,
        "reasoning": {"enabled": False},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    raw = ""
    last_err: Exception | None = None
    attempts = max(1, retries + 1)
    for idx in range(attempts):
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://drclaw.local",
                "X-Title": "DrClaw Skill i18n Generator",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=request_timeout) as resp:
                raw = resp.read().decode("utf-8")
                last_err = None
                break
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_err = RuntimeError(f"HTTP {exc.code}: {detail}")
        except Exception as exc:
            last_err = RuntimeError(f"Request failed: {exc}")

        if idx < attempts - 1:
            time.sleep(max(0.0, retry_wait))
    if last_err is not None:
        raise last_err

    data = json.loads(raw)
    try:
        message = data["choices"][0]["message"]
        content = message.get("content")
    except Exception as exc:
        raise RuntimeError(f"Unexpected OpenRouter response: {raw[:500]}") from exc

    obj = extract_json_object(content)
    desc_raw = obj.get("description", "")
    desc = desc_raw.strip() if isinstance(desc_raw, str) else ""
    if not desc:
        raise RuntimeError(f"Empty description from model: {content!r}")
    return normalize_zh_description(desc, max_chars=max_chars)


def get_existing_zh_desc(frontmatter: dict[str, Any]) -> str:
    i18n = frontmatter.get("i18n")
    if not isinstance(i18n, dict):
        return ""
    zh = i18n.get("zh")
    if not isinstance(zh, dict):
        return ""
    desc = zh.get("description")
    if isinstance(desc, str):
        return desc.strip()
    return ""


def set_zh_desc(frontmatter: dict[str, Any], zh_desc: str) -> None:
    i18n = frontmatter.get("i18n")
    if not isinstance(i18n, dict):
        i18n = {}
    zh = i18n.get("zh")
    if not isinstance(zh, dict):
        zh = {}
    zh["description"] = zh_desc
    i18n["zh"] = zh
    frontmatter["i18n"] = i18n


def process_file(
    path: Path,
    *,
    api_key: str,
    base_url: str,
    model: str,
    max_chars: int,
    write: bool,
    overwrite: bool,
    request_timeout: float,
    max_tokens: int,
    retries: int,
    retry_wait: float,
) -> tuple[str, str, str, bool]:
    text = path.read_text(encoding="utf-8")
    parsed = split_frontmatter(text)
    if parsed is None:
        raise RuntimeError("Missing YAML frontmatter")
    block, body = parsed

    frontmatter = yaml.safe_load(block)
    if not isinstance(frontmatter, dict):
        raise RuntimeError("Frontmatter is not a mapping")

    skill_name = str(frontmatter.get("name") or path.parent.name).strip()
    en_desc = str(frontmatter.get("description") or "").strip()
    if not en_desc:
        raise RuntimeError("Missing English description in frontmatter")

    old_zh = get_existing_zh_desc(frontmatter)
    if old_zh and not overwrite:
        return en_desc, old_zh, old_zh, False

    new_zh = request_openrouter(
        api_key=api_key,
        base_url=base_url,
        model=model,
        skill_name=skill_name,
        skill_desc_en=en_desc,
        skill_path=str(path),
        max_chars=max_chars,
        request_timeout=request_timeout,
        max_tokens=max_tokens,
        retries=retries,
        retry_wait=retry_wait,
    )

    if not write:
        return en_desc, old_zh, new_zh, True

    set_zh_desc(frontmatter, new_zh)
    new_text = dump_frontmatter(frontmatter, body)
    path.write_text(new_text, encoding="utf-8")
    return en_desc, old_zh, new_zh, True


def main() -> int:
    args = parse_args()
    cfg = load_drclaw_config()
    api_key = resolve_api_key(args, cfg)
    model = resolve_model(args, cfg)
    if not api_key:
        print("ERROR: Missing OpenRouter API key.", file=sys.stderr)
        print("Set --api-key or OPENROUTER_API_KEY, or configure ~/.drclaw/config.json.", file=sys.stderr)
        return 2

    files = collect_skill_files(args)
    if not files:
        print("No SKILL.md files found.")
        return 0

    print(f"model={model}")
    print(f"mode={'write' if args.write else 'dry-run'} overwrite={args.overwrite}")
    print(f"files={len(files)}")

    ok = 0
    skipped = 0
    failed = 0
    for idx, path in enumerate(files, start=1):
        try:
            en_desc, old_zh, new_zh, changed = process_file(
                path,
                api_key=api_key,
                base_url=args.base_url,
                model=model,
                max_chars=args.max_chars,
                write=args.write,
                overwrite=args.overwrite,
                request_timeout=args.request_timeout,
                max_tokens=args.max_tokens,
                retries=args.retries,
                retry_wait=args.retry_wait,
            )
            status = "updated" if changed else "skipped"
            if changed:
                ok += 1
            else:
                skipped += 1
            print(f"[{idx}/{len(files)}] {status} {path}", flush=True)
            print(f"  EN: {en_desc}", flush=True)
            if old_zh:
                print(f"  OLD_ZH: {old_zh}", flush=True)
            print(f"  NEW_ZH: {new_zh}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"[{idx}/{len(files)}] failed {path}: {exc}", file=sys.stderr, flush=True)
        if args.sleep > 0:
            time.sleep(args.sleep)

    print(f"done ok={ok} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
