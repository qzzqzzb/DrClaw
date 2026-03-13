"""AgentHubStore — discovers and activates agent hub templates."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from drclaw.models.project import Project, ProjectStore
from drclaw.utils.helpers import slugify


@dataclass(frozen=True)
class AgentHubTemplate:
    """A single agent hub template definition."""

    name: str
    root_dir: Path
    skills_dir: Path
    description: str = ""
    tags: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    equipment_refs: list[str] = field(default_factory=list)
    avatar: str | None = None
    i18n: dict[str, dict[str, str]] = field(default_factory=dict)


AGENT_YAML = "AGENT.yaml"


def _normalize_i18n_map(raw: object) -> dict[str, dict[str, str]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for lang_raw, payload in raw.items():
        if not isinstance(lang_raw, str):
            continue
        lang = lang_raw.strip().lower()
        if not lang:
            continue
        if not isinstance(payload, dict):
            continue
        normalized_payload: dict[str, str] = {}
        for key in ("name", "description"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                normalized_payload[key] = value.strip()
        if normalized_payload:
            out[lang] = normalized_payload
    return out


def _parse_metadata(root_dir: Path) -> dict:
    """Read AGENT.yaml from a template directory."""
    meta_file = root_dir / AGENT_YAML
    if not meta_file.is_file():
        return {}
    try:
        data = yaml.safe_load(meta_file.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _build_template(root_dir: Path) -> AgentHubTemplate | None:
    """Build a template from a directory, or None if invalid."""
    if not root_dir.is_dir():
        return None
    meta = _parse_metadata(root_dir)
    name = meta.get("name", root_dir.name)
    avatar = meta.get("avatar")
    if not isinstance(avatar, str):
        avatar = None
    elif not avatar.strip():
        avatar = None
    return AgentHubTemplate(
        name=name,
        root_dir=root_dir,
        skills_dir=root_dir / "skills",
        description=meta.get("description", ""),
        tags=meta.get("tags", []) or [],
        goals=meta.get("goals", []) or [],
        equipment_refs=meta.get("equipment_refs", []) or [],
        avatar=avatar,
        i18n=_normalize_i18n_map(meta.get("i18n")),
    )


class AgentHubStore:
    """Manages agent hub templates: discovery, listing, and activation."""

    def __init__(self, hub_dir: Path, bundled_dir: Path | None = None) -> None:
        self.hub_dir = hub_dir
        self.bundled_dir = bundled_dir or Path(__file__).parent / "templates"
        if not self.hub_dir.exists():
            self._seed()

    def _seed(self) -> None:
        """Copy bundled templates to hub_dir on first use."""
        self.hub_dir.mkdir(parents=True, exist_ok=True)
        if not self.bundled_dir.is_dir():
            return
        for entry in self.bundled_dir.iterdir():
            if not entry.is_dir():
                continue
            dest = self.hub_dir / entry.name
            shutil.copytree(entry, dest)

    def list(self) -> list[AgentHubTemplate]:
        if not self.hub_dir.is_dir():
            return []
        out: list[AgentHubTemplate] = []
        for entry in sorted(self.hub_dir.iterdir(), key=lambda p: p.name):
            t = _build_template(entry)
            if t is not None:
                out.append(t)
        return out

    def get(self, name: str) -> AgentHubTemplate | None:
        """Look up by YAML metadata name (scanning all templates)."""
        for t in self.list():
            if t.name == name:
                return t
        return None

    def activate(
        self,
        template_name: str,
        project_store: ProjectStore,
        data_dir: Path,
    ) -> Project:
        """Create a project from a hub template.

        Copies SOUL.md and skills into the new project workspace.
        Does NOT auto-spawn the agent — user activates from My Agents.
        """
        template = self.get(template_name)
        if template is None:
            raise ValueError(f"Hub template not found: {template_name!r}")

        project = project_store.create_project(
            name=template.name,
            description=template.description,
        )

        # Set goals, tags, hub_template on the project.
        project.goals = "\n".join(template.goals)
        project.tags = list(template.tags)
        project.hub_template = template_name
        project_store.update_project(project)

        # Copy SOUL.md if template provides one.
        workspace = data_dir / "projects" / project.id / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        template_soul = template.root_dir / "SOUL.md"
        if template_soul.is_file():
            shutil.copy2(template_soul, workspace / "SOUL.md")

        # Copy skills if template provides them.
        if template.skills_dir.is_dir() and any(template.skills_dir.iterdir()):
            dest_skills = workspace / "skills"
            dest_skills.mkdir(parents=True, exist_ok=True)
            for skill_entry in template.skills_dir.iterdir():
                if skill_entry.is_dir():
                    shutil.copytree(
                        skill_entry,
                        dest_skills / skill_entry.name,
                        dirs_exist_ok=True,
                    )

        return project

    def publish_from_project(
        self,
        project: Project,
        data_dir: Path,
        *,
        template_name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        goals: list[str] | None = None,
        equipment_refs: list[str] | None = None,
    ) -> AgentHubTemplate:
        """Publish a project agent snapshot as a hub template.

        Snapshot includes:
        - AGENT.yaml metadata
        - workspace/SOUL.md (if present)
        - merged active skills: global + project-local (project-local wins by name)
        """
        name = (template_name or project.name).strip()
        if not name:
            raise ValueError("Template name cannot be empty.")
        if self.get(name) is not None:
            raise ValueError(f"Hub template {name!r} already exists.")

        slug = slugify(name)
        if not slug:
            raise ValueError(f"Invalid template name: {name!r}")

        self.hub_dir.mkdir(parents=True, exist_ok=True)
        template_dir = self.hub_dir / slug
        if template_dir.exists():
            raise ValueError(
                f"Hub template folder already exists: {template_dir.name!r}. "
                "Use a different template name."
            )

        project_workspace = data_dir / "projects" / project.id / "workspace"
        global_skills_root = data_dir / "skills"
        project_skills_root = project_workspace / "skills"

        publish_description = (
            str(description).strip()
            if description is not None
            else (project.description or "").strip()
        )
        publish_tags = [str(t).strip() for t in (tags if tags is not None else project.tags)]
        publish_tags = [t for t in publish_tags if t]
        if goals is not None:
            publish_goals = [str(g).strip() for g in goals if str(g).strip()]
        else:
            publish_goals = [line.strip() for line in project.goals.splitlines() if line.strip()]
        publish_equipment_refs = [str(e).strip() for e in (equipment_refs or []) if str(e).strip()]

        metadata = {
            "name": name,
            "description": publish_description,
            "tags": publish_tags,
            "goals": publish_goals,
            "equipment_refs": publish_equipment_refs,
        }

        try:
            template_dir.mkdir(parents=True, exist_ok=False)
            (template_dir / AGENT_YAML).write_text(
                yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )

            source_soul = project_workspace / "SOUL.md"
            if source_soul.is_file():
                shutil.copy2(source_soul, template_dir / "SOUL.md")

            merged_skills: dict[str, Path] = {}

            if global_skills_root.is_dir():
                for skill_dir in sorted(global_skills_root.iterdir(), key=lambda p: p.name):
                    if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file():
                        merged_skills[skill_dir.name] = skill_dir

            if project_skills_root.is_dir():
                for skill_dir in sorted(project_skills_root.iterdir(), key=lambda p: p.name):
                    if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file():
                        merged_skills[skill_dir.name] = skill_dir

            if merged_skills:
                dest_skills = template_dir / "skills"
                dest_skills.mkdir(parents=True, exist_ok=True)
                for skill_name, skill_src in merged_skills.items():
                    shutil.copytree(skill_src, dest_skills / skill_name, dirs_exist_ok=False)
        except Exception:
            shutil.rmtree(template_dir, ignore_errors=True)
            raise

        published = _build_template(template_dir)
        if published is None:
            raise ValueError(f"Failed to publish template {name!r}.")
        return published
