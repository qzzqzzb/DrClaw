"""Skills loader for agent capabilities.

Discovers, parses, and injects Markdown-driven skills into agent prompts.
2-tier resolution: workspace > global. First match wins on name collision.
"""

import json
import os
import re
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


class SkillsLoader:
    """Loads skills from workspace and global directories.

    Skills are directories containing a SKILL.md file with optional
    YAML-like frontmatter for metadata.
    """

    def __init__(
        self,
        workspace: Path,
        global_skills_dir: Path | None = None,
        extra_skill_dirs: list[tuple[str, Path]] | None = None,
        env_provider: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self.workspace = workspace
        self.global_skills_dir = global_skills_dir
        self.extra_skill_dirs = list(extra_skill_dirs or [])
        self._env_provider = env_provider

    def _get_skill_dirs(self) -> list[tuple[str, Path]]:
        """Return (source_label, path) for each existing skill tier directory."""
        candidates = [
            *self.extra_skill_dirs,
            ("workspace", self.workspace / "skills"),
            ("global", self.global_skills_dir),
        ]
        return [(label, p) for label, p in candidates if p is not None and p.is_dir()]

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """List all skills, deduplicated by name (first tier wins)."""
        skills: list[dict[str, str]] = []
        seen: set[str] = set()

        for source, skill_dir in self._get_skill_dirs():
            for entry in sorted(skill_dir.iterdir(), key=lambda p: p.name):
                if not entry.is_dir() or entry.name in seen:
                    continue
                skill_file = entry / "SKILL.md"
                if skill_file.exists():
                    seen.add(entry.name)
                    skills.append(
                        {
                            "name": entry.name,
                            "path": str(skill_file),
                            "source": source,
                        }
                    )

        if filter_unavailable:
            return [s for s in skills if self._check_requirements(self._get_skill_meta(s["name"]))]
        return skills

    def load_skill(self, name: str) -> str | None:
        """Load a skill's SKILL.md content by name (first tier wins)."""
        for _, skill_dir in self._get_skill_dirs():
            path = skill_dir / name / "SKILL.md"
            if path.exists():
                return path.read_text(encoding="utf-8")
        return None

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """Load and format skills for inclusion in agent context."""
        parts = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                content = self._strip_frontmatter(content)
                if content:
                    parts.append(f"### Skill: {name}\n\n{content}")
        return "\n\n---\n\n".join(parts) if parts else ""

    def build_skills_summary(self) -> str:
        """Build XML summary of all skills (for progressive loading)."""
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        def escape_xml(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = ["<skills>"]
        for s in all_skills:
            name = escape_xml(s["name"])
            path = escape_xml(s["path"])
            desc = escape_xml(self._get_skill_description(s["name"]))
            skill_meta = self._get_skill_meta(s["name"])
            available = self._check_requirements(skill_meta)

            lines.append(f'  <skill available="{str(available).lower()}">')
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <location>{path}</location>")

            if not available:
                missing = self._get_missing_requirements(skill_meta)
                if missing:
                    lines.append(f"    <requires>{escape_xml(missing)}</requires>")
                install = self._get_install_instructions(skill_meta)
                if install:
                    lines.append(f"    <install>{escape_xml(install)}</install>")

            lines.append("  </skill>")
        lines.append("</skills>")
        return "\n".join(lines)

    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true in top-level frontmatter."""
        result = []
        for s in self.list_skills(filter_unavailable=True):
            meta = self.get_skill_metadata(s["name"]) or {}
            if str(meta.get("always", "")).lower() in ("true", "1", "yes"):
                result.append(s["name"])
        return result

    def get_skill_metadata(self, name: str) -> dict | None:
        """Parse top-level frontmatter key-value pairs from a skill."""
        content = self.load_skill(name)
        if not content or not content.startswith("---"):
            return None

        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return None

        metadata: dict[str, str] = {}
        for line in match.group(1).split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip().strip("\"'")
        return metadata

    def _get_skill_description(self, name: str) -> str:
        """Get the description from a skill's frontmatter, or fall back to name."""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name

    def _get_skill_meta(self, name: str) -> dict:
        """Get parsed nanobot/openclaw metadata from a skill's frontmatter."""
        meta = self.get_skill_metadata(name) or {}
        return self._parse_nanobot_metadata(meta.get("metadata", ""))

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end() :].strip()
        return content

    def _parse_nanobot_metadata(self, raw: str) -> dict[str, Any]:
        """Parse skill metadata JSON (nanobot key takes precedence over openclaw)."""
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                return {}
            result = data.get("nanobot", data.get("openclaw", {}))
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements (bins, env vars) are met."""
        requires = skill_meta.get("requires", {})
        env_map = self._get_env()
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False
        for env_name in requires.get("env", []):
            if not env_map.get(env_name):
                return False
        return True

    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get description of missing requirements."""
        missing = []
        requires = skill_meta.get("requires", {})
        env_map = self._get_env()
        for b in requires.get("bins", []):
            if not shutil.which(b):
                missing.append(f"CLI: {b}")
        for env_name in requires.get("env", []):
            if not env_map.get(env_name):
                missing.append(f"ENV: {env_name}")
        return ", ".join(missing)

    def _get_install_instructions(self, skill_meta: dict) -> str:
        """Get install instructions from skill metadata."""
        items = skill_meta.get("install", [])
        if not isinstance(items, list) or not items:
            return ""
        return "; ".join(items)

    def _get_env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self._env_provider is not None:
            env.update(dict(self._env_provider()))
        return env
