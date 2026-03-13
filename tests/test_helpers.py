"""Tests for utility helpers."""

from pathlib import Path

import pytest

from drclaw.utils.helpers import (
    ensure_default_skill_dirs,
    ensure_dir,
    safe_filename,
    slugify,
    timestamp,
)


def test_ensure_dir(tmp_path: Path):
    nested = tmp_path / "a" / "b" / "c"
    result = ensure_dir(nested)
    assert result == nested
    assert nested.is_dir()


def test_safe_filename():
    assert safe_filename("hello<world>:test") == "hello_world__test"
    assert safe_filename("normal.txt") == "normal.txt"
    assert safe_filename('a/b\\c"d') == "a_b_c_d"


def test_safe_filename_empty_raises():
    with pytest.raises(ValueError):
        safe_filename("")


def test_safe_filename_whitespace_only_raises():
    with pytest.raises(ValueError):
        safe_filename("   ")


def test_slugify():
    assert slugify("My Cool Project!!!") == "my-cool-project"
    assert slugify("  Hello   World  ") == "hello-world"
    assert slugify("UPPERCASE-and_mixed") == "uppercase-and-mixed"
    assert slugify("already-slugified") == "already-slugified"
    assert slugify("123 test") == "123-test"


def test_timestamp():
    ts = timestamp()
    assert "T" in ts
    assert len(ts) >= 19
    # UTC timestamps end with +00:00
    assert "+00:00" in ts


def test_ensure_default_skill_dirs_seeds_builtin_global_skills(tmp_path: Path):
    ensure_default_skill_dirs(tmp_path)
    skills_dir = tmp_path / "skills"
    assert skills_dir.is_dir()
    assert (tmp_path / "assets").is_dir()
    assert (tmp_path / "local-skill-hub").is_dir()
    assert (tmp_path / "agent-hub").is_dir()
    assert (tmp_path / "assets" / "avatars" / "17.png").is_file()
    # Builtin skills are copied into user-global skills on bootstrap.
    assert (skills_dir / "memory" / "SKILL.md").is_file()
    # Agent hub templates are seeded on first bootstrap.
    assert (tmp_path / "agent-hub" / "cat" / "AGENT.yaml").is_file()
    # Local hub templates are seeded on first bootstrap.
    assert (tmp_path / "local-skill-hub" / "search" / "arxiv" / "fetch" / "SKILL.md").is_file()


def test_ensure_default_skill_dirs_does_not_overwrite_existing_skill(tmp_path: Path):
    custom_skill = tmp_path / "skills" / "memory"
    custom_skill.mkdir(parents=True)
    custom_file = custom_skill / "SKILL.md"
    custom_file.write_text("CUSTOM MEMORY SKILL", encoding="utf-8")

    ensure_default_skill_dirs(tmp_path)
    assert custom_file.read_text(encoding="utf-8") == "CUSTOM MEMORY SKILL"


def test_ensure_default_skill_dirs_does_not_overwrite_existing_assets(tmp_path: Path):
    avatars_dir = tmp_path / "assets" / "avatars"
    avatars_dir.mkdir(parents=True)
    avatar = avatars_dir / "17.png"
    avatar.write_text("custom-avatar", encoding="utf-8")

    ensure_default_skill_dirs(tmp_path)
    assert avatar.read_text(encoding="utf-8") == "custom-avatar"


def test_ensure_default_skill_dirs_skips_hub_reseeding_if_hub_exists(tmp_path: Path):
    local_hub = tmp_path / "local-skill-hub"
    agent_hub = tmp_path / "agent-hub"
    (local_hub / "custom" / "tool").mkdir(parents=True)
    (local_hub / "custom" / "tool" / "SKILL.md").write_text("custom", encoding="utf-8")
    (agent_hub / "mine").mkdir(parents=True)
    (agent_hub / "mine" / "AGENT.yaml").write_text("name: mine\n", encoding="utf-8")

    ensure_default_skill_dirs(tmp_path)

    assert (local_hub / "custom" / "tool" / "SKILL.md").read_text(encoding="utf-8") == "custom"
    assert (agent_hub / "mine" / "AGENT.yaml").read_text(encoding="utf-8") == "name: mine\n"
    assert not (agent_hub / "cat").exists()
    assert not (local_hub / "search" / "arxiv" / "fetch" / "SKILL.md").exists()


def test_ensure_default_skill_dirs_backfills_missing_agent_hub_avatar(tmp_path: Path):
    agent_hub = tmp_path / "agent-hub"
    cat_dir = agent_hub / "cat"
    cat_dir.mkdir(parents=True)
    (cat_dir / "AGENT.yaml").write_text("name: cat\n", encoding="utf-8")

    ensure_default_skill_dirs(tmp_path)
    content = (cat_dir / "AGENT.yaml").read_text(encoding="utf-8")
    assert "avatar: /assets/avatars/19.png" in content
