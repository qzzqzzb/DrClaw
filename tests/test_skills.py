"""Tests for drclaw.agent.skills — SkillsLoader."""

import json
from pathlib import Path

from drclaw.agent.skills import SkillsLoader


def _loader(workspace: Path, **kwargs) -> SkillsLoader:
    """Create a SkillsLoader."""
    return SkillsLoader(workspace, **kwargs)


def _make_skill(base: Path, name: str, content: str) -> Path:
    """Helper: create a skill directory with SKILL.md."""
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(content, encoding="utf-8")
    return d


# ── Frontmatter parsing ──────────────────────────────────────────────


def test_parse_simple_kv(tmp_path: Path):
    _make_skill(tmp_path / "skills", "s1", "---\nname: s1\ndescription: A skill\n---\n# S1\n")
    loader = SkillsLoader(tmp_path)
    meta = loader.get_skill_metadata("s1")
    assert meta == {"name": "s1", "description": "A skill"}


def test_parse_quoted_description(tmp_path: Path):
    _make_skill(
        tmp_path / "skills", "s1", '---\nname: s1\ndescription: "Quoted desc"\n---\n# S1\n'
    )
    loader = SkillsLoader(tmp_path)
    meta = loader.get_skill_metadata("s1")
    assert meta is not None
    assert meta["description"] == "Quoted desc"


def test_parse_always_true(tmp_path: Path):
    _make_skill(tmp_path / "skills", "s1", "---\nname: s1\nalways: true\n---\n# S1\n")
    loader = SkillsLoader(tmp_path)
    meta = loader.get_skill_metadata("s1")
    assert meta is not None
    assert meta["always"] == "true"


def test_parse_colons_in_value(tmp_path: Path):
    _make_skill(tmp_path / "skills", "s1", "---\nname: s1\nnote: key: value: deep\n---\n# S1\n")
    loader = SkillsLoader(tmp_path)
    meta = loader.get_skill_metadata("s1")
    assert meta is not None
    assert meta["note"] == "key: value: deep"


def test_parse_nanobot_metadata(tmp_path: Path):
    md = json.dumps({"nanobot": {"requires": {"bins": ["git"]}}})
    _make_skill(tmp_path / "skills", "s1", f"---\nname: s1\nmetadata: {md}\n---\n# S1\n")
    loader = SkillsLoader(tmp_path)
    skill_meta = loader._get_skill_meta("s1")
    assert skill_meta == {"requires": {"bins": ["git"]}}


def test_parse_openclaw_metadata(tmp_path: Path):
    md = json.dumps({"openclaw": {"requires": {"env": ["API_KEY"]}}})
    _make_skill(tmp_path / "skills", "s1", f"---\nname: s1\nmetadata: {md}\n---\n# S1\n")
    loader = SkillsLoader(tmp_path)
    skill_meta = loader._get_skill_meta("s1")
    assert skill_meta == {"requires": {"env": ["API_KEY"]}}


def test_strip_frontmatter(tmp_path: Path):
    loader = SkillsLoader(tmp_path)
    text = "---\nname: s1\n---\n# Hello\n\nBody."
    assert loader._strip_frontmatter(text) == "# Hello\n\nBody."


def test_no_frontmatter(tmp_path: Path):
    _make_skill(tmp_path / "skills", "s1", "# Just content\n")
    loader = SkillsLoader(tmp_path)
    assert loader.get_skill_metadata("s1") is None


def test_unclosed_frontmatter(tmp_path: Path):
    _make_skill(tmp_path / "skills", "s1", "---\nname: s1\n# No closing\n")
    loader = SkillsLoader(tmp_path)
    assert loader.get_skill_metadata("s1") is None


# ── Tier resolution ──────────────────────────────────────────────────


def test_workspace_overrides_global(tmp_path: Path):
    ws = tmp_path / "ws"
    glob = tmp_path / "global"
    _make_skill(ws / "skills", "mem", "---\nname: mem\n---\n# WS")
    _make_skill(glob, "mem", "---\nname: mem\n---\n# Global")
    loader = _loader(ws, global_skills_dir=glob)
    skills = loader.list_skills()
    assert len(skills) == 1
    assert skills[0]["source"] == "workspace"


def test_list_all_sources(tmp_path: Path):
    ws = tmp_path / "ws"
    glob = tmp_path / "global"
    _make_skill(ws / "skills", "alpha", "---\nname: alpha\n---\n# A")
    _make_skill(glob, "beta", "---\nname: beta\n---\n# B")
    loader = SkillsLoader(ws, global_skills_dir=glob)
    skills = loader.list_skills()
    sources = {s["name"]: s["source"] for s in skills}
    assert sources == {"alpha": "workspace", "beta": "global"}


def test_within_tier_sorted_by_name(tmp_path: Path):
    glob = tmp_path / "global"
    _make_skill(glob, "zebra", "---\nname: zebra\n---\n# Z")
    _make_skill(glob, "alpha", "---\nname: alpha\n---\n# A")
    _make_skill(glob, "mid", "---\nname: mid\n---\n# M")
    loader = SkillsLoader(tmp_path, global_skills_dir=glob)
    names = [s["name"] for s in loader.list_skills()]
    assert names == ["alpha", "mid", "zebra"]


def test_missing_global_dir_silently_ignored(tmp_path: Path):
    _make_skill(tmp_path / "skills", "s1", "---\nname: s1\n---\n# S1")
    loader = SkillsLoader(tmp_path, global_skills_dir=tmp_path / "nonexistent")
    skills = loader.list_skills()
    assert len(skills) == 1


# ── Requirements ─────────────────────────────────────────────────────


def test_no_requirements_passes(tmp_path: Path):
    _make_skill(tmp_path / "skills", "s1", "---\nname: s1\n---\n# S1")
    loader = _loader(tmp_path)
    assert len(loader.list_skills(filter_unavailable=True)) == 1


def test_bin_present(tmp_path: Path):
    md = json.dumps({"nanobot": {"requires": {"bins": ["python3"]}}})
    _make_skill(tmp_path / "skills", "s1", f"---\nname: s1\nmetadata: {md}\n---\n# S1")
    loader = _loader(tmp_path)
    assert len(loader.list_skills(filter_unavailable=True)) == 1


def test_bin_missing(tmp_path: Path):
    md = json.dumps({"nanobot": {"requires": {"bins": ["__nonexistent_bin_xyz__"]}}})
    _make_skill(tmp_path / "skills", "s1", f"---\nname: s1\nmetadata: {md}\n---\n# S1")
    loader = _loader(tmp_path)
    assert len(loader.list_skills(filter_unavailable=True)) == 0
    assert len(loader.list_skills(filter_unavailable=False)) == 1


def test_env_missing(tmp_path: Path):
    md = json.dumps({"nanobot": {"requires": {"env": ["__RBOT_TEST_NONEXISTENT__"]}}})
    _make_skill(tmp_path / "skills", "s1", f"---\nname: s1\nmetadata: {md}\n---\n# S1")
    loader = _loader(tmp_path)
    assert len(loader.list_skills(filter_unavailable=True)) == 0


def test_env_present_via_env_provider(tmp_path: Path):
    md = json.dumps({"nanobot": {"requires": {"env": ["SCOPED_KEY"]}}})
    _make_skill(tmp_path / "skills", "s1", f"---\nname: s1\nmetadata: {md}\n---\n# S1")
    loader = _loader(tmp_path, env_provider=lambda: {"SCOPED_KEY": "ok"})
    assert len(loader.list_skills(filter_unavailable=True)) == 1


# ── get_always_skills ────────────────────────────────────────────────


def test_always_skills_returns_always_true(tmp_path: Path):
    _make_skill(tmp_path / "skills", "mem", "---\nname: mem\nalways: true\n---\n# M")
    _make_skill(tmp_path / "skills", "other", "---\nname: other\n---\n# O")
    loader = _loader(tmp_path)
    assert loader.get_always_skills() == ["mem"]


def test_always_skills_excludes_unavailable(tmp_path: Path):
    md = json.dumps({"nanobot": {"requires": {"bins": ["__nonexistent__"]}}})
    _make_skill(
        tmp_path / "skills", "mem", f"---\nname: mem\nalways: true\nmetadata: {md}\n---\n# M"
    )
    loader = _loader(tmp_path)
    assert loader.get_always_skills() == []


def test_always_false_not_treated_as_always(tmp_path: Path):
    _make_skill(tmp_path / "skills", "s1", "---\nname: s1\nalways: false\n---\n# S1")
    loader = _loader(tmp_path)
    assert loader.get_always_skills() == []


def test_always_skills_empty_if_none(tmp_path: Path):
    _make_skill(tmp_path / "skills", "s1", "---\nname: s1\n---\n# S1")
    loader = _loader(tmp_path)
    assert loader.get_always_skills() == []


# ── load_skill ───────────────────────────────────────────────────────


def test_load_skill_from_workspace(tmp_path: Path):
    _make_skill(tmp_path / "skills", "s1", "---\nname: s1\n---\n# WS")
    loader = SkillsLoader(tmp_path)
    content = loader.load_skill("s1")
    assert content is not None
    assert "# WS" in content


def test_load_skill_from_global(tmp_path: Path):
    glob = tmp_path / "global"
    _make_skill(glob, "s1", "---\nname: s1\n---\n# Global")
    loader = SkillsLoader(tmp_path, global_skills_dir=glob)
    content = loader.load_skill("s1")
    assert content is not None
    assert "# Global" in content


def test_load_skill_not_found(tmp_path: Path):
    loader = SkillsLoader(tmp_path)
    assert loader.load_skill("nonexistent") is None


def test_load_skill_dir_exists_no_skill_md(tmp_path: Path):
    (tmp_path / "skills" / "empty_skill").mkdir(parents=True)
    loader = SkillsLoader(tmp_path)
    assert loader.load_skill("empty_skill") is None


# ── load_skills_for_context ──────────────────────────────────────────


def test_load_skills_strips_frontmatter_and_adds_header(tmp_path: Path):
    _make_skill(tmp_path / "skills", "s1", "---\nname: s1\n---\n# Body\n\nContent.")
    loader = SkillsLoader(tmp_path)
    result = loader.load_skills_for_context(["s1"])
    assert result.startswith("### Skill: s1")
    assert "---\nname:" not in result
    assert "# Body" in result


def test_load_skills_multiple_joined(tmp_path: Path):
    _make_skill(tmp_path / "skills", "a", "---\nname: a\n---\n# A")
    _make_skill(tmp_path / "skills", "b", "---\nname: b\n---\n# B")
    loader = SkillsLoader(tmp_path)
    result = loader.load_skills_for_context(["a", "b"])
    assert "\n\n---\n\n" in result
    assert "### Skill: a" in result
    assert "### Skill: b" in result


def test_load_skills_skips_missing(tmp_path: Path):
    _make_skill(tmp_path / "skills", "a", "---\nname: a\n---\n# A")
    loader = SkillsLoader(tmp_path)
    result = loader.load_skills_for_context(["a", "nonexistent"])
    assert "### Skill: a" in result
    assert "nonexistent" not in result


# ── build_skills_summary ─────────────────────────────────────────────


def test_build_skills_summary_xml_structure(tmp_path: Path):
    _make_skill(tmp_path / "skills", "s1", "---\nname: s1\ndescription: A skill\n---\n# S1")
    loader = _loader(tmp_path)
    xml = loader.build_skills_summary()
    assert xml.startswith("<skills>")
    assert xml.endswith("</skills>")
    assert "<name>s1</name>" in xml
    assert "<description>A skill</description>" in xml
    assert 'available="true"' in xml


def test_build_skills_summary_shows_unavailable(tmp_path: Path):
    md = json.dumps({"nanobot": {"requires": {"bins": ["__nonexistent__"]}}})
    _make_skill(tmp_path / "skills", "s1", f"---\nname: s1\nmetadata: {md}\n---\n# S1")
    loader = _loader(tmp_path)
    xml = loader.build_skills_summary()
    assert 'available="false"' in xml
    assert "<requires>CLI: __nonexistent__</requires>" in xml


def test_build_skills_summary_xml_escaping(tmp_path: Path):
    _make_skill(
        tmp_path / "skills", "s1", "---\nname: s1\ndescription: A & B < C\n---\n# S1"
    )
    loader = _loader(tmp_path)
    xml = loader.build_skills_summary()
    assert "A &amp; B &lt; C" in xml


# ── Edge cases ───────────────────────────────────────────────────────


def test_malformed_json_metadata(tmp_path: Path):
    _make_skill(tmp_path / "skills", "s1", "---\nname: s1\nmetadata: {not json}\n---\n# S1")
    loader = _loader(tmp_path)
    assert loader._get_skill_meta("s1") == {}


def test_empty_skill_md(tmp_path: Path):
    _make_skill(tmp_path / "skills", "s1", "")
    loader = _loader(tmp_path)
    assert loader.get_skill_metadata("s1") is None
    # Should still appear in list (no requirements to fail)
    skills = loader.list_skills()
    assert len(skills) == 1


def test_nanobot_key_takes_precedence_over_openclaw(tmp_path: Path):
    md = json.dumps({"nanobot": {"val": "nano"}, "openclaw": {"val": "claw"}})
    _make_skill(tmp_path / "skills", "s1", f"---\nname: s1\nmetadata: {md}\n---\n# S1")
    loader = _loader(tmp_path)
    assert loader._get_skill_meta("s1") == {"val": "nano"}


# ── Install metadata ─────────────────────────────────────────────────


def test_install_metadata_surfaced_in_summary(tmp_path: Path):
    md = json.dumps({
        "nanobot": {
            "requires": {"bins": ["__nonexistent_xyz__"]},
            "install": ["brew install xyz (Install XYZ CLI)"],
        }
    })
    _make_skill(tmp_path / "skills", "s1", f"---\nname: s1\nmetadata: {md}\n---\n# S1")
    loader = _loader(tmp_path)
    xml = loader.build_skills_summary()
    assert 'available="false"' in xml
    assert "<install>brew install xyz (Install XYZ CLI)</install>" in xml


def test_install_not_shown_when_available(tmp_path: Path):
    md = json.dumps({
        "nanobot": {
            "requires": {"bins": ["python3"]},
            "install": ["brew install python3"],
        }
    })
    _make_skill(tmp_path / "skills", "s1", f"---\nname: s1\nmetadata: {md}\n---\n# S1")
    loader = _loader(tmp_path)
    xml = loader.build_skills_summary()
    assert 'available="true"' in xml
    assert "<install>" not in xml


def test_install_missing_gracefully(tmp_path: Path):
    md = json.dumps({"nanobot": {"requires": {"bins": ["__nonexistent_abc__"]}}})
    _make_skill(tmp_path / "skills", "s1", f"---\nname: s1\nmetadata: {md}\n---\n# S1")
    loader = _loader(tmp_path)
    xml = loader.build_skills_summary()
    assert "<requires>" in xml
    assert "<install>" not in xml


def test_runtime_does_not_load_builtin_skills_by_default(tmp_path: Path):
    loader = SkillsLoader(tmp_path)
    assert loader.list_skills(filter_unavailable=False) == []
    assert loader.load_skill("memory") is None
