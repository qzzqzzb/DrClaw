"""Tests for Project model, JsonProjectStore, and ProjectStore protocol."""

import json
import re
from pathlib import Path

import pytest

from drclaw.models.project import (
    AmbiguousProjectNameError,
    CorruptProjectStoreError,
    JsonProjectStore,
    ProjectStore,
)


@pytest.fixture
def store(tmp_data_dir: Path) -> JsonProjectStore:
    return JsonProjectStore(tmp_data_dir)


def test_create_project(store: JsonProjectStore, tmp_data_dir: Path):
    p = store.create_project("ML Classifier", "A test project")
    assert p.name == "ML Classifier"
    assert p.description == "A test project"
    assert p.status == "active"
    assert (tmp_data_dir / "projects" / p.id / "workspace").is_dir()
    assert (tmp_data_dir / "projects" / p.id / "workspace" / "skills").is_dir()
    soul_file = tmp_data_dir / "projects" / p.id / "workspace" / "SOUL.md"
    assert soul_file.is_file()
    assert "Identity & Persona" in soul_file.read_text(encoding="utf-8")
    assert (tmp_data_dir / "projects" / p.id / "sessions").is_dir()


def test_list_projects(store: JsonProjectStore):
    assert store.list_projects() == []
    store.create_project("Alpha")
    store.create_project("Beta")
    names = {p.name for p in store.list_projects()}
    assert names == {"Alpha", "Beta"}


def test_get_project(store: JsonProjectStore):
    p = store.create_project("Gamma")
    found = store.get_project(p.id)
    assert found is not None
    assert found.id == p.id
    assert store.get_project("nonexistent-0000") is None


def test_update_project(store: JsonProjectStore):
    p = store.create_project("Delta")
    p.description = "updated description"
    store.update_project(p)
    reloaded = store.get_project(p.id)
    assert reloaded is not None
    assert reloaded.description == "updated description"


def test_update_project_missing_raises(store: JsonProjectStore):
    ghost = store.create_project("Ghost")
    store.delete_project(ghost.id)
    with pytest.raises(KeyError):
        store.update_project(ghost)


def test_delete_project(store: JsonProjectStore):
    p = store.create_project("Epsilon")
    assert store.delete_project(p.id) is True
    assert store.get_project(p.id) is None
    assert store.delete_project(p.id) is False


def test_find_by_name_exact(store: JsonProjectStore):
    store.create_project("ML Classifier")
    store.create_project("ml classifier variant")
    p = store.find_by_name("ML Classifier")
    assert p is not None
    assert p.name == "ML Classifier"


def test_find_by_name_ambiguous(store: JsonProjectStore):
    store.create_project("ml classifier alpha")
    store.create_project("ml classifier beta")
    with pytest.raises(AmbiguousProjectNameError):
        store.find_by_name("ml")


def test_find_by_name_not_found(store: JsonProjectStore):
    store.create_project("Zeta")
    assert store.find_by_name("nonexistent") is None


def test_project_id_format(store: JsonProjectStore):
    p = store.create_project("My Test Project!")
    assert re.fullmatch(r"[a-z0-9-]+-[0-9a-f]{8}", p.id), f"Bad ID format: {p.id}"


def test_json_roundtrip(tmp_data_dir: Path):
    store1 = JsonProjectStore(tmp_data_dir)
    p = store1.create_project("Roundtrip", "persisted data")

    store2 = JsonProjectStore(tmp_data_dir)
    loaded = store2.get_project(p.id)
    assert loaded is not None
    assert loaded.name == "Roundtrip"
    assert loaded.description == "persisted data"
    assert loaded.created_at == p.created_at
    assert loaded.status == "active"


def test_projectstore_protocol(store: JsonProjectStore):
    assert isinstance(store, ProjectStore)


def test_create_project_empty_name_raises(store: JsonProjectStore):
    with pytest.raises(ValueError, match="empty"):
        store.create_project("")
    with pytest.raises(ValueError, match="empty"):
        store.create_project("   ")


def test_create_project_duplicate_name_raises(store: JsonProjectStore):
    store.create_project("Duplicate")
    with pytest.raises(ValueError, match="already exists"):
        store.create_project("Duplicate")


def test_load_corrupt_json_raises(tmp_data_dir: Path):
    store = JsonProjectStore(tmp_data_dir)
    (tmp_data_dir / "projects.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(CorruptProjectStoreError):
        store.list_projects()


def test_load_corrupt_schema_raises(tmp_data_dir: Path):
    store = JsonProjectStore(tmp_data_dir)
    # Valid JSON but missing required fields
    (tmp_data_dir / "projects.json").write_text('[{"id": "x"}]', encoding="utf-8")
    with pytest.raises(CorruptProjectStoreError):
        store.list_projects()


def test_project_goals_tags_roundtrip(tmp_data_dir: Path):
    store = JsonProjectStore(tmp_data_dir)
    p = store.create_project("Tagged", "desc")
    p.goals = "Find the answer to everything"
    p.tags = ["ml", "nlp", "transformers"]
    store.update_project(p)

    store2 = JsonProjectStore(tmp_data_dir)
    loaded = store2.get_project(p.id)
    assert loaded is not None
    assert loaded.goals == "Find the answer to everything"
    assert loaded.tags == ["ml", "nlp", "transformers"]


def test_backward_compat_existing_projects_json(tmp_data_dir: Path):
    """Pre-2b JSON without goals/tags fields loads with defaults."""
    raw = [
        {
            "id": "old-proj-00000000",
            "name": "Legacy",
            "description": "old project",
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:00+00:00",
            "status": "active",
        }
    ]
    (tmp_data_dir / "projects.json").write_text(json.dumps(raw), encoding="utf-8")

    store = JsonProjectStore(tmp_data_dir)
    projects = store.list_projects()
    assert len(projects) == 1
    assert projects[0].goals == ""
    assert projects[0].tags == []
