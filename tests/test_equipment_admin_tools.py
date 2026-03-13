"""Tests for equipment prototype management tools."""

from __future__ import annotations

from pathlib import Path

from drclaw.equipment.prototypes import EquipmentPrototypeStore
from drclaw.models.project import JsonProjectStore
from drclaw.skills.local_hub import LocalSkillHubStore
from drclaw.tools.equipment_admin_tools import (
    AddEquipmentTool,
    AddLocalHubSkillsToEquipmentTool,
    AddLocalHubSkillsToProjectTool,
    ImportSkillToLocalHubTool,
    ListEquipmentsTool,
    ListLocalSkillHubCategoriesTool,
    ListLocalSkillHubSkillsTool,
    RemoveEquipmentTool,
    SetLocalSkillHubCategoryMetadataTool,
)


class _FakeRuntimeManager:
    def __init__(self, active_counts: dict[str, int] | None = None) -> None:
        self.active_counts = active_counts or {}

    def count_active_for_prototype(self, prototype_name: str) -> int:
        return self.active_counts.get(prototype_name, 0)


class TestEquipmentPrototypeStore:
    def test_create_and_delete(self, tmp_path: Path) -> None:
        store = EquipmentPrototypeStore(tmp_path / "equipments")
        prototype = store.create("Web Searcher")
        assert prototype.name == "web-searcher"
        assert prototype.skills_dir.is_dir()
        assert store.get("web-searcher") is not None

        assert store.delete("web-searcher") is True
        assert store.get("web-searcher") is None


class TestAddEquipmentTool:
    async def test_add_creates_starter_skill(self, tmp_path: Path) -> None:
        store = EquipmentPrototypeStore(tmp_path / "equipments")
        tool = AddEquipmentTool(store)

        result = await tool.execute(
            {
                "name": "Analyzer",
                "description": "Analyze experiment outputs",
            }
        )

        assert "Created equipment" in result
        assert store.get("analyzer") is not None
        skill_file = tmp_path / "equipments" / "analyzer" / "skills" / "default" / "SKILL.md"
        assert skill_file.is_file()
        assert "Analyze experiment outputs" in skill_file.read_text(encoding="utf-8")

    async def test_add_duplicate_returns_error(self, tmp_path: Path) -> None:
        store = EquipmentPrototypeStore(tmp_path / "equipments")
        store.create("analyzer")
        tool = AddEquipmentTool(store)

        result = await tool.execute({"name": "analyzer"})

        assert result.startswith("Error:")

    async def test_add_can_seed_local_hub_skills(self, tmp_path: Path) -> None:
        store = EquipmentPrototypeStore(tmp_path / "equipments")
        hub = LocalSkillHubStore(tmp_path / "local-skill-hub")
        source = tmp_path / "source-skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: fetch\n---\n# Fetch", encoding="utf-8")
        hub.import_skill(source, category="search", skill_name="fetch")
        tool = AddEquipmentTool(store, local_skill_hub=hub)

        result = await tool.execute(
            {
                "name": "analyzer",
                "create_starter_skill": False,
                "local_hub_skills": ["search/fetch"],
            }
        )

        assert "Seeded local hub skills: search/fetch->fetch" in result
        assert (tmp_path / "equipments" / "analyzer" / "skills" / "fetch" / "SKILL.md").is_file()

    async def test_add_with_local_hub_skill_requires_hub(self, tmp_path: Path) -> None:
        store = EquipmentPrototypeStore(tmp_path / "equipments")
        tool = AddEquipmentTool(store)

        result = await tool.execute({"name": "analyzer", "local_hub_skills": ["fetch"]})

        assert result.startswith("Error:")
        assert "local skill hub is not configured" in result

    async def test_add_rejects_colliding_leaf_names_from_different_categories(
        self, tmp_path: Path
    ) -> None:
        store = EquipmentPrototypeStore(tmp_path / "equipments")
        hub = LocalSkillHubStore(tmp_path / "local-skill-hub")
        source_a = tmp_path / "source-a"
        source_b = tmp_path / "source-b"
        source_a.mkdir(parents=True)
        source_b.mkdir(parents=True)
        (source_a / "SKILL.md").write_text("---\nname: fetch\n---\n# Fetch A", encoding="utf-8")
        (source_b / "SKILL.md").write_text("---\nname: fetch\n---\n# Fetch B", encoding="utf-8")
        hub.import_skill(source_a, category="search/arxiv", skill_name="fetch")
        hub.import_skill(source_b, category="search/pubmed", skill_name="fetch")
        tool = AddEquipmentTool(store, local_skill_hub=hub)

        result = await tool.execute(
            {
                "name": "analyzer",
                "create_starter_skill": False,
                "local_hub_skills": ["search/arxiv/fetch", "search/pubmed/fetch"],
            }
        )

        assert result.startswith("Error:")
        assert "both map to equipment skill name 'fetch'" in result


class TestLocalSkillHubTools:
    async def test_list_local_hub_skills(self, tmp_path: Path) -> None:
        hub = LocalSkillHubStore(tmp_path / "local-skill-hub")
        source = tmp_path / "source-skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: fetch\n---\n# Fetch", encoding="utf-8")
        hub.import_skill(source, skill_name="fetch")
        tool = ListLocalSkillHubSkillsTool(hub)

        result = await tool.execute({})

        assert "Local skill hub" in result
        assert "unarchived/fetch (category=unarchived)" in result

    async def test_list_local_hub_skills_with_category_filter(self, tmp_path: Path) -> None:
        hub = LocalSkillHubStore(tmp_path / "local-skill-hub")
        source = tmp_path / "source-skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: fetch\n---\n# Fetch", encoding="utf-8")
        hub.import_skill(source, skill_name="fetch", category="search")
        tool = ListLocalSkillHubSkillsTool(hub)

        result = await tool.execute({"category": "search"})

        assert "search/fetch (category=search)" in result

    async def test_import_skill_to_local_hub(self, tmp_path: Path) -> None:
        hub = LocalSkillHubStore(tmp_path / "local-skill-hub")
        source = tmp_path / "source-skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: fetch\n---\n# Fetch", encoding="utf-8")
        tool = ImportSkillToLocalHubTool(hub)

        result = await tool.execute(
            {"source_dir": str(source), "skill_name": "fetch", "category": "search/arxiv"}
        )

        assert "Imported local hub skill 'search/arxiv/fetch'" in result
        assert (tmp_path / "local-skill-hub" / "search" / "arxiv" / "fetch" / "SKILL.md").is_file()

    async def test_add_local_hub_skills_to_equipment(self, tmp_path: Path) -> None:
        store = EquipmentPrototypeStore(tmp_path / "equipments")
        store.create("analyzer")
        hub = LocalSkillHubStore(tmp_path / "local-skill-hub")
        source = tmp_path / "source-skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: fetch\n---\n# Fetch", encoding="utf-8")
        hub.import_skill(source, category="search", skill_name="fetch")
        tool = AddLocalHubSkillsToEquipmentTool(store, hub)

        result = await tool.execute(
            {"equipment_name": "analyzer", "skill_names": ["search/fetch"]}
        )

        assert "Added 1 local hub skill(s)" in result
        assert "search/fetch->fetch" in result
        assert (tmp_path / "equipments" / "analyzer" / "skills" / "fetch" / "SKILL.md").is_file()

    async def test_add_local_hub_skills_to_project(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "drclaw_data"
        project_store = JsonProjectStore(data_dir)
        project = project_store.create_project("Ada")
        hub = LocalSkillHubStore(data_dir / "local-skill-hub")
        source = tmp_path / "source-skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: ssh-exec\n---\n# SSH", encoding="utf-8")
        hub.import_skill(source, category="exec", skill_name="ssh-essentials")
        tool = AddLocalHubSkillsToProjectTool(
            project_store=project_store,
            projects_dir=data_dir / "projects",
            local_skill_hub=hub,
        )

        result = await tool.execute(
            {"project_id": project.id, "skill_names": ["exec/ssh-essentials"]}
        )

        assert "Added 1 local hub skill(s)" in result
        assert "exec/ssh-essentials->ssh-essentials" in result
        assert (
            data_dir
            / "projects"
            / project.id
            / "workspace"
            / "skills"
            / "ssh-essentials"
            / "SKILL.md"
        ).is_file()

    async def test_add_local_hub_skills_to_project_missing_project(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "drclaw_data"
        project_store = JsonProjectStore(data_dir)
        hub = LocalSkillHubStore(data_dir / "local-skill-hub")
        source = tmp_path / "source-skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: ssh-exec\n---\n# SSH", encoding="utf-8")
        hub.import_skill(source, category="exec", skill_name="ssh-essentials")
        tool = AddLocalHubSkillsToProjectTool(
            project_store=project_store,
            projects_dir=data_dir / "projects",
            local_skill_hub=hub,
        )

        result = await tool.execute(
            {"project_id": "missing", "skill_names": ["exec/ssh-essentials"]}
        )

        assert result.startswith("Error:")
        assert "project 'missing' not found" in result

    async def test_set_and_list_local_hub_category_metadata(self, tmp_path: Path) -> None:
        hub = LocalSkillHubStore(tmp_path / "local-skill-hub")
        set_tool = SetLocalSkillHubCategoryMetadataTool(hub)
        list_tool = ListLocalSkillHubCategoriesTool(hub)

        update_result = await set_tool.execute(
            {"category": "science/math", "description": "Math-related discovery skills."}
        )
        list_result = await list_tool.execute({})

        assert "Updated local skill hub category 'science/math' metadata" in update_result
        assert "science/math: Math-related discovery skills." in list_result


class TestListEquipmentsTool:
    async def test_list_with_active_runs(self, tmp_path: Path) -> None:
        store = EquipmentPrototypeStore(tmp_path / "equipments")
        prototype = store.create("analyzer")
        skill_dir = prototype.skills_dir / "baseline"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Baseline", encoding="utf-8")
        manager = _FakeRuntimeManager({"analyzer": 2})

        tool = ListEquipmentsTool(store, runtime_manager=manager)
        result = await tool.execute({})

        assert "Equipments:" in result
        assert "analyzer" in result
        assert "skills=1" in result
        assert "active_runs=2" in result

    async def test_list_empty(self, tmp_path: Path) -> None:
        store = EquipmentPrototypeStore(tmp_path / "equipments")
        tool = ListEquipmentsTool(store)

        result = await tool.execute({})

        assert result == "No equipments yet."


class TestRemoveEquipmentTool:
    async def test_remove_blocks_when_active_without_force(self, tmp_path: Path) -> None:
        store = EquipmentPrototypeStore(tmp_path / "equipments")
        store.create("analyzer")
        manager = _FakeRuntimeManager({"analyzer": 1})
        tool = RemoveEquipmentTool(store, runtime_manager=manager)

        result = await tool.execute({"name": "analyzer"})

        assert result.startswith("Error:")
        assert "active runs" in result
        assert store.get("analyzer") is not None

    async def test_remove_force_succeeds(self, tmp_path: Path) -> None:
        store = EquipmentPrototypeStore(tmp_path / "equipments")
        store.create("analyzer")
        manager = _FakeRuntimeManager({"analyzer": 1})
        tool = RemoveEquipmentTool(store, runtime_manager=manager)

        result = await tool.execute({"name": "analyzer", "force": True})

        assert "Removed equipment" in result
        assert store.get("analyzer") is None

    async def test_remove_missing_returns_error(self, tmp_path: Path) -> None:
        store = EquipmentPrototypeStore(tmp_path / "equipments")
        store.create("analyzer")
        tool = RemoveEquipmentTool(store)

        result = await tool.execute({"name": "ghost"})

        assert result.startswith("Error:")
        assert "analyzer" in result
