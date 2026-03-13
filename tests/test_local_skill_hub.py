"""Tests for local dormant-skill storage used by equipment provisioning."""

from __future__ import annotations

from pathlib import Path

from drclaw.skills.local_hub import LocalSkillHubStore


def _make_source_skill(base: Path, name: str = "fetch") -> Path:
    source = base / name
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(f"---\nname: {name}\n---\n# {name}", encoding="utf-8")
    return source


def test_import_and_get_skill(tmp_path: Path) -> None:
    store = LocalSkillHubStore(tmp_path / "local-skill-hub")
    source = _make_source_skill(tmp_path, "fetch")

    imported = store.import_skill(source)
    loaded = store.get("fetch")

    assert imported.ref == "unarchived/fetch"
    assert imported.name == "fetch"
    assert imported.category == "unarchived"
    assert loaded is not None
    assert loaded.ref == "unarchived/fetch"
    assert loaded.skill_file.is_file()


def test_import_requires_skill_md(tmp_path: Path) -> None:
    store = LocalSkillHubStore(tmp_path / "local-skill-hub")
    source = tmp_path / "bad-source"
    source.mkdir(parents=True)

    try:
        store.import_skill(source)
    except ValueError as exc:
        assert "Missing SKILL.md" in str(exc)
    else:
        raise AssertionError("Expected ValueError for missing SKILL.md")


def test_list_ignores_invalid_entries(tmp_path: Path) -> None:
    root = tmp_path / "local-skill-hub"
    (root / "unarchived" / "good").mkdir(parents=True)
    (root / "unarchived" / "good" / "SKILL.md").write_text("# good", encoding="utf-8")
    (root / "bad").mkdir(parents=True)

    store = LocalSkillHubStore(root)
    listed = store.list()

    assert [s.ref for s in listed] == ["unarchived/good"]


def test_import_with_hierarchical_category(tmp_path: Path) -> None:
    store = LocalSkillHubStore(tmp_path / "local-skill-hub")
    source = _make_source_skill(tmp_path, "Arxiv Reader")

    imported = store.import_skill(source, category="Science/Math", skill_name="Arxiv Reader")

    assert imported.ref == "science/math/arxiv-reader"
    loaded = store.get("science/math/arxiv-reader")
    assert loaded is not None
    assert loaded.category == "science/math"
    assert loaded.name == "arxiv-reader"


def test_list_supports_category_filter(tmp_path: Path) -> None:
    store = LocalSkillHubStore(tmp_path / "local-skill-hub")
    source_a = _make_source_skill(tmp_path, "fetch")
    source_b = _make_source_skill(tmp_path, "pdf-reader")
    store.import_skill(source_a, category="search", skill_name="fetch")
    store.import_skill(source_b, category="doc", skill_name="pdf-reader")

    listed = store.list(category="search")

    assert [s.ref for s in listed] == ["search/fetch"]


def test_set_and_get_category_metadata(tmp_path: Path) -> None:
    store = LocalSkillHubStore(tmp_path / "local-skill-hub")
    store.set_category_metadata("science/math", "Mathematics and theorem proving skills.")

    categories = store.list_categories()
    by_cat = {c["category"]: c["description"] for c in categories}

    assert by_cat["science/math"] == "Mathematics and theorem proving skills."


def test_normalize_single_segment_ref_defaults_to_unarchived(tmp_path: Path) -> None:
    store = LocalSkillHubStore(tmp_path / "local-skill-hub")
    assert store.normalize_skill_ref("fetch") == "unarchived/fetch"


def test_list_categories_includes_empty_category_dirs_but_not_skill_subdirs(tmp_path: Path) -> None:
    root = tmp_path / "local-skill-hub"
    (root / "science").mkdir(parents=True)
    (root / "exec").mkdir(parents=True)
    source = _make_source_skill(tmp_path, "arxiv-reader")
    dotted_source = _make_source_skill(tmp_path, "paper-reader-1.0.0")
    store = LocalSkillHubStore(root)
    store.import_skill(source, category="search", skill_name="arxiv-reader")
    store.import_skill(dotted_source, category="doc", skill_name="paper-reader-1.0.0")
    (root / "search" / "arxiv-reader" / "scripts").mkdir(parents=True)
    (root / "doc" / "paper-reader-1.0.0" / "scripts").mkdir(parents=True)

    categories = store.list_categories()
    names = {c["category"] for c in categories}

    assert "science" in names
    assert "exec" in names
    assert "search" in names
    assert "search/arxiv-reader" not in names
    assert "search/arxiv-reader/scripts" not in names
    assert "doc/paper-reader-1-0-0" not in names
    assert "doc/paper-reader-1-0-0/scripts" not in names


def test_get_resolves_dotted_directory_with_normalized_ref(tmp_path: Path) -> None:
    root = tmp_path / "local-skill-hub"
    dotted = root / "doc" / "academic-deep-research-1.0.0"
    dotted.mkdir(parents=True)
    (dotted / "SKILL.md").write_text("---\nname: adr\n---\n# ADR", encoding="utf-8")
    store = LocalSkillHubStore(root)

    # Listing normalizes ref segments (dot -> dash).
    listed = store.list()
    assert [s.ref for s in listed] == ["doc/academic-deep-research-1-0-0"]

    # Lookup by normalized ref should still resolve the dotted on-disk folder.
    loaded = store.get("doc/academic-deep-research-1-0-0")
    assert loaded is not None
    assert loaded.root_dir == dotted
