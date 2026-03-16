"""HTTP and WebSocket route handlers for the web frontend."""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from aiohttp import WSMsgType, web
from pydantic import ValidationError

from drclaw.agent.registry import AgentHandle, AgentStatus
from drclaw.config.loader import save_config
from drclaw.config.schema import DrClawConfig
from drclaw.frontends.web.chat_files import ChatFileStore
from drclaw.models.messages import InboundMessage
from drclaw.session.manager import SessionManager
from drclaw.skills.local_hub import LocalSkillHubStore
from drclaw.soul import load_project_soul
from drclaw.utils.helpers import slugify

if TYPE_CHECKING:
    from drclaw.frontends.web.adapter import WebAdapter
    from drclaw.models.project import Project

STATIC_DIR = Path(__file__).parent / "static"
BUNDLED_ASSETS_DIR = Path(__file__).resolve().parents[3] / "assets"
SKILL_MARKDOWN_NAME = "SKILL.md"
SOUL_MARKDOWN_NAME = "SOUL.md"
SKILL_SOURCE_PRIORITY = {"global": 0, "hub": 1, "agent": 2}
DEFAULT_WEB_LOCALE = "en"
SUPPORTED_WEB_LOCALES = {"en", "zh"}
SKILL_SOURCE_LABELS = {
    "en": {"global": "global", "hub": "hub", "agent": "Agent"},
    "zh": {"global": "全局", "hub": "本地技能库", "agent": "智能体"},
}
SKILL_SOURCE_LABEL = SKILL_SOURCE_LABELS[DEFAULT_WEB_LOCALE]
AGENT_ROLE_LABELS = {
    "main": {"en": "Main orchestrator agent", "zh": "主控协调智能体"},
    "project": {"en": "Project execution agent", "zh": "项目执行智能体"},
    "equipment": {"en": "Equipment runtime agent", "zh": "设备运行智能体"},
    "external": {"en": "External provider agent", "zh": "外部智能体"},
}
AGENT_SKILL_VARIANT_WARNING = (
    "Multiple agent copies exist and may differ; showing one deterministic copy."
)
_AGENT_SOURCE_PREFIXES = ("proj:", "equip:", "claude_code:", "ext:")
_AGENT_SOURCE_EXACT = {"main", "cron"}
_CHAT_FILES_MAX_COUNT = 10
_CHAT_FILE_MAX_BYTES = 20 * 1024 * 1024
_CHAT_UPLOAD_MAX_TOTAL_BYTES = 50 * 1024 * 1024
_CHAT_ALLOWED_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".pdf",
    ".docx",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".zip",
}


class SkillUploadConflictError(ValueError):
    """Raised when uploaded skill conflicts with existing skill name."""


def _request_locale(request: web.Request) -> str:
    raw = request.query.get("lang", DEFAULT_WEB_LOCALE)
    if not isinstance(raw, str):
        return DEFAULT_WEB_LOCALE
    locale = raw.strip().lower()
    if locale in SUPPORTED_WEB_LOCALES:
        return locale
    return DEFAULT_WEB_LOCALE


def _localized_text(
    *,
    fallback: str,
    locale: str,
    i18n_map: dict[str, dict[str, str]] | None,
    field: str,
) -> str:
    if locale == DEFAULT_WEB_LOCALE:
        return fallback
    if not isinstance(i18n_map, dict):
        return fallback
    localized = i18n_map.get(locale, {})
    if not isinstance(localized, dict):
        return fallback
    value = localized.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _localized_skill_source_label(source: str, locale: str) -> str:
    return SKILL_SOURCE_LABELS.get(locale, SKILL_SOURCE_LABELS[DEFAULT_WEB_LOCALE]).get(
        source,
        source,
    )


def _localized_agent_role(role_key: str, locale: str) -> str:
    mapping = AGENT_ROLE_LABELS.get(role_key, {})
    value = mapping.get(locale)
    if isinstance(value, str) and value:
        return value
    return mapping.get(DEFAULT_WEB_LOCALE, role_key)


def _normalize_avatar_for_web(avatar: str | None, data_dir: Path) -> str | None:
    """Normalize avatar values into browser-safe URLs when possible."""
    if not isinstance(avatar, str):
        return None
    raw = avatar.strip()
    if not raw:
        return None
    if raw.startswith(("http://", "https://", "data:")):
        return raw
    if raw.startswith("/assets/"):
        return raw
    if raw.startswith("assets/"):
        return f"/{raw}"

    try:
        resolved_avatar = Path(raw).expanduser().resolve(strict=False)
        assets_dir = (data_dir / "assets").resolve()
    except (OSError, RuntimeError, ValueError):
        return raw

    if resolved_avatar.is_relative_to(assets_dir):
        rel = resolved_avatar.relative_to(assets_dir).as_posix()
        return f"/assets/{rel}"
    return raw


def _is_ignored_zip_entry(path: str) -> bool:
    parts = [part for part in Path(path).parts if part not in ("", ".")]
    if not parts:
        return True
    if any(part == "__MACOSX" for part in parts):
        return True
    if parts[-1] == ".DS_Store":
        return True
    return False


def _safe_extract_zip(zip_file: zipfile.ZipFile, destination: Path) -> None:
    dest_root = destination.resolve()
    for info in zip_file.infolist():
        raw_name = info.filename
        if not raw_name or raw_name.endswith("/"):
            continue
        if _is_ignored_zip_entry(raw_name):
            continue

        output_path = (destination / raw_name).resolve()
        if not output_path.is_relative_to(dest_root):
            raise ValueError("ZIP contains unsafe path traversal entries.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with zip_file.open(info, "r") as src, output_path.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def _resolve_uploaded_skill_root(extract_root: Path, upload_filename: str) -> tuple[Path, str]:
    root_skill = extract_root / SKILL_MARKDOWN_NAME
    if root_skill.is_file():
        inferred_name = Path(upload_filename).stem.strip()
        if not inferred_name:
            raise ValueError("Uploaded ZIP filename is invalid.")
        return extract_root, inferred_name

    top_entries = [
        entry
        for entry in extract_root.iterdir()
        if not _is_ignored_zip_entry(entry.name)
    ]
    if len(top_entries) != 1 or not top_entries[0].is_dir():
        raise ValueError(
            "ZIP must contain SKILL.md at root, or one top-level skill "
            "directory containing SKILL.md.",
        )

    skill_root = top_entries[0]
    if not (skill_root / SKILL_MARKDOWN_NAME).is_file():
        raise ValueError("Uploaded ZIP is missing SKILL.md.")
    return skill_root, skill_root.name


def _skill_frontmatter(content: str) -> dict[str, Any]:
    if not content.startswith("---"):
        return {}
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = idx
            break
    if end is None:
        return {}
    block = "\n".join(lines[1:end]).strip()
    if not block:
        return {}
    try:
        parsed = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _skill_i18n_map(frontmatter: dict[str, Any]) -> dict[str, dict[str, str]]:
    def normalize(raw: Any) -> dict[str, dict[str, str]]:
        if not isinstance(raw, dict):
            return {}
        out: dict[str, dict[str, str]] = {}
        for lang_raw, payload in raw.items():
            if not isinstance(lang_raw, str):
                continue
            lang = lang_raw.strip().lower()
            if not lang or not isinstance(payload, dict):
                continue
            entry: dict[str, str] = {}
            for key in ("name", "description"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    entry[key] = value.strip()
            if entry:
                out[lang] = entry
        return out

    direct = normalize(frontmatter.get("i18n"))
    if direct:
        return direct
    metadata = frontmatter.get("metadata")
    if isinstance(metadata, dict):
        nested = normalize(metadata.get("i18n"))
        if nested:
            return nested
    return {}


def _skill_name_from_frontmatter(
    frontmatter: dict[str, Any],
    fallback_name: str,
) -> str:
    raw = frontmatter.get("name")
    if isinstance(raw, str):
        name = raw.strip()
        if name:
            return name
    return fallback_name


def _skill_description_from_frontmatter(
    frontmatter: dict[str, Any],
    fallback_name: str,
) -> str:
    raw = frontmatter.get("description")
    if isinstance(raw, str):
        desc = raw.strip()
        if desc:
            return desc
    return fallback_name


def _skill_description_from_content(content: str, fallback_name: str) -> str:
    frontmatter = _skill_frontmatter(content)
    return _skill_description_from_frontmatter(frontmatter, fallback_name)


def _skill_tags_from_content(content: str) -> list[str]:
    """Extract tags from SKILL.md YAML frontmatter ``tags`` field."""
    frontmatter = _skill_frontmatter(content)
    raw = frontmatter.get("tags")
    if raw is None:
        return []
    values: list[str] = []
    if isinstance(raw, str):
        values = [item.strip().lower() for item in raw.split(",")]
    elif isinstance(raw, list):
        values = [str(item).strip().lower() for item in raw]
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _iter_skill_files(root: Path) -> list[tuple[str, Path]]:
    if not root.is_dir():
        return []
    entries: list[tuple[str, Path]] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        skill_file = entry / SKILL_MARKDOWN_NAME
        if skill_file.is_file():
            entries.append((entry.name, skill_file))
    return entries


def _make_skill_id(source: str, skill_name: str) -> str:
    return f"{source}:{skill_name}"


def _append_skill_candidates(
    out: list[dict],
    *,
    source: str,
    root: Path,
    locale: str,
    candidate_id_prefix: str,
    candidate_id_owner: str = "",
    candidate_agent_name: str = "",
    candidate_agent_display_name: str = "",
    extra_tags: list[str] | None = None,
) -> None:
    for skill_name, skill_file in _iter_skill_files(root):
        content = skill_file.read_text(encoding="utf-8")
        frontmatter = _skill_frontmatter(content)
        i18n_map = _skill_i18n_map(frontmatter)
        frontmatter_tags = _skill_tags_from_content(content)
        merged: list[str] = list(extra_tags or [])
        seen = set(merged)
        for t in frontmatter_tags:
            if t not in seen:
                seen.add(t)
                merged.append(t)
        default_name = _skill_name_from_frontmatter(frontmatter, skill_name)
        description = _skill_description_from_frontmatter(frontmatter, skill_name)
        candidate_id = (
            _make_skill_id(candidate_id_prefix, skill_name)
            if not candidate_id_owner
            else f"{candidate_id_prefix}:{candidate_id_owner}:{skill_name}"
        )
        out.append(
            {
                "candidate_id": candidate_id,
                "name": skill_name,
                "source": source,
                "path": str(skill_file),
                "agent_name": candidate_agent_name,
                "agent_display_name": candidate_agent_display_name or candidate_agent_name,
                "display_name": _localized_text(
                    fallback=default_name,
                    locale=locale,
                    i18n_map=i18n_map,
                    field="name",
                ),
                "description": description,
                "display_description": _localized_text(
                    fallback=description,
                    locale=locale,
                    i18n_map=i18n_map,
                    field="description",
                ),
                "tags": merged,
            }
        )


def _collect_skill_candidates(request: web.Request, *, locale: str) -> list[dict]:
    kernel = request.app["kernel"]
    data_dir = kernel.config.data_path
    entries: list[dict] = []
    template_i18n_by_name = _template_i18n_map(kernel)

    _append_skill_candidates(
        entries,
        source="global",
        root=data_dir / "skills",
        locale=locale,
        candidate_id_prefix="global",
    )

    project_store = getattr(kernel, "project_store", None)
    if project_store is not None:
        for project in project_store.list_projects():
            project_root = data_dir / "projects" / project.id / "workspace" / "skills"
            project_display_name = project.name
            if project.hub_template:
                project_display_name = _localized_text(
                    fallback=project.name,
                    locale=locale,
                    i18n_map=template_i18n_by_name.get(project.hub_template),
                    field="name",
                )
            _append_skill_candidates(
                entries,
                source="agent",
                root=project_root,
                locale=locale,
                candidate_id_prefix="project",
                candidate_id_owner=project.id,
                candidate_agent_name=project.name,
                candidate_agent_display_name=project_display_name,
            )

    equipment_manager = getattr(kernel, "equipment_manager", None)
    prototype_store = getattr(equipment_manager, "prototype_store", None)
    if prototype_store is not None:
        for prototype in prototype_store.list():
            _append_skill_candidates(
                entries,
                source="agent",
                root=prototype.skills_dir,
                locale=locale,
                candidate_id_prefix="equipment",
                candidate_id_owner=prototype.name,
                candidate_agent_name=prototype.name,
            )

    hub_store = LocalSkillHubStore(root_dir=data_dir / "local-skill-hub")
    for hub_skill in hub_store.list():
        category_tags = [t for t in hub_skill.category.split("/") if t]
        content = hub_skill.skill_file.read_text(encoding="utf-8")
        frontmatter_tags = _skill_tags_from_content(content)
        merged: list[str] = list(category_tags)
        seen = set(merged)
        for t in frontmatter_tags:
            if t not in seen:
                seen.add(t)
                merged.append(t)
        frontmatter = _skill_frontmatter(content)
        i18n_map = _skill_i18n_map(frontmatter)
        default_name = _skill_name_from_frontmatter(frontmatter, hub_skill.name)
        description = _skill_description_from_frontmatter(frontmatter, hub_skill.name)
        entries.append({
            "candidate_id": f"hub:{hub_skill.ref}",
            "name": hub_skill.name,
            "source": "hub",
            "path": str(hub_skill.skill_file),
            "agent_name": "",
            "agent_display_name": "",
            "display_name": _localized_text(
                fallback=default_name,
                locale=locale,
                i18n_map=i18n_map,
                field="name",
            ),
            "description": description,
            "display_description": _localized_text(
                fallback=description,
                locale=locale,
                i18n_map=i18n_map,
                field="description",
            ),
            "tags": merged,
        })
    return entries


def _merged_tags(entries: list[dict]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for item in entries:
        for tag in item.get("tags", []):
            if tag not in seen:
                seen.add(tag)
                tags.append(tag)
    return tags


def _candidate_pick_key(item: dict) -> tuple[int, str]:
    return (SKILL_SOURCE_PRIORITY[item["source"]], item["path"])


def _list_skill_summaries(request: web.Request, *, locale: str) -> list[dict]:
    candidates = _collect_skill_candidates(request, locale=locale)
    by_name: dict[str, list[dict]] = defaultdict(list)
    for candidate in candidates:
        by_name[candidate["name"]].append(candidate)

    summaries: list[dict] = []
    for skill_name in sorted(by_name):
        group = sorted(by_name[skill_name], key=_candidate_pick_key)
        winner = group[0]
        used_by_agents = sorted(
            {
                str(item["agent_name"])
                for item in group
                if item["source"] == "agent" and item.get("agent_name")
            }
        )
        used_by_agents_display = sorted(
            {
                str(item["agent_display_name"])
                for item in group
                if item["source"] == "agent" and item.get("agent_display_name")
            }
        )

        summary = {
            "id": _make_skill_id(winner["source"], skill_name),
            "name": skill_name,
            "display_name": winner.get("display_name", skill_name),
            "source": winner["source"],
            "source_label": _localized_skill_source_label(winner["source"], locale),
            "used_by_agent": bool(used_by_agents),
            "used_by_agents": used_by_agents,
            "used_by_agents_display": used_by_agents_display,
            "path": winner["path"],
            "description": winner["description"],
            "display_description": winner.get("display_description", winner["description"]),
            "tags": _merged_tags(group),
        }
        if winner["source"] == "agent":
            agent_variants = [item for item in group if item["source"] == "agent"]
            if len(agent_variants) > 1:
                summary["warning"] = AGENT_SKILL_VARIANT_WARNING
        summaries.append(summary)
    return summaries


def _public_skill_summary(summary: dict) -> dict:
    return {
        "id": summary["id"],
        "name": summary["name"],
        "display_name": summary.get("display_name", summary["name"]),
        "source": summary["source"],
        "source_label": summary["source_label"],
        "used_by_agent": summary["used_by_agent"],
        "used_by_agents": summary["used_by_agents"],
        "used_by_agents_display": summary.get("used_by_agents_display", summary["used_by_agents"]),
        "description": summary["description"],
        "display_description": summary.get("display_description", summary["description"]),
        "tags": summary.get("tags", []),
    }


def _resolve_skill_summary(request: web.Request, skill_ref: str, *, locale: str) -> dict | None:
    summaries = _list_skill_summaries(request, locale=locale)
    by_id = {item["id"]: item for item in summaries}
    if skill_ref in by_id:
        return by_id[skill_ref]

    # Backward-compat for old /api/skills/{name} references.
    if ":" not in skill_ref:
        for item in summaries:
            if item["name"] == skill_ref:
                return item

    # Backward-compat for old source-scoped IDs, e.g. project:<id>:<skill>.
    for candidate in _collect_skill_candidates(request, locale=locale):
        if candidate["candidate_id"] != skill_ref:
            continue
        for item in summaries:
            if item["name"] == candidate["name"]:
                return item
    return None


async def handle_index(request: web.Request) -> web.Response:
    html = (STATIC_DIR / "index.html").read_text()
    return web.Response(text=html, content_type="text/html")


def _template_avatar_map(kernel) -> dict[str, str]:
    hub_store = getattr(kernel, "agent_hub_store", None)
    if hub_store is None:
        return {}
    data_dir = kernel.config.data_path
    avatars: dict[str, str] = {}
    for template in hub_store.list():
        avatar = getattr(template, "avatar", None)
        name = getattr(template, "name", "")
        normalized = _normalize_avatar_for_web(avatar, data_dir)
        if isinstance(name, str) and name and isinstance(normalized, str) and normalized:
            avatars[name] = normalized
    return avatars


def _template_i18n_map(kernel) -> dict[str, dict[str, dict[str, str]]]:
    hub_store = getattr(kernel, "agent_hub_store", None)
    if hub_store is None:
        return {}
    i18n_by_template: dict[str, dict[str, dict[str, str]]] = {}
    for template in hub_store.list():
        name = getattr(template, "name", "")
        if not isinstance(name, str) or not name:
            continue
        i18n = getattr(template, "i18n", None)
        if isinstance(i18n, dict):
            for lang, payload in i18n.items():
                if isinstance(lang, str) and isinstance(payload, dict):
                    current: dict[str, str] = {}
                    for key in ("name", "description"):
                        value = payload.get(key)
                        if isinstance(value, str) and value.strip():
                            current[key] = value.strip()
                    if current:
                        i18n_by_template.setdefault(name, {})[lang] = current
    return i18n_by_template


def _resolve_template_avatar(
    template_name: str | None,
    avatars_by_template: dict[str, str],
) -> str | None:
    if not template_name:
        return None
    return avatars_by_template.get(template_name)


def _project_goals_list(project: "Project") -> list[str]:
    return [line.strip() for line in project.goals.splitlines() if line.strip()]


def _normalize_string_list(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            value = str(item).strip()
            if value:
                out.append(value)
        return out
    if isinstance(raw, str):
        return [line.strip() for line in raw.splitlines() if line.strip()]
    return []


def _project_publish_skills(project: "Project", data_dir: Path) -> list[dict[str, str]]:
    merged_sources: dict[str, str] = {}
    global_skills_root = data_dir / "skills"
    project_skills_root = data_dir / "projects" / project.id / "workspace" / "skills"

    if global_skills_root.is_dir():
        for skill_dir in sorted(global_skills_root.iterdir(), key=lambda p: p.name):
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file():
                merged_sources[skill_dir.name] = "global"

    if project_skills_root.is_dir():
        for skill_dir in sorted(project_skills_root.iterdir(), key=lambda p: p.name):
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file():
                merged_sources[skill_dir.name] = "project"

    return [
        {"name": skill_name, "source": merged_sources[skill_name]}
        for skill_name in sorted(merged_sources.keys())
    ]


def _project_workspace_dir(project: "Project", data_dir: Path) -> Path:
    return data_dir / "projects" / project.id / "workspace"


def _project_soul_content(project: "Project", data_dir: Path) -> str:
    workspace_dir = _project_workspace_dir(project, data_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Ensure default SOUL.md exists and return current content.
        return load_project_soul(workspace_dir)
    except OSError:
        return ""


def _publish_draft_payload(project: "Project", data_dir: Path) -> dict[str, object]:
    return {
        "name": project.name,
        "description": project.description,
        "tags": list(project.tags),
        "goals": _project_goals_list(project),
        "soul": _project_soul_content(project, data_dir),
        "skills": _project_publish_skills(project, data_dir),
    }


def _agent_payload_for_handle(
    handle: "AgentHandle",
    *,
    locale: str = DEFAULT_WEB_LOCALE,
    display_name: str | None = None,
    avatar: str | None = None,
) -> dict[str, str | None]:
    role_type = "assistant" if handle.agent_type == "main" else "student"
    if handle.agent_type == "main":
        name = "虾秘"
        role_key = "main"
    else:
        name = handle.label
        role_key = "project"
    role = _localized_agent_role(role_key, DEFAULT_WEB_LOCALE)
    status = "error" if handle.status == AgentStatus.ERROR else "idle"
    return {
        "id": handle.agent_id,
        "label": handle.label,
        "name": name,
        "display_name": display_name or name,
        "role": role,
        "display_role": _localized_agent_role(role_key, locale),
        "type": role_type,
        "status": status,
        "avatar": avatar,
    }


def _idle_project_payload(
    project: "Project",
    *,
    locale: str = DEFAULT_WEB_LOCALE,
    display_name: str | None = None,
    avatar: str | None = None,
) -> dict[str, str | None]:
    return {
        "id": f"proj:{project.id}",
        "label": project.name,
        "name": project.name,
        "display_name": display_name or project.name,
        "role": "Project execution agent",
        "display_role": _localized_agent_role("project", locale),
        "type": "student",
        "status": "idle",
        "hub_template": project.hub_template,
        "avatar": avatar,
    }


def _usage_totals_zero() -> dict[str, int | float]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "input_cost_usd": 0.0,
        "output_cost_usd": 0.0,
        "total_cost_usd": 0.0,
    }


def _agent_name_map_for_statistics(kernel) -> dict[str, str]:
    names: dict[str, str] = {"main": "虾秘"}

    for handle in kernel.registry.list_agents():
        if handle.agent_id == "main":
            names[handle.agent_id] = "虾秘"
            continue
        if handle.project is not None and handle.project.name:
            names[handle.agent_id] = handle.project.name
            continue
        if handle.label:
            names[handle.agent_id] = handle.label

    for project in kernel.project_store.list_projects():
        names.setdefault(f"proj:{project.id}", project.name)

    for runtime in kernel.equipment_manager.list_runtime_agents():
        runtime_id = runtime.get("id")
        if not isinstance(runtime_id, str) or not runtime_id:
            continue
        label = runtime.get("label")
        names[runtime_id] = label if isinstance(label, str) and label else runtime_id

    for external in kernel.external_agent_bridge.list_agent_payloads():
        ext_id = external.get("id")
        ext_name = external.get("name")
        if isinstance(ext_id, str) and ext_id:
            names[ext_id] = (
                ext_name
                if isinstance(ext_name, str) and ext_name
                else ext_id
            )

    return names


def _agent_display_name_map_for_statistics(kernel, *, locale: str) -> dict[str, str]:
    names = dict(_agent_name_map_for_statistics(kernel))
    if locale == DEFAULT_WEB_LOCALE:
        return names
    template_i18n_by_name = _template_i18n_map(kernel)
    for handle in kernel.registry.list_agents():
        project = getattr(handle, "project", None)
        if project is None:
            continue
        template_name = getattr(project, "hub_template", None)
        if not isinstance(template_name, str) or not template_name:
            continue
        names[handle.agent_id] = _localized_text(
            fallback=names.get(handle.agent_id, project.name),
            locale=locale,
            i18n_map=template_i18n_by_name.get(template_name),
            field="name",
        )
    for project in kernel.project_store.list_projects():
        template_name = project.hub_template
        if not template_name:
            continue
        agent_id = f"proj:{project.id}"
        names[agent_id] = _localized_text(
            fallback=names.get(agent_id, project.name),
            locale=locale,
            i18n_map=template_i18n_by_name.get(template_name),
            field="name",
        )
    return names


async def handle_agents(request: web.Request) -> web.Response:
    kernel = request.app["kernel"]
    locale = _request_locale(request)
    data_dir = kernel.config.data_path
    avatars_by_template = _template_avatar_map(kernel)
    template_i18n_by_name = _template_i18n_map(kernel)
    agents = []
    represented_project_ids: set[str] = set()
    for h in kernel.registry.list_agents():
        if h.agent_type == "project":
            if h.project is None:
                continue
            represented_project_ids.add(h.project.id)
        display_name = None
        if h.agent_type == "project" and h.project is not None:
            display_name = _localized_text(
                fallback=h.project.name,
                locale=locale,
                i18n_map=template_i18n_by_name.get(h.project.hub_template or ""),
                field="name",
            )
        payload = _agent_payload_for_handle(h, locale=locale, display_name=display_name)
        if h.agent_type == "project" and h.project is not None:
            payload["hub_template"] = h.project.hub_template
            payload["avatar"] = _resolve_template_avatar(
                h.project.hub_template,
                avatars_by_template,
            )
        agents.append(payload)

    for project in kernel.project_store.list_projects():
        if project.id in represented_project_ids:
            continue
        agents.append(
            _idle_project_payload(
                project,
                locale=locale,
                display_name=_localized_text(
                    fallback=project.name,
                    locale=locale,
                    i18n_map=template_i18n_by_name.get(project.hub_template or ""),
                    field="name",
                ),
                avatar=_resolve_template_avatar(project.hub_template, avatars_by_template),
            )
        )

    for runtime in kernel.equipment_manager.list_runtime_agents():
        runtime_status = runtime.get("status")
        status = "error" if runtime_status == "error" else "idle"
        agents.append({
            **runtime,
            "status": status,
            "name": runtime.get("label", runtime["id"]),
            "display_name": runtime.get("label", runtime["id"]),
            "role": _localized_agent_role("equipment", DEFAULT_WEB_LOCALE),
            "display_role": _localized_agent_role("equipment", locale),
        })
    for external in kernel.external_agent_bridge.list_agent_payloads():
        payload = dict(external)
        payload["avatar"] = _normalize_avatar_for_web(payload.get("avatar"), data_dir)
        name = payload.get("name")
        role = payload.get("role")
        payload["display_name"] = name if isinstance(name, str) else payload.get("id")
        default_external_role = _localized_agent_role("external", DEFAULT_WEB_LOCALE)
        if isinstance(role, str) and role.strip():
            payload["display_role"] = (
                _localized_agent_role("external", locale)
                if role.strip() == default_external_role
                else role
            )
        else:
            payload["display_role"] = _localized_agent_role("external", locale)
        if not isinstance(role, str) or not role.strip():
            payload["role"] = default_external_role
        agents.append(payload)
    return web.json_response(agents)


def _normalize_history_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(content)


def _normalize_tool_call_text(tool_call: object) -> str:
    if not isinstance(tool_call, dict):
        return ""

    fn_name = ""
    fn_args = ""

    fn = tool_call.get("function")
    if isinstance(fn, dict):
        name_raw = fn.get("name")
        args_raw = fn.get("arguments")
    else:
        name_raw = tool_call.get("name")
        args_raw = tool_call.get("arguments")

    if isinstance(name_raw, str):
        fn_name = name_raw.strip()
    elif name_raw is not None:
        fn_name = str(name_raw)

    if isinstance(args_raw, str):
        fn_args = args_raw.strip()
    elif args_raw is not None:
        fn_args = _normalize_history_text(args_raw).strip()

    if fn_name and fn_args:
        return f"{fn_name}({fn_args})"
    if fn_name:
        return f"{fn_name}()"
    if fn_args:
        return f"tool_call({fn_args})"
    return ""


def _normalize_attachment_id(raw: Any) -> str:
    if isinstance(raw, dict):
        value = raw.get("id")
    else:
        value = raw
    if isinstance(value, str):
        file_id = value.strip().lower()
        if file_id:
            return file_id
    return ""


def _sanitize_history_attachments(
    raw: Any,
    *,
    file_store: ChatFileStore | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        file_id = item.get("id")
        name = item.get("name")
        mime = item.get("mime")
        size = item.get("size")
        download_url = item.get("download_url")
        cleaned_id = file_id.strip().lower() if isinstance(file_id, str) else ""
        if cleaned_id:
            if cleaned_id in seen:
                continue
            out_item: dict[str, Any] = {
                "id": cleaned_id,
                "name": str(name) if name is not None else "file",
                "mime": str(mime) if mime is not None else "application/octet-stream",
                "size": max(0, int(size)) if isinstance(size, (int, float)) else 0,
                "download_url": (
                    str(download_url)
                    if isinstance(download_url, str) and download_url.strip()
                    else ChatFileStore.download_url(cleaned_id)
                ),
            }
            out.append(out_item)
            seen.add(cleaned_id)
            continue

        path_raw = item.get("path")
        if not isinstance(path_raw, str) or not path_raw.strip() or file_store is None:
            continue
        try:
            out_item = file_store.ensure_descriptor_for_path(
                source_path=Path(path_raw),
                filename=str(name) if name is not None else None,
                content_type=str(mime) if mime is not None else None,
            )
        except (OSError, ValueError):
            continue
        if out_item["id"] in seen:
            continue
        out.append(out_item)
        seen.add(out_item["id"])
    return out


def _message_source(raw: dict[str, object]) -> str:
    source_raw = raw.get("source")
    if isinstance(source_raw, str):
        source = source_raw.strip()
        if source:
            return source
    return "unknown"


def _is_agent_like_source(source: str) -> bool:
    return source in _AGENT_SOURCE_EXACT or source.startswith(_AGENT_SOURCE_PREFIXES)


def _history_message_visible(role: str, source: str, verbose_chat: bool) -> bool:
    if verbose_chat:
        return True
    if role in {"assistant", "error"}:
        return True
    if role == "user":
        return not _is_agent_like_source(source)
    return False


def _load_agent_session_messages(kernel, agent_id: str) -> list[dict]:
    data_path = kernel.config.data_path
    if agent_id == "main":
        manager = SessionManager(data_path / "sessions")
        session = manager.load("main")
        return session.messages

    if not agent_id.startswith("proj:"):
        raise ValueError(f"Unsupported agent_id: {agent_id}")

    project_id = agent_id.split(":", 1)[1]
    project = kernel.project_store.get_project(project_id)
    if project is None:
        raise KeyError(project_id)

    manager = SessionManager(data_path / "projects" / project_id / "sessions")
    session = manager.load(agent_id)
    return session.messages


async def handle_agent_history(request: web.Request) -> web.Response:
    kernel = request.app["kernel"]
    verbose_chat = bool(kernel.config.daemon.verbose_chat)
    show_tool_calls = bool(kernel.config.daemon.show_tool_calls) and verbose_chat
    file_store = ChatFileStore(kernel.config.data_path)
    agent_id = request.match_info.get("agent_id", "").strip()
    if not agent_id:
        return web.json_response({"error": "Missing agent_id."}, status=400)

    try:
        messages = _load_agent_session_messages(kernel, agent_id)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except KeyError:
        return web.json_response({"error": f"Project not found for agent: {agent_id}"}, status=404)

    payload_messages: list[dict[str, object]] = []
    for raw in messages:
        role_raw = raw.get("role", "")
        if isinstance(role_raw, str):
            role = role_raw
        elif role_raw is None:
            role = ""
        else:
            role = str(role_raw)

        source = _message_source(raw)
        text = _normalize_history_text(raw.get("content"))
        attachments = _sanitize_history_attachments(raw.get("attachments"), file_store=file_store)
        tool_calls_raw = raw.get("tool_calls")
        tool_calls = tool_calls_raw if isinstance(tool_calls_raw, list) else []

        # Assistant tool-call turns often have empty content; keep tool call lines visible.
        if (text.strip() or attachments) and _history_message_visible(role, source, verbose_chat):
            entry: dict[str, object] = {
                "role": role,
                "text": text,
                "source": source,
                "visible": True,
            }
            if attachments:
                entry["files"] = attachments
            payload_messages.append(entry)

        if show_tool_calls:
            for call in tool_calls:
                call_text = _normalize_tool_call_text(call)
                if not call_text:
                    continue
                payload_messages.append(
                    {
                        "role": "tool_call",
                        "text": call_text,
                        "source": source,
                        "visible": True,
                    }
                )

    return web.json_response(
        {
            "agent_id": agent_id,
            "messages": payload_messages,
        }
    )


async def handle_statistics(request: web.Request) -> web.Response:
    kernel = request.app["kernel"]
    locale = _request_locale(request)
    usage_store = getattr(kernel, "usage_store", None)
    name_map = _agent_name_map_for_statistics(kernel)
    display_name_map = _agent_display_name_map_for_statistics(kernel, locale=locale)
    totals = {
        "all_time": _usage_totals_zero(),
        "this_run": _usage_totals_zero(),
    }

    if usage_store is None:
        rows = [
            {
                "agent_id": agent_id,
                "agent_name": agent_name,
                "display_agent_name": display_name_map.get(agent_id, agent_name),
                "all_time": _usage_totals_zero(),
                "this_run": _usage_totals_zero(),
            }
            for agent_id, agent_name in name_map.items()
        ]
        rows.sort(
            key=lambda item: (
                -int(item["all_time"].get("total_tokens", 0)),
                item["display_agent_name"],
            )
        )
        return web.json_response({"agents": rows, "totals": totals})

    snapshot = usage_store.snapshot()
    snapshot_totals = snapshot.get("totals")
    if isinstance(snapshot_totals, dict):
        totals = snapshot_totals

    rows_by_agent_id: dict[str, dict] = {}
    for row in snapshot.get("agents", []):
        if not isinstance(row, dict):
            continue
        agent_id = row.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            continue
        all_time = row.get("all_time")
        this_run = row.get("this_run")
        rows_by_agent_id[agent_id] = {
            "agent_id": agent_id,
            "agent_name": name_map.get(agent_id, agent_id),
            "display_agent_name": display_name_map.get(
                agent_id,
                name_map.get(agent_id, agent_id),
            ),
            "all_time": all_time if isinstance(all_time, dict) else _usage_totals_zero(),
            "this_run": this_run if isinstance(this_run, dict) else _usage_totals_zero(),
        }

    for agent_id, agent_name in name_map.items():
        if agent_id in rows_by_agent_id:
            rows_by_agent_id[agent_id]["agent_name"] = agent_name
            rows_by_agent_id[agent_id]["display_agent_name"] = display_name_map.get(
                agent_id,
                agent_name,
            )
            continue
        rows_by_agent_id[agent_id] = {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "display_agent_name": display_name_map.get(agent_id, agent_name),
            "all_time": _usage_totals_zero(),
            "this_run": _usage_totals_zero(),
        }

    rows = list(rows_by_agent_id.values())
    rows.sort(
        key=lambda item: (
            -int(item["all_time"].get("total_tokens", 0)),
            item["display_agent_name"],
        )
    )
    return web.json_response({"agents": rows, "totals": totals})


async def handle_get_config(request: web.Request) -> web.Response:
    kernel = request.app["kernel"]
    return web.json_response(kernel.config.model_dump(by_alias=True))


async def handle_activate_agent(request: web.Request) -> web.Response:
    kernel = request.app["kernel"]
    locale = _request_locale(request)
    agent_id = request.match_info.get("agent_id", "").strip()
    if not agent_id:
        return web.json_response({"error": "Missing agent_id."}, status=400)

    if agent_id == "main":
        handle = kernel.registry.start_main()
        return web.json_response({"agent": _agent_payload_for_handle(handle, locale=locale)})

    if kernel.external_agent_bridge.is_external_agent(agent_id):
        return web.json_response(
            {"error": f"External agent does not support activation: {agent_id}"},
            status=400,
        )

    if not agent_id.startswith("proj:"):
        return web.json_response(
            {"error": f"Unsupported agent_id: {agent_id}"},
            status=400,
        )

    project_id = agent_id.split(":", 1)[1]
    project = kernel.project_store.get_project(project_id)
    if project is None:
        return web.json_response(
            {"error": f"Project not found for agent: {agent_id}"},
            status=404,
        )

    handle = kernel.registry.activate_project(project, interactive=True)
    template_i18n_by_name = _template_i18n_map(kernel)
    payload = _agent_payload_for_handle(
        handle,
        locale=locale,
        display_name=_localized_text(
            fallback=project.name,
            locale=locale,
            i18n_map=template_i18n_by_name.get(project.hub_template or ""),
            field="name",
        ),
        avatar=_resolve_template_avatar(project.hub_template, _template_avatar_map(kernel)),
    )
    payload["hub_template"] = project.hub_template
    return web.json_response({"agent": payload})


async def handle_get_publish_draft(request: web.Request) -> web.Response:
    kernel = request.app["kernel"]
    agent_id = request.match_info.get("agent_id", "").strip()
    if not agent_id:
        return web.json_response({"error": "Missing agent_id."}, status=400)
    if agent_id == "main":
        return web.json_response(
            {"error": "Main agent cannot be published to agent hub."},
            status=400,
        )
    if not agent_id.startswith("proj:"):
        return web.json_response(
            {"error": f"Unsupported agent_id: {agent_id}"},
            status=400,
        )

    handle = kernel.registry.get(agent_id)
    if handle is None or handle.agent_type != "project" or handle.status != AgentStatus.RUNNING:
        return web.json_response(
            {"error": "Only active project agents can be published."},
            status=409,
        )

    project_id = agent_id.split(":", 1)[1]
    project = kernel.project_store.get_project(project_id)
    if project is None:
        return web.json_response(
            {"error": f"Project not found for agent: {agent_id}"},
            status=404,
        )

    return web.json_response(
        {
            "agent_id": agent_id,
            "template": _publish_draft_payload(project, kernel.config.data_path),
        }
    )


async def handle_publish_agent_to_hub(request: web.Request) -> web.Response:
    kernel = request.app["kernel"]
    agent_id = request.match_info.get("agent_id", "").strip()
    if not agent_id:
        return web.json_response({"error": "Missing agent_id."}, status=400)
    if agent_id == "main":
        return web.json_response(
            {"error": "Main agent cannot be published to agent hub."},
            status=400,
        )
    if not agent_id.startswith("proj:"):
        return web.json_response(
            {"error": f"Unsupported agent_id: {agent_id}"},
            status=400,
        )

    handle = kernel.registry.get(agent_id)
    if handle is None or handle.agent_type != "project" or handle.status != AgentStatus.RUNNING:
        return web.json_response(
            {"error": "Only active project agents can be published."},
            status=409,
        )

    project_id = agent_id.split(":", 1)[1]
    project = kernel.project_store.get_project(project_id)
    if project is None:
        return web.json_response(
            {"error": f"Project not found for agent: {agent_id}"},
            status=404,
        )

    try:
        payload = await request.json() if request.can_read_body else {}
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    raw_name = payload.get("name")
    name = str(raw_name).strip() if raw_name is not None else project.name
    description_raw = payload.get("description")
    description = (
        str(description_raw).strip()
        if description_raw is not None
        else project.description
    )

    tags = _normalize_string_list(payload.get("tags")) if "tags" in payload else list(project.tags)
    goals = (
        _normalize_string_list(payload.get("goals"))
        if "goals" in payload
        else _project_goals_list(project)
    )
    soul_raw = payload.get("soul")
    soul = str(soul_raw) if soul_raw is not None else None

    try:
        if soul is not None:
            workspace_dir = _project_workspace_dir(project, kernel.config.data_path)
            workspace_dir.mkdir(parents=True, exist_ok=True)
            (workspace_dir / SOUL_MARKDOWN_NAME).write_text(soul, encoding="utf-8")

        published = kernel.agent_hub_store.publish_from_project(
            project,
            kernel.config.data_path,
            template_name=name,
            description=description,
            tags=tags,
            goals=goals,
            equipment_refs=[],
        )
    except ValueError as exc:
        msg = str(exc)
        lowered = msg.lower()
        status = 409 if ("already exists" in lowered or "already" in lowered) else 400
        return web.json_response({"error": msg}, status=status)
    except OSError as exc:
        return web.json_response({"error": f"Failed to publish template: {exc}"}, status=500)

    skill_count = 0
    if published.skills_dir.is_dir():
        skill_count = sum(
            1 for e in published.skills_dir.iterdir()
            if e.is_dir() and (e / "SKILL.md").is_file()
        )

    return web.json_response(
        {
            "message": f"Published project '{project.name}' as hub template '{published.name}'.",
            "template": {
                "name": published.name,
                "description": published.description,
                "tags": published.tags,
                "goals": published.goals,
                "equipment_refs": published.equipment_refs,
                "avatar": published.avatar,
                "skill_count": skill_count,
            },
        },
        status=201,
    )


async def handle_put_config(request: web.Request) -> web.Response:
    kernel = request.app["kernel"]
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    if "external_agents" not in payload:
        existing = kernel.config.model_dump(by_alias=True)
        payload["external_agents"] = existing.get("external_agents", [])

    try:
        config = DrClawConfig.model_validate(payload)
    except ValidationError as exc:
        return web.json_response(
            {
                "error": "Invalid configuration",
                "details": exc.errors(),
            },
            status=400,
        )

    config_path = request.app.get("config_path", kernel.config.data_path / "config.json")
    try:
        save_config(config, config_path)
    except OSError as exc:
        return web.json_response(
            {"error": f"Failed to persist config: {exc}"},
            status=500,
        )

    kernel.config = config
    kernel.external_agent_bridge.reload_config(config)
    return web.json_response({
        "config": config.model_dump(by_alias=True),
        "restart_required": True,
    })


async def handle_list_skills(request: web.Request) -> web.Response:
    locale = _request_locale(request)
    skills = [_public_skill_summary(item) for item in _list_skill_summaries(request, locale=locale)]
    return web.json_response({"skills": skills})


async def handle_get_skill_content(request: web.Request) -> web.Response:
    locale = _request_locale(request)
    skill_ref = request.match_info.get("name", "").strip()
    if not skill_ref:
        return web.json_response({"error": "Missing skill reference"}, status=400)

    skill_summary = _resolve_skill_summary(request, skill_ref, locale=locale)
    if skill_summary is None:
        return web.json_response({"error": f"Skill not found: {skill_ref}"}, status=404)

    skill_file = Path(skill_summary["path"])
    if not skill_file.is_file():
        return web.json_response({"error": f"Skill not found: {skill_ref}"}, status=404)
    content = skill_file.read_text(encoding="utf-8")
    response = {
        "id": skill_summary["id"],
        "name": skill_summary["name"],
        "display_name": skill_summary.get("display_name", skill_summary["name"]),
        "source": skill_summary["source"],
        "source_label": skill_summary["source_label"],
        "used_by_agent": skill_summary["used_by_agent"],
        "used_by_agents": skill_summary["used_by_agents"],
        "used_by_agents_display": skill_summary.get(
            "used_by_agents_display",
            skill_summary["used_by_agents"],
        ),
        "description": skill_summary.get("description", ""),
        "display_description": skill_summary.get(
            "display_description",
            skill_summary.get("description", ""),
        ),
        "tags": skill_summary.get("tags", []),
        "content": content,
    }
    warning = skill_summary.get("warning")
    if isinstance(warning, str) and warning:
        response["warning"] = warning
    return web.json_response(response)


def _save_uploaded_skill_zip(request: web.Request, zip_path: Path, filename: str) -> dict[str, str]:
    existing_names = {
        s["name"]
        for s in _list_skill_summaries(request, locale=DEFAULT_WEB_LOCALE)
        if s["source"] == "global"
    }

    kernel = request.app["kernel"]
    global_skills_dir = kernel.config.data_path / "skills"
    global_skills_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="drclaw-skill-upload-") as tmp_dir:
        extract_root = Path(tmp_dir)
        with zipfile.ZipFile(zip_path, "r") as zf:
            _safe_extract_zip(zf, extract_root)

        skill_root, inferred_name = _resolve_uploaded_skill_root(extract_root, filename)
        normalized_name = slugify(inferred_name).replace("_", "-")
        if not normalized_name:
            raise ValueError("Could not infer a valid skill name from uploaded ZIP.")

        if normalized_name in existing_names:
            raise SkillUploadConflictError(
                f"Skill {normalized_name!r} already exists. Rename the skill and try again.",
            )

        target_dir = global_skills_dir / normalized_name
        if target_dir.exists():
            raise SkillUploadConflictError(
                f"Skill directory {normalized_name!r} already exists. "
                "Rename the skill and try again.",
            )
        shutil.copytree(skill_root, target_dir)

    skill_file = target_dir / SKILL_MARKDOWN_NAME
    if not skill_file.is_file():
        raise ValueError("Uploaded ZIP did not produce a valid SKILL.md.")
    content = skill_file.read_text(encoding="utf-8")
    return {
        "id": _make_skill_id("global", normalized_name),
        "name": normalized_name,
        "display_name": normalized_name,
        "source": "global",
        "source_label": SKILL_SOURCE_LABEL["global"],
        "path": str(skill_file),
        "description": _skill_description_from_content(content, normalized_name),
        "display_description": _skill_description_from_content(content, normalized_name),
        "tags": _skill_tags_from_content(content),
    }


async def handle_upload_skill(request: web.Request) -> web.Response:
    try:
        reader = await request.multipart()
    except ValueError:
        return web.json_response({"error": "Request must be multipart/form-data."}, status=400)

    file_part = None
    while True:
        part = await reader.next()
        if part is None:
            break
        if getattr(part, "name", None) == "file":
            file_part = part
            break

    if file_part is None:
        return web.json_response({"error": "Missing 'file' field in upload form."}, status=400)

    filename = (file_part.filename or "").strip()
    if not filename:
        return web.json_response({"error": "Uploaded file is missing a filename."}, status=400)
    if not filename.lower().endswith(".zip"):
        return web.json_response({"error": "Only .zip skill uploads are supported."}, status=400)

    with tempfile.NamedTemporaryFile(prefix="drclaw-skill-", suffix=".zip", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        while True:
            chunk = await file_part.read_chunk()
            if not chunk:
                break
            tmp.write(chunk)

    try:
        skill = _save_uploaded_skill_zip(request, tmp_path, filename)
    except SkillUploadConflictError as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except (ValueError, zipfile.BadZipFile) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except OSError as exc:
        return web.json_response({"error": f"Failed to save skill: {exc}"}, status=500)
    finally:
        tmp_path.unlink(missing_ok=True)

    return web.json_response(
        {
            "message": f"Uploaded skill '{skill['name']}' successfully.",
            "skill": skill,
        },
        status=201,
    )


def _chat_file_allowed(filename: str) -> bool:
    suffix = Path(filename).suffix.lower()
    return bool(suffix) and suffix in _CHAT_ALLOWED_EXTENSIONS


async def handle_upload_chat_files(request: web.Request) -> web.Response:
    try:
        reader = await request.multipart()
    except ValueError:
        return web.json_response({"error": "Request must be multipart/form-data."}, status=400)

    kernel = request.app["kernel"]
    store = ChatFileStore(kernel.config.data_path)

    uploaded: list[dict[str, Any]] = []
    total_bytes = 0
    count = 0

    while True:
        part = await reader.next()
        if part is None:
            break
        if getattr(part, "name", None) != "files":
            continue

        count += 1
        if count > _CHAT_FILES_MAX_COUNT:
            return web.json_response(
                {"error": f"Too many files. Max {_CHAT_FILES_MAX_COUNT} files per upload."},
                status=400,
            )

        filename = (part.filename or "").strip()
        if not filename:
            return web.json_response({"error": "Uploaded file is missing a filename."}, status=400)
        if not _chat_file_allowed(filename):
            return web.json_response(
                {
                    "error": (
                        f"Unsupported file type for '{filename}'. "
                        "Allowed types: txt, md, json, yaml, yml, csv, py, js, ts, tsx, "
                        "html, css, pdf, docx, png, jpg, jpeg, gif, webp, svg, zip."
                    )
                },
                status=400,
            )

        with tempfile.NamedTemporaryFile(prefix="drclaw-chat-upload-", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            file_bytes = 0
            while True:
                chunk = await part.read_chunk()
                if not chunk:
                    break
                file_bytes += len(chunk)
                total_bytes += len(chunk)
                if file_bytes > _CHAT_FILE_MAX_BYTES:
                    tmp_path.unlink(missing_ok=True)
                    return web.json_response(
                        {
                            "error": (
                                f"File '{filename}' exceeds max size "
                                f"{_CHAT_FILE_MAX_BYTES // (1024 * 1024)}MB."
                            )
                        },
                        status=400,
                    )
                if total_bytes > _CHAT_UPLOAD_MAX_TOTAL_BYTES:
                    tmp_path.unlink(missing_ok=True)
                    return web.json_response(
                        {
                            "error": (
                                "Total upload exceeds max size "
                                f"{_CHAT_UPLOAD_MAX_TOTAL_BYTES // (1024 * 1024)}MB."
                            )
                        },
                        status=400,
                    )
                tmp.write(chunk)

        try:
            rec = store.save_upload(
                source_path=tmp_path,
                filename=filename,
                content_type=part.headers.get("Content-Type"),
            )
            uploaded.append(store.public_descriptor(rec))
        except OSError as exc:
            return web.json_response({"error": f"Failed to save upload: {exc}"}, status=500)
        finally:
            tmp_path.unlink(missing_ok=True)

    if not uploaded:
        return web.json_response({"error": "Missing 'files' fields in upload form."}, status=400)

    return web.json_response({"files": uploaded}, status=201)


async def handle_download_chat_file(request: web.Request) -> web.StreamResponse:
    kernel = request.app["kernel"]
    file_id = request.match_info.get("file_id", "").strip().lower()
    if not file_id:
        raise web.HTTPNotFound()

    store = ChatFileStore(kernel.config.data_path)
    rec = store.get(file_id)
    if rec is None:
        raise web.HTTPNotFound()

    resp = web.FileResponse(rec.path)
    escaped = rec.name.replace("\\", "_").replace("\"", "")
    resp.headers["Content-Disposition"] = f'attachment; filename="{escaped}"'
    if rec.mime:
        resp.content_type = rec.mime
    return resp


async def handle_asset(request: web.Request) -> web.StreamResponse:
    kernel = request.app["kernel"]
    filename = request.match_info.get("filename", "")
    if not filename:
        raise web.HTTPNotFound()

    candidate_dirs = [
        (kernel.config.data_path / "assets").resolve(),
        BUNDLED_ASSETS_DIR.resolve(),
    ]
    path: Path | None = None
    for assets_dir in candidate_dirs:
        candidate = (assets_dir / filename).resolve()
        if not candidate.is_relative_to(assets_dir):
            continue
        if candidate.exists() and candidate.is_file():
            path = candidate
            break
    if path is None:
        raise web.HTTPNotFound()

    resp = web.FileResponse(path)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


async def handle_list_hub_templates(request: web.Request) -> web.Response:
    kernel = request.app["kernel"]
    locale = _request_locale(request)
    data_dir = kernel.config.data_path
    hub = kernel.agent_hub_store
    projects = kernel.project_store.list_projects()
    activated = {p.hub_template for p in projects if p.hub_template}

    templates = []
    for t in hub.list():
        template_i18n = getattr(t, "i18n", {})
        skill_count = 0
        if t.skills_dir.is_dir():
            skill_count = sum(
                1 for e in t.skills_dir.iterdir()
                if e.is_dir() and (e / "SKILL.md").is_file()
            )
        entry: dict = {
            "name": t.name,
            "display_name": _localized_text(
                fallback=t.name,
                locale=locale,
                i18n_map=template_i18n,
                field="name",
            ),
            "description": t.description,
            "display_description": _localized_text(
                fallback=t.description,
                locale=locale,
                i18n_map=template_i18n,
                field="description",
            ),
            "tags": t.tags,
            "goals": t.goals,
            "equipment_refs": t.equipment_refs,
            "avatar": _normalize_avatar_for_web(t.avatar, data_dir),
            "skill_count": skill_count,
            "activated": t.name in activated,
        }
        templates.append(entry)
    return web.json_response({"templates": templates})


async def handle_get_hub_template(request: web.Request) -> web.Response:
    kernel = request.app["kernel"]
    locale = _request_locale(request)
    data_dir = kernel.config.data_path
    hub = kernel.agent_hub_store
    name = request.match_info.get("name", "").strip()
    if not name:
        return web.json_response({"error": "Missing template name"}, status=400)

    template = hub.get(name)
    if template is None:
        return web.json_response({"error": f"Template not found: {name}"}, status=404)
    template_i18n = getattr(template, "i18n", {})

    soul_content = ""
    soul_file = template.root_dir / "SOUL.md"
    if soul_file.is_file():
        soul_content = soul_file.read_text(encoding="utf-8")

    skills: list[dict] = []
    if template.skills_dir.is_dir():
        for entry in sorted(template.skills_dir.iterdir(), key=lambda p: p.name):
            if not entry.is_dir():
                continue
            skill_file = entry / "SKILL.md"
            if skill_file.is_file():
                content = skill_file.read_text(encoding="utf-8")
                frontmatter = _skill_frontmatter(content)
                i18n_map = _skill_i18n_map(frontmatter)
                default_name = _skill_name_from_frontmatter(frontmatter, entry.name)
                description = _skill_description_from_frontmatter(frontmatter, entry.name)
                skills.append({
                    "name": entry.name,
                    "display_name": _localized_text(
                        fallback=default_name,
                        locale=locale,
                        i18n_map=i18n_map,
                        field="name",
                    ),
                    "description": description,
                    "display_description": _localized_text(
                        fallback=description,
                        locale=locale,
                        i18n_map=i18n_map,
                        field="description",
                    ),
                })

    projects = kernel.project_store.list_projects()
    activated_projects = [
        {
            "id": p.id,
            "name": p.name,
            "display_name": _localized_text(
                fallback=p.name,
                locale=locale,
                i18n_map=template_i18n,
                field="name",
            ),
        }
        for p in projects
        if p.hub_template == name
    ]

    return web.json_response({
        "name": template.name,
        "display_name": _localized_text(
            fallback=template.name,
            locale=locale,
            i18n_map=template_i18n,
            field="name",
        ),
        "description": template.description,
        "display_description": _localized_text(
            fallback=template.description,
            locale=locale,
            i18n_map=template_i18n,
            field="description",
        ),
        "tags": template.tags,
        "goals": template.goals,
        "equipment_refs": template.equipment_refs,
        "avatar": _normalize_avatar_for_web(template.avatar, data_dir),
        "soul": soul_content,
        "skills": skills,
        "activated_projects": activated_projects,
    })


async def handle_activate_hub_template(request: web.Request) -> web.Response:
    kernel = request.app["kernel"]
    locale = _request_locale(request)
    data_dir = kernel.config.data_path
    hub = kernel.agent_hub_store
    name = request.match_info.get("name", "").strip()
    if not name:
        return web.json_response({"error": "Missing template name"}, status=400)
    template = hub.get(name)
    if template is None:
        return web.json_response({"error": f"Template not found: {name}"}, status=404)
    template_i18n = getattr(template, "i18n", {})

    try:
        project = hub.activate(name, kernel.project_store, kernel.config.data_path)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    return web.json_response({
        "message": f"Activated template '{name}' as project '{project.name}'.",
        "project": {
            "id": project.id,
            "name": project.name,
            "display_name": _localized_text(
                fallback=project.name,
                locale=locale,
                i18n_map=template_i18n,
                field="name",
            ),
            "description": project.description,
            "display_description": _localized_text(
                fallback=project.description,
                locale=locale,
                i18n_map=template_i18n,
                field="description",
            ),
            "hub_template": project.hub_template,
            "avatar": _normalize_avatar_for_web(template.avatar, data_dir),
        },
    }, status=201)


async def handle_external_callback(request: web.Request) -> web.Response:
    kernel = request.app["kernel"]
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    status, body = await kernel.external_agent_bridge.handle_callback(payload)
    return web.json_response(body, status=status)


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    adapter: WebAdapter = request.app["adapter"]
    kernel = request.app["kernel"]
    file_store = ChatFileStore(kernel.config.data_path)
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    chat_id = adapter.register_connection(ws)

    try:
        async for raw in ws:
            if raw.type == WSMsgType.TEXT:
                try:
                    data = json.loads(raw.data)
                except json.JSONDecodeError:
                    await ws.send_json({"type": "error", "text": "Invalid JSON"})
                    continue

                msg_type = data.get("type")
                if msg_type != "chat":
                    await ws.send_json({"type": "error", "text": f"Unknown type: {msg_type}"})
                    continue

                agent_id_raw = data.get("agent_id")
                agent_id = (
                    agent_id_raw.strip()
                    if isinstance(agent_id_raw, str)
                    else str(agent_id_raw).strip() if agent_id_raw is not None else ""
                )
                text_raw = data.get("text", "")
                if isinstance(text_raw, str):
                    text = text_raw.strip()
                elif text_raw is None:
                    text = ""
                else:
                    text = str(text_raw).strip()
                files_raw = data.get("files", [])
                metadata = data.get("metadata", {})
                if not isinstance(metadata, dict):
                    metadata = {}
                if not isinstance(files_raw, list):
                    await ws.send_json({"type": "error", "text": "Invalid files payload"})
                    continue

                file_ids: list[str] = []
                for item in files_raw:
                    file_id = _normalize_attachment_id(item)
                    if not file_id:
                        continue
                    if file_id not in file_ids:
                        file_ids.append(file_id)
                if len(file_ids) > _CHAT_FILES_MAX_COUNT:
                    await ws.send_json(
                        {
                            "type": "error",
                            "text": (
                                f"Too many files in message. "
                                f"Max {_CHAT_FILES_MAX_COUNT} files."
                            ),
                        }
                    )
                    continue

                try:
                    attachments = file_store.resolve_ids_for_agent(file_ids)
                except ValueError as exc:
                    await ws.send_json({"type": "error", "text": str(exc)})
                    continue
                if attachments:
                    metadata["attachments"] = attachments

                if not agent_id or (not text and not attachments):
                    await ws.send_json(
                        {
                            "type": "error",
                            "text": "Missing agent_id and message content (text or files).",
                        }
                    )
                    continue

                if kernel.external_agent_bridge.is_external_agent(agent_id):
                    if attachments:
                        await ws.send_json(
                            {
                                "type": "error",
                                "text": (
                                    "External agents currently support text-only messages. "
                                    "Remove files and try again."
                                ),
                            }
                        )
                        continue
                    try:
                        await kernel.external_agent_bridge.submit_from_web(
                            agent_id=agent_id,
                            text=text,
                            metadata=metadata,
                            chat_id=chat_id,
                        )
                    except Exception as exc:
                        await ws.send_json(
                            {
                                "type": "error",
                                "text": f"External agent request failed: {exc}",
                            }
                        )
                    continue

                handle = kernel.registry.get(agent_id)
                if handle is None or handle.status != AgentStatus.RUNNING:
                    if agent_id == "main":
                        handle = kernel.registry.start_main()
                    elif agent_id.startswith("proj:"):
                        project_id = agent_id.split(":", 1)[1]
                        project = kernel.project_store.get_project(project_id)
                        if project is None:
                            await ws.send_json({
                                "type": "error",
                                "text": f"Agent not found or not running: {agent_id}",
                            })
                            continue
                        handle = kernel.registry.activate_project(project, interactive=True)
                    else:
                        await ws.send_json({
                            "type": "error",
                            "text": f"Agent not found or not running: {agent_id}",
                        })
                        continue

                await kernel.bus.publish_inbound(
                    InboundMessage(
                        channel="web",
                        chat_id=chat_id,
                        text=text,
                        metadata=metadata,
                    ),
                    topic=agent_id,
                )
            elif raw.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    finally:
        adapter.remove_connection(chat_id)

    return ws
