"""Local skill hub for storing dormant skills outside active skill tiers."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from drclaw.utils.helpers import slugify


@dataclass(frozen=True)
class LocalHubSkill:
    """One skill entry stored in the local skill hub."""

    ref: str
    name: str
    category: str
    root_dir: Path
    skill_file: Path


class LocalSkillHubStore:
    """Persistent store for local hub skills.

    Canonical layout:
    ``~/.drclaw/local-skill-hub/<category>/<skill_name>/SKILL.md``
    with default category ``unarchived``.
    """

    UNARCHIVED_CATEGORY = "unarchived"
    CATEGORY_METADATA_FILE = "CATEGORY.md"

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = (root_dir or Path("~/.drclaw/local-skill-hub")).expanduser()

    def _normalize_segment(self, name: str) -> str:
        normalized = slugify(name).replace("_", "-")
        if not normalized:
            raise ValueError(f"Invalid skill name: {name!r}")
        return normalized

    def normalize_name(self, name: str) -> str:
        """Normalize a single skill-name segment to canonical slug."""
        return self._normalize_segment(name)

    def normalize_category(self, category: str | None, *, default_unarchived: bool = False) -> str:
        """Normalize hierarchical category path (e.g., science/math)."""
        if category is None:
            return self.UNARCHIVED_CATEGORY if default_unarchived else ""
        raw = category.strip().strip("/")
        if not raw:
            return self.UNARCHIVED_CATEGORY if default_unarchived else ""
        segments = [seg for seg in raw.split("/") if seg]
        normalized_segments = [self._normalize_segment(seg) for seg in segments]
        return "/".join(normalized_segments)

    def normalize_skill_ref(self, skill_ref: str) -> str:
        """Normalize a skill reference path (category + skill name)."""
        raw = skill_ref.strip().strip("/")
        if not raw:
            raise ValueError("Skill reference cannot be empty.")
        segments = [seg for seg in raw.split("/") if seg]
        normalized_segments = [self._normalize_segment(seg) for seg in segments]
        if len(normalized_segments) == 1:
            return f"{self.UNARCHIVED_CATEGORY}/{normalized_segments[0]}"
        return "/".join(normalized_segments)

    def build_skill_ref(self, *, category: str | None, skill_name: str) -> str:
        """Build normalized ref from category and skill name."""
        normalized_name = self._normalize_segment(skill_name)
        normalized_category = self.normalize_category(category, default_unarchived=True)
        return f"{normalized_category}/{normalized_name}"

    def _resolve_ref_path(self, skill_ref: str) -> Path:
        normalized_ref = self.normalize_skill_ref(skill_ref)
        return self.root_dir / Path(normalized_ref)

    def _make_skill_entry(self, skill_root: Path) -> LocalHubSkill | None:
        """Build LocalHubSkill entry from a skill root dir if valid."""
        skill_file = skill_root / "SKILL.md"
        if not skill_file.is_file():
            return None
        rel = skill_root.relative_to(self.root_dir).as_posix()
        ref = self.normalize_skill_ref(rel)
        parts = ref.split("/")
        name = parts[-1]
        category = "/".join(parts[:-1])
        return LocalHubSkill(
            ref=ref,
            name=name,
            category=category,
            root_dir=skill_root,
            skill_file=skill_file,
        )

    def get(self, skill_ref: str) -> LocalHubSkill | None:
        normalized_ref = self.normalize_skill_ref(skill_ref)
        root = self.root_dir / Path(normalized_ref)
        if root.is_dir():
            return self._make_skill_entry(root)

        # Backward-compat: existing on-disk folders may use non-canonical names
        # (for example, dots in version segments). Match by normalized ref.
        for skill in self.list():
            if skill.ref == normalized_ref:
                return skill
        return None

    def list(self, category: str | None = None) -> list[LocalHubSkill]:
        base = self.root_dir
        normalized_category = self.normalize_category(category, default_unarchived=False)
        if normalized_category:
            base = self.root_dir / Path(normalized_category)

        if not base.exists() or not base.is_dir():
            return []

        out: list[LocalHubSkill] = []
        for dirpath, dirnames, filenames in os.walk(base):
            current = Path(dirpath)
            if "SKILL.md" in filenames:
                entry = self._make_skill_entry(current)
                if entry is not None:
                    out.append(entry)
                # One SKILL.md defines one skill root; do not treat nested folders as siblings.
                dirnames[:] = []
                continue
            dirnames.sort()
        return sorted(out, key=lambda s: s.ref)

    def _category_dir(self, category: str) -> Path:
        normalized = self.normalize_category(category, default_unarchived=True)
        return self.root_dir / Path(normalized)

    def _category_metadata_file(self, category: str) -> Path:
        return self._category_dir(category) / self.CATEGORY_METADATA_FILE

    def set_category_metadata(self, category: str, description: str) -> Path:
        """Write CATEGORY.md for one category."""
        if not description.strip():
            raise ValueError("Category description cannot be empty.")
        meta_file = self._category_metadata_file(category)
        meta_file.parent.mkdir(parents=True, exist_ok=True)
        meta_file.write_text(description.strip() + "\n", encoding="utf-8")
        return meta_file

    def get_category_metadata(self, category: str) -> str | None:
        """Read CATEGORY.md for one category."""
        meta_file = self._category_metadata_file(category)
        if not meta_file.is_file():
            return None
        content = meta_file.read_text(encoding="utf-8").strip()
        return content or None

    def list_categories(self) -> list[dict[str, str]]:
        """List categories with optional metadata descriptions."""
        categories: set[str] = set()

        skills = self.list()
        skill_refs = {skill.ref for skill in skills}

        for skill in skills:
            parts = skill.category.split("/")
            for i in range(1, len(parts) + 1):
                categories.add("/".join(parts[:i]))

        # Include explicit category directories even if currently empty.
        # Exclude skill roots and anything inside a skill root (e.g. scripts/).
        if self.root_dir.is_dir():
            for path in self.root_dir.rglob("*"):
                if not path.is_dir():
                    continue
                rel = path.relative_to(self.root_dir).as_posix()
                if rel in ("", "."):
                    continue
                normalized_rel = self.normalize_category(rel, default_unarchived=False)
                if any(
                    normalized_rel == ref or normalized_rel.startswith(f"{ref}/")
                    for ref in skill_refs
                ):
                    continue
                categories.add(normalized_rel)

        if self.root_dir.is_dir():
            for meta_file in self.root_dir.rglob(self.CATEGORY_METADATA_FILE):
                category = meta_file.parent.relative_to(self.root_dir).as_posix()
                if category and category != ".":
                    categories.add(self.normalize_category(category, default_unarchived=False))

        out: list[dict[str, str]] = []
        for category in sorted(categories):
            desc = self.get_category_metadata(category)
            out.append({"category": category, "description": desc or ""})
        return out

    def import_skill(
        self,
        source_dir: Path,
        *,
        skill_name: str | None = None,
        category: str | None = None,
        overwrite: bool = False,
    ) -> LocalHubSkill:
        """Copy a skill directory (must contain SKILL.md) into the local hub."""
        source = source_dir.expanduser()
        if not source.is_dir():
            raise ValueError(f"Source directory not found: {source}")
        source_skill = source / "SKILL.md"
        if not source_skill.is_file():
            raise ValueError(f"Missing SKILL.md in source directory: {source}")

        ref = self.build_skill_ref(
            category=category,
            skill_name=(skill_name or source.name),
        )
        target = self.root_dir / Path(ref)
        if target.exists():
            if not overwrite:
                raise ValueError(
                    f"Skill {ref!r} already exists in local hub. "
                    "Set overwrite=true to replace it."
                )
            shutil.rmtree(target)

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
        entry = self._make_skill_entry(target)
        if entry is None:
            raise ValueError(f"Imported skill at {target} is invalid (missing SKILL.md).")
        return entry
