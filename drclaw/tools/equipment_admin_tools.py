"""Main-agent tools for managing equipment prototypes."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from drclaw.skills.local_hub import LocalHubSkill, LocalSkillHubStore
from drclaw.tools.base import Tool
from drclaw.utils.helpers import slugify

if TYPE_CHECKING:
    from drclaw.equipment.manager import EquipmentRuntimeManager
    from drclaw.equipment.prototypes import EquipmentPrototypeStore
    from drclaw.models.project import ProjectStore


def _equipment_not_found_message(store: EquipmentPrototypeStore, equipment_name: str) -> str:
    available = [p.name for p in store.list()]
    if available:
        return (
            f"Error: equipment {equipment_name!r} not found. "
            f"Available: {', '.join(available)}"
        )
    return f"Error: equipment {equipment_name!r} not found. No equipments exist yet."


def _project_not_found_message(store: ProjectStore, project_id: str) -> str:
    available = [p.id for p in store.list_projects()]
    if available:
        return (
            f"Error: project {project_id!r} not found. "
            f"Available: {', '.join(available)}"
        )
    return f"Error: project {project_id!r} not found. No projects exist yet."


def _dedupe_normalized_skill_refs(
    refs: list[str], normalize_ref: Callable[[str], str],
) -> tuple[list[str], str | None]:
    """Normalize and deduplicate skill refs while preserving order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in refs:
        try:
            name = normalize_ref(raw)
        except Exception as exc:
            return [], f"Error: invalid local hub skill ref {raw!r}: {exc}"
        if name in seen:
            continue
        seen.add(name)
        normalized.append(name)
    return normalized, None


def _copy_skill_tree(source_dir: Path, target_dir: Path, *, overwrite: bool = False) -> None:
    """Copy one skill directory recursively."""
    if target_dir.exists():
        if not overwrite:
            raise ValueError(f"Skill directory already exists: {target_dir}")
        shutil.rmtree(target_dir)
    shutil.copytree(source_dir, target_dir)


def _resolve_local_hub_skills(
    local_hub: LocalSkillHubStore,
    refs: list[str],
) -> tuple[list[LocalHubSkill], str | None]:
    """Resolve hub refs to skill entries."""
    resolved: list[LocalHubSkill] = []
    missing: list[str] = []
    for ref in refs:
        skill = local_hub.get(ref)
        if skill is None:
            missing.append(ref)
            continue
        resolved.append(skill)
    if missing:
        return (
            [],
            "Error: local hub skills not found: "
            + ", ".join(missing)
            + ". Use import_skill_to_local_hub first.",
        )
    return resolved, None


def _compute_destination_names(
    hub_skills: list[LocalHubSkill],
    *,
    starter_skill_name: str | None = None,
) -> tuple[dict[str, str], str | None]:
    """Map hub refs to destination equipment skill names (flat leaf names)."""
    dest_by_ref: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for skill in hub_skills:
        dest_name = skill.name
        existing_ref = by_name.get(dest_name)
        if existing_ref is not None and existing_ref != skill.ref:
            return {}, (
                f"Error: local hub skills {existing_ref!r} and {skill.ref!r} both map to "
                f"equipment skill name {dest_name!r}. Rename one skill or copy them separately."
            )
        if starter_skill_name is not None and dest_name == starter_skill_name:
            return {}, (
                f"Error: local hub skill {skill.ref!r} maps to starter skill name "
                f"{starter_skill_name!r}. Choose a different starter_skill_name or remove "
                "the conflicting hub skill."
            )
        by_name[dest_name] = skill.ref
        dest_by_ref[skill.ref] = dest_name
    return dest_by_ref, None


class AddEquipmentTool(Tool):
    """Create a new equipment prototype under ~/.drclaw/equipments."""

    def __init__(
        self,
        prototype_store: EquipmentPrototypeStore,
        local_skill_hub: LocalSkillHubStore | None = None,
    ) -> None:
        self._store = prototype_store
        self._local_skill_hub = local_skill_hub

    @property
    def name(self) -> str:
        return "add_equipment"

    @property
    def description(self) -> str:
        return "Create a new equipment prototype with a skills directory."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Equipment prototype name"},
                "description": {
                    "type": "string",
                    "description": "Optional description for the starter skill file.",
                },
                "create_starter_skill": {
                    "type": "boolean",
                    "description": "Create starter SKILL.md under skills/<starter_skill_name>/.",
                },
                "starter_skill_name": {
                    "type": "string",
                    "description": "Optional starter skill directory name (default: default).",
                },
                "local_hub_skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional local hub skill refs to copy into this equipment on creation. "
                        "Supports categories, e.g. science/math/arxiv-reader."
                    ),
                },
            },
            "required": ["name"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        name: str = params["name"]
        description: str = params.get("description", "")
        create_starter_skill = params.get("create_starter_skill", True)
        starter_skill_name: str = params.get("starter_skill_name", "default")
        local_hub_skills: list[str] = params.get("local_hub_skills", [])
        starter_slug = slugify(starter_skill_name).replace("_", "-") or "default"

        if local_hub_skills and self._local_skill_hub is None:
            return "Error: local skill hub is not configured."

        normalized_hub_refs: list[str] = []
        hub_skills: list[LocalHubSkill] = []
        dest_by_ref: dict[str, str] = {}
        if local_hub_skills:
            normalized_hub_refs, error = _dedupe_normalized_skill_refs(
                local_hub_skills, self._local_skill_hub.normalize_skill_ref
            )
            if error:
                return error

            hub_skills, error = _resolve_local_hub_skills(
                self._local_skill_hub, normalized_hub_refs
            )
            if error:
                return error
            dest_by_ref, error = _compute_destination_names(
                hub_skills,
                starter_skill_name=starter_slug if create_starter_skill else None,
            )
            if error:
                return error

        try:
            prototype = self._store.create(name)
        except Exception as exc:
            return f"Error: failed to create equipment: {exc}"

        if create_starter_skill:
            skill_dir = prototype.skills_dir / starter_slug
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_file = skill_dir / "SKILL.md"
            skill_description = description.strip() or f"Starter skill for {prototype.name}."
            skill_file.write_text(
                (
                    "---\n"
                    f"name: {starter_slug}\n"
                    f"description: {skill_description}\n"
                    "---\n\n"
                    "# Instructions\n\n"
                    "1. Use `update_status` during long-running steps.\n"
                    "2. Call `report_to_caller` exactly once before finishing.\n"
                ),
                encoding="utf-8",
            )

        copied_skills: list[str] = []
        for hub_skill in hub_skills:
            destination_name = dest_by_ref[hub_skill.ref]
            try:
                _copy_skill_tree(hub_skill.root_dir, prototype.skills_dir / destination_name)
            except Exception as exc:
                return (
                    f"Error: failed to copy local hub skill {hub_skill.ref!r} into equipment "
                    f"{prototype.name!r}: {exc}"
                )
            copied_skills.append(f"{hub_skill.ref}->{destination_name}")

        message = (
            f"Created equipment {prototype.name!r} at {prototype.root_dir}. "
            f"Skills root: {prototype.skills_dir}"
        )
        if copied_skills:
            message += f". Seeded local hub skills: {', '.join(copied_skills)}"
        return message


class ListLocalSkillHubSkillsTool(Tool):
    """List skills stored in the local skill hub."""

    def __init__(self, local_skill_hub: LocalSkillHubStore) -> None:
        self._local_skill_hub = local_skill_hub

    @property
    def name(self) -> str:
        return "list_local_skill_hub_skills"

    @property
    def description(self) -> str:
        return "List skill templates stored in ~/.drclaw/local-skill-hub."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional category path filter, e.g. science/math.",
                },
            },
        }

    async def execute(self, params: dict[str, Any]) -> str:
        category: str | None = params.get("category")
        skills = self._local_skill_hub.list(category=category)
        if not skills:
            if category:
                return f"No local skill hub skills under category {category!r}."
            return "No local skill hub skills yet."
        lines = [f"Local skill hub ({self._local_skill_hub.root_dir}):"]
        for skill in skills:
            category_label = skill.category if skill.category else "(root)"
            lines.append(f"  - {skill.ref} (category={category_label})")
        return "\n".join(lines)


class ListLocalSkillHubCategoriesTool(Tool):
    """List local hub categories and optional metadata descriptions."""

    def __init__(self, local_skill_hub: LocalSkillHubStore) -> None:
        self._local_skill_hub = local_skill_hub

    @property
    def name(self) -> str:
        return "list_local_skill_hub_categories"

    @property
    def description(self) -> str:
        return (
            "List local skill hub categories (including unarchived) and their CATEGORY.md "
            "descriptions."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, params: dict[str, Any]) -> str:
        categories = self._local_skill_hub.list_categories()
        if not categories:
            return "No local skill hub categories yet."
        lines = [f"Local skill hub categories ({self._local_skill_hub.root_dir}):"]
        for item in categories:
            category = item["category"]
            desc = item["description"]
            if desc:
                lines.append(f"  - {category}: {desc}")
            else:
                lines.append(f"  - {category}")
        return "\n".join(lines)


class SetLocalSkillHubCategoryMetadataTool(Tool):
    """Create or update CATEGORY.md for a local hub category."""

    def __init__(self, local_skill_hub: LocalSkillHubStore) -> None:
        self._local_skill_hub = local_skill_hub

    @property
    def name(self) -> str:
        return "set_local_skill_hub_category_metadata"

    @property
    def description(self) -> str:
        return "Set CATEGORY.md description text for a local skill hub category."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Category path, e.g. search/arxiv or science/math.",
                },
                "description": {
                    "type": "string",
                    "description": "Meaning/purpose of this category.",
                },
            },
            "required": ["category", "description"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        category: str = params["category"]
        description: str = params["description"]
        try:
            path = self._local_skill_hub.set_category_metadata(category, description)
            normalized = self._local_skill_hub.normalize_category(
                category, default_unarchived=True
            )
        except Exception as exc:
            return f"Error: failed to update category metadata: {exc}"
        return f"Updated local skill hub category {normalized!r} metadata at {path}."


class ImportSkillToLocalHubTool(Tool):
    """Import a skill directory into the local skill hub."""

    def __init__(self, local_skill_hub: LocalSkillHubStore) -> None:
        self._local_skill_hub = local_skill_hub

    @property
    def name(self) -> str:
        return "import_skill_to_local_hub"

    @property
    def description(self) -> str:
        return (
            "Copy a local skill directory into ~/.drclaw/local-skill-hub for later reuse."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source_dir": {
                    "type": "string",
                    "description": "Path to a directory containing SKILL.md.",
                },
                "skill_name": {
                    "type": "string",
                    "description": "Optional target name in local hub (defaults to source folder).",
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Optional hierarchical category path, e.g. search/arxiv or science/math."
                    ),
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Replace existing local hub skill if already present.",
                },
            },
            "required": ["source_dir"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        source_dir = Path(params["source_dir"])
        skill_name: str | None = params.get("skill_name")
        category: str | None = params.get("category")
        overwrite: bool = params.get("overwrite", False)
        try:
            skill = self._local_skill_hub.import_skill(
                source_dir,
                skill_name=skill_name,
                category=category,
                overwrite=overwrite,
            )
        except Exception as exc:
            return f"Error: failed to import local hub skill: {exc}"
        return (
            f"Imported local hub skill {skill.ref!r} at {skill.root_dir}. "
            "This skill is stored only and is not auto-activated."
        )


class AddLocalHubSkillsToEquipmentTool(Tool):
    """Copy one or more local-hub skills into an existing equipment prototype."""

    def __init__(
        self,
        prototype_store: EquipmentPrototypeStore,
        local_skill_hub: LocalSkillHubStore,
    ) -> None:
        self._store = prototype_store
        self._local_skill_hub = local_skill_hub

    @property
    def name(self) -> str:
        return "add_local_hub_skills_to_equipment"

    @property
    def description(self) -> str:
        return (
            "Copy local hub skills into ~/.drclaw/equipments/<equipment>/skills so that "
            "equipment agents can use them."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "equipment_name": {"type": "string", "description": "Target equipment name"},
                "skill_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "One or more local hub skill refs to copy. Supports category paths, "
                        "e.g. search/arxiv-reader."
                    ),
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Replace existing equipment skill directories on conflict.",
                },
            },
            "required": ["equipment_name", "skill_names"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        equipment_name: str = params["equipment_name"]
        requested: list[str] = params["skill_names"]
        overwrite: bool = params.get("overwrite", False)

        if not requested:
            return "Error: skill_names must include at least one item."

        prototype = self._store.get(equipment_name)
        if prototype is None:
            return _equipment_not_found_message(self._store, equipment_name)

        skill_refs, error = _dedupe_normalized_skill_refs(
            requested, self._local_skill_hub.normalize_skill_ref
        )
        if error:
            return error

        hub_skills, error = _resolve_local_hub_skills(self._local_skill_hub, skill_refs)
        if error:
            return error
        dest_by_ref, error = _compute_destination_names(hub_skills)
        if error:
            return error

        conflicts = []
        for skill in hub_skills:
            name = dest_by_ref[skill.ref]
            if (prototype.skills_dir / name).exists():
                conflicts.append(name)
        if conflicts and not overwrite:
            return (
                "Error: equipment already has skill directories: "
                + ", ".join(sorted(set(conflicts)))
                + ". Set overwrite=true to replace them."
            )

        copied: list[str] = []
        for hub_skill in hub_skills:
            destination_name = dest_by_ref[hub_skill.ref]
            try:
                _copy_skill_tree(
                    hub_skill.root_dir,
                    prototype.skills_dir / destination_name,
                    overwrite=overwrite,
                )
            except Exception as exc:
                return f"Error: failed to copy skill {hub_skill.ref!r} into equipment: {exc}"
            copied.append(f"{hub_skill.ref}->{destination_name}")

        return (
            f"Added {len(copied)} local hub skill(s) to equipment {prototype.name!r}: "
            + ", ".join(copied)
        )


class AddLocalHubSkillsToProjectTool(Tool):
    """Copy local-hub skills into one project's workspace/skills directory."""

    def __init__(
        self,
        project_store: ProjectStore,
        projects_dir: Path,
        local_skill_hub: LocalSkillHubStore,
    ) -> None:
        self._project_store = project_store
        self._projects_dir = projects_dir
        self._local_skill_hub = local_skill_hub

    @property
    def name(self) -> str:
        return "add_local_hub_skills_to_project"

    @property
    def description(self) -> str:
        return (
            "Copy local hub skills into ~/.drclaw/projects/<project_id>/workspace/skills so "
            "the student agent can use them directly."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Target project id for the student agent.",
                },
                "skill_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "One or more local hub skill refs to copy. Supports category paths, "
                        "e.g. search/arxiv-reader."
                    ),
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Replace existing project skill directories on conflict.",
                },
            },
            "required": ["project_id", "skill_names"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        project_id: str = params["project_id"]
        requested: list[str] = params["skill_names"]
        overwrite: bool = params.get("overwrite", False)

        if not requested:
            return "Error: skill_names must include at least one item."

        project = self._project_store.get_project(project_id)
        if project is None:
            return _project_not_found_message(self._project_store, project_id)

        skill_refs, error = _dedupe_normalized_skill_refs(
            requested, self._local_skill_hub.normalize_skill_ref
        )
        if error:
            return error

        hub_skills, error = _resolve_local_hub_skills(self._local_skill_hub, skill_refs)
        if error:
            return error

        dest_by_ref: dict[str, str] = {}
        by_name: dict[str, str] = {}
        for skill in hub_skills:
            destination_name = skill.name
            existing_ref = by_name.get(destination_name)
            if existing_ref is not None and existing_ref != skill.ref:
                return (
                    f"Error: local hub skills {existing_ref!r} and {skill.ref!r} both map to "
                    f"project skill name {destination_name!r}. Rename one skill or copy "
                    "them separately."
                )
            by_name[destination_name] = skill.ref
            dest_by_ref[skill.ref] = destination_name

        skills_dir = self._projects_dir / project.id / "workspace" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        conflicts = []
        for skill in hub_skills:
            destination_name = dest_by_ref[skill.ref]
            if (skills_dir / destination_name).exists():
                conflicts.append(destination_name)
        if conflicts and not overwrite:
            return (
                "Error: project already has skill directories: "
                + ", ".join(sorted(set(conflicts)))
                + ". Set overwrite=true to replace them."
            )

        copied: list[str] = []
        for hub_skill in hub_skills:
            destination_name = dest_by_ref[hub_skill.ref]
            try:
                _copy_skill_tree(
                    hub_skill.root_dir,
                    skills_dir / destination_name,
                    overwrite=overwrite,
                )
            except Exception as exc:
                return f"Error: failed to copy skill {hub_skill.ref!r} into project: {exc}"
            copied.append(f"{hub_skill.ref}->{destination_name}")

        return (
            f"Added {len(copied)} local hub skill(s) to project {project.id!r}: "
            + ", ".join(copied)
        )


class ListEquipmentsTool(Tool):
    """List all available equipment prototypes."""

    def __init__(
        self,
        prototype_store: EquipmentPrototypeStore,
        runtime_manager: EquipmentRuntimeManager | None = None,
    ) -> None:
        self._store = prototype_store
        self._manager = runtime_manager

    @property
    def name(self) -> str:
        return "list_equipments"

    @property
    def description(self) -> str:
        return "List all equipment prototypes that can be used by agents."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, params: dict[str, Any]) -> str:
        prototypes = self._store.list()
        if not prototypes:
            return "No equipments yet."

        lines = ["Equipments:"]
        for p in prototypes:
            skill_count = sum(
                1 for skill in p.skills_dir.iterdir()
                if skill.is_dir() and (skill / "SKILL.md").is_file()
            )
            extra = ""
            if self._manager is not None:
                active_runs = self._manager.count_active_for_prototype(p.name)
                if active_runs:
                    extra = f", active_runs={active_runs}"
            lines.append(f"  - {p.name} (skills={skill_count}{extra})")
        return "\n".join(lines)


class RemoveEquipmentTool(Tool):
    """Delete an equipment prototype from local storage."""

    def __init__(
        self,
        prototype_store: EquipmentPrototypeStore,
        runtime_manager: EquipmentRuntimeManager | None = None,
    ) -> None:
        self._store = prototype_store
        self._manager = runtime_manager

    @property
    def name(self) -> str:
        return "remove_equipment"

    @property
    def description(self) -> str:
        return "Remove an equipment prototype and delete its local directory."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Equipment prototype name"},
                "force": {
                    "type": "boolean",
                    "description": "Allow removal even if there are active runs.",
                },
            },
            "required": ["name"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        name: str = params["name"]
        force: bool = params.get("force", False)

        active_runs = 0
        if self._manager is not None:
            active_runs = self._manager.count_active_for_prototype(name)
            if active_runs > 0 and not force:
                return (
                    f"Error: equipment {name!r} has {active_runs} active runs. "
                    "Use force=true to remove prototype definition anyway."
                )

        try:
            deleted = self._store.delete(name)
        except Exception as exc:
            return f"Error: failed to remove equipment: {exc}"
        if not deleted:
            return _equipment_not_found_message(self._store, name)

        suffix = ""
        if active_runs > 0:
            suffix = f" Active runs still in progress: {active_runs}."
        return f"Removed equipment {name!r} and deleted its prototype directory.{suffix}"
