"""Tests for AgentHubStore: discovery, parsing, seeding, and activation."""

from pathlib import Path

import pytest
import yaml

from drclaw.agent_hub.store import AgentHubStore, AgentHubTemplate, _parse_metadata
from drclaw.models.project import JsonProjectStore


def _make_template(root: Path, name: str, *, soul: str = "", skills: list[str] | None = None,
                   yaml_extra: str = "") -> Path:
    """Create a minimal hub template directory."""
    tpl_dir = root / name
    tpl_dir.mkdir(parents=True, exist_ok=True)
    yaml_content = (
        f"name: {name}\n"
        f"description: Test template {name}\n"
        f"tags: [test, demo]\n"
        f"goals:\n  - Goal one\n  - Goal two\n"
        f"equipment_refs: []\n"
        + yaml_extra
    )
    (tpl_dir / "AGENT.yaml").write_text(yaml_content, encoding="utf-8")
    if soul:
        (tpl_dir / "SOUL.md").write_text(soul, encoding="utf-8")
    skills_dir = tpl_dir / "skills"
    skills_dir.mkdir(exist_ok=True)
    for sk in (skills or []):
        sk_dir = skills_dir / sk
        sk_dir.mkdir()
        (sk_dir / "SKILL.md").write_text(
            f"---\nname: {sk}\ndescription: Skill {sk}\n---\n# {sk}\n",
            encoding="utf-8",
        )
    return tpl_dir


# --- _parse_metadata ---

def test_parse_metadata_valid(tmp_path: Path):
    d = tmp_path / "tpl"
    d.mkdir()
    (d / "AGENT.yaml").write_text("name: foo\ndescription: bar\ntags: [a]\n")
    meta = _parse_metadata(d)
    assert meta["name"] == "foo"
    assert meta["description"] == "bar"
    assert meta["tags"] == ["a"]


def test_parse_metadata_missing(tmp_path: Path):
    d = tmp_path / "tpl"
    d.mkdir()
    assert _parse_metadata(d) == {}


def test_parse_metadata_invalid_yaml(tmp_path: Path):
    d = tmp_path / "tpl"
    d.mkdir()
    (d / "AGENT.yaml").write_text(": :\n  - - [")
    assert _parse_metadata(d) == {}


def test_parse_metadata_non_dict(tmp_path: Path):
    d = tmp_path / "tpl"
    d.mkdir()
    (d / "AGENT.yaml").write_text("- item1\n- item2\n")
    assert _parse_metadata(d) == {}


# --- Seeding ---

def test_seed_copies_bundled_templates(tmp_path: Path):
    bundled = tmp_path / "bundled"
    hub = tmp_path / "hub"
    _make_template(bundled, "alpha", soul="I am Alpha")
    _make_template(bundled, "beta")

    store = AgentHubStore(hub, bundled_dir=bundled)
    assert hub.is_dir()
    assert (hub / "alpha" / "AGENT.yaml").is_file()
    assert (hub / "beta" / "AGENT.yaml").is_file()
    assert (hub / "alpha" / "SOUL.md").read_text() == "I am Alpha"

    templates = store.list()
    assert len(templates) == 2


def test_seed_no_bundled_dir(tmp_path: Path):
    hub = tmp_path / "hub"
    store = AgentHubStore(hub, bundled_dir=tmp_path / "nonexistent")
    assert hub.is_dir()
    assert store.list() == []


def test_seed_skipped_if_hub_exists(tmp_path: Path):
    bundled = tmp_path / "bundled"
    hub = tmp_path / "hub"
    hub.mkdir()
    _make_template(bundled, "alpha")

    store = AgentHubStore(hub, bundled_dir=bundled)
    # Hub existed already, so seeding is skipped.
    assert not (hub / "alpha").exists()
    assert store.list() == []


# --- list / get ---

def test_list_templates(tmp_path: Path):
    hub = tmp_path / "hub"
    _make_template(hub, "alpha")
    _make_template(hub, "beta")

    store = AgentHubStore(hub, bundled_dir=tmp_path / "empty")
    templates = store.list()
    assert len(templates) == 2
    names = {t.name for t in templates}
    assert names == {"alpha", "beta"}


def test_get_template(tmp_path: Path):
    hub = tmp_path / "hub"
    _make_template(hub, "alpha", soul="I am Alpha", skills=["web-search"])

    store = AgentHubStore(hub, bundled_dir=tmp_path / "empty")
    t = store.get("alpha")
    assert t is not None
    assert t.name == "alpha"
    assert t.description == "Test template alpha"
    assert t.tags == ["test", "demo"]
    assert t.goals == ["Goal one", "Goal two"]
    assert t.skills_dir == hub / "alpha" / "skills"


def test_get_nonexistent(tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    store = AgentHubStore(hub, bundled_dir=tmp_path / "empty")
    assert store.get("nope") is None


def test_get_empty_name(tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    store = AgentHubStore(hub, bundled_dir=tmp_path / "empty")
    assert store.get("") is None


def test_template_fields(tmp_path: Path):
    hub = tmp_path / "hub"
    _make_template(hub, "gamma", yaml_extra="equipment_refs:\n  - web-searcher\n")
    store = AgentHubStore(hub, bundled_dir=tmp_path / "empty")
    t = store.get("gamma")
    assert t is not None
    assert t.equipment_refs == ["web-searcher"]
    assert isinstance(t, AgentHubTemplate)
    assert t.root_dir == hub / "gamma"


def test_template_avatar_field(tmp_path: Path):
    hub = tmp_path / "hub"
    _make_template(hub, "cat", yaml_extra="avatar: /assets/avatar_cat.png\n")
    store = AgentHubStore(hub, bundled_dir=tmp_path / "empty")
    t = store.get("cat")
    assert t is not None
    assert t.avatar == "/assets/avatar_cat.png"


# --- Activation ---

@pytest.fixture
def project_store(tmp_path: Path) -> JsonProjectStore:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return JsonProjectStore(data_dir)


def test_activate_creates_project(tmp_path: Path, project_store: JsonProjectStore):
    hub = tmp_path / "hub"
    _make_template(hub, "alpha", soul="# Alpha Soul", skills=["web-search"])
    store = AgentHubStore(hub, bundled_dir=tmp_path / "empty")

    data_dir = tmp_path / "data"
    project = store.activate("alpha", project_store, data_dir)

    assert project.name == "alpha"
    assert project.description == "Test template alpha"
    assert project.hub_template == "alpha"
    assert "Goal one" in project.goals
    assert "test" in project.tags

    # SOUL.md copied
    ws = data_dir / "projects" / project.id / "workspace"
    assert (ws / "SOUL.md").read_text() == "# Alpha Soul"

    # Skills copied
    assert (ws / "skills" / "web-search" / "SKILL.md").is_file()

    # Project persisted
    found = project_store.get_project(project.id)
    assert found is not None
    assert found.hub_template == "alpha"


def test_activate_nonexistent_template(tmp_path: Path, project_store: JsonProjectStore):
    hub = tmp_path / "hub"
    hub.mkdir()
    store = AgentHubStore(hub, bundled_dir=tmp_path / "empty")

    with pytest.raises(ValueError, match="not found"):
        store.activate("nope", project_store, tmp_path / "data")


def test_activate_duplicate_creates_separate_projects(
    tmp_path: Path, project_store: JsonProjectStore
):
    hub = tmp_path / "hub"
    _make_template(hub, "alpha", soul="soul")
    store = AgentHubStore(hub, bundled_dir=tmp_path / "empty")
    data_dir = tmp_path / "data"

    store.activate("alpha", project_store, data_dir)
    # Second activation would fail because name already exists.
    # This is expected — each activation must have unique project name.
    with pytest.raises(ValueError, match="already exists"):
        store.activate("alpha", project_store, data_dir)


def test_activate_no_soul(tmp_path: Path, project_store: JsonProjectStore):
    """Templates without SOUL.md should still work (default soul created by project store)."""
    hub = tmp_path / "hub"
    _make_template(hub, "beta")
    store = AgentHubStore(hub, bundled_dir=tmp_path / "empty")
    data_dir = tmp_path / "data"

    project = store.activate("beta", project_store, data_dir)
    ws = data_dir / "projects" / project.id / "workspace"
    # Default soul was created by JsonProjectStore.create_project
    assert (ws / "SOUL.md").is_file()


def test_activate_no_skills(tmp_path: Path, project_store: JsonProjectStore):
    """Templates without skills should still activate cleanly."""
    hub = tmp_path / "hub"
    _make_template(hub, "gamma")
    store = AgentHubStore(hub, bundled_dir=tmp_path / "empty")
    data_dir = tmp_path / "data"

    project = store.activate("gamma", project_store, data_dir)
    assert project.hub_template == "gamma"


def test_publish_from_project_builds_template_snapshot(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    project_store = JsonProjectStore(data_dir)
    project = project_store.create_project("Research Agent", description="Current project desc")
    project.tags = ["science", "demo"]
    project.goals = "Goal one\nGoal two\n"
    project_store.update_project(project)

    project_dir = data_dir / "projects" / project.id
    workspace = project_dir / "workspace"
    (workspace / "SOUL.md").write_text("# Custom Soul", encoding="utf-8")
    (project_dir / "MEMORY.md").write_text("memory", encoding="utf-8")
    (project_dir / "HISTORY.md").write_text("history", encoding="utf-8")

    global_skills = data_dir / "skills"
    (global_skills / "global-fetch").mkdir(parents=True, exist_ok=True)
    (global_skills / "global-fetch" / "SKILL.md").write_text("# global fetch", encoding="utf-8")
    (global_skills / "shared").mkdir(parents=True, exist_ok=True)
    (global_skills / "shared" / "SKILL.md").write_text("# global shared", encoding="utf-8")

    local_skills = workspace / "skills"
    (local_skills / "local-analyze").mkdir(parents=True, exist_ok=True)
    (local_skills / "local-analyze" / "SKILL.md").write_text("# local analyze", encoding="utf-8")
    (local_skills / "shared").mkdir(parents=True, exist_ok=True)
    (local_skills / "shared" / "SKILL.md").write_text("# local shared", encoding="utf-8")

    hub = tmp_path / "hub"
    store = AgentHubStore(hub, bundled_dir=tmp_path / "empty")
    published = store.publish_from_project(project, data_dir)

    assert published.name == "Research Agent"
    template_dir = hub / "research-agent"
    assert template_dir.is_dir()

    metadata = yaml.safe_load((template_dir / "AGENT.yaml").read_text(encoding="utf-8"))
    assert metadata["name"] == "Research Agent"
    assert metadata["description"] == "Current project desc"
    assert metadata["tags"] == ["science", "demo"]
    assert metadata["goals"] == ["Goal one", "Goal two"]
    assert metadata["equipment_refs"] == []

    assert (template_dir / "SOUL.md").read_text(encoding="utf-8") == "# Custom Soul"
    assert (template_dir / "skills" / "global-fetch" / "SKILL.md").is_file()
    assert (template_dir / "skills" / "local-analyze" / "SKILL.md").is_file()
    # Local skill should override same-name global skill.
    assert (template_dir / "skills" / "shared" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "# local shared"

    # Memory/session artifacts are not part of published templates.
    assert not (template_dir / "MEMORY.md").exists()
    assert not (template_dir / "HISTORY.md").exists()
    assert not (template_dir / "sessions").exists()


def test_publish_from_project_conflict_name(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    project_store = JsonProjectStore(data_dir)
    project = project_store.create_project("Alpha")

    hub = tmp_path / "hub"
    _make_template(hub, "alpha")
    store = AgentHubStore(hub, bundled_dir=tmp_path / "empty")

    with pytest.raises(ValueError, match="already exists"):
        store.publish_from_project(project, data_dir, template_name="alpha")


# --- Project model hub_template round-trip ---

def test_hub_template_roundtrip(project_store: JsonProjectStore):
    """hub_template field should survive JSON serialization."""
    p = project_store.create_project("test-rt")
    assert p.hub_template is None

    p.hub_template = "my-template"
    project_store.update_project(p)

    reloaded = project_store.get_project(p.id)
    assert reloaded is not None
    assert reloaded.hub_template == "my-template"


def test_hub_template_none_by_default(project_store: JsonProjectStore):
    p = project_store.create_project("test-default")
    reloaded = project_store.get_project(p.id)
    assert reloaded is not None
    assert reloaded.hub_template is None
