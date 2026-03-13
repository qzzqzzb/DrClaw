"""Persistent prototype discovery for equipment agents."""

from __future__ import annotations

import shutil
from pathlib import Path

from drclaw.equipment.models import EquipmentPrototype
from drclaw.utils.helpers import slugify


class EquipmentPrototypeStore:
    """Loads equipment prototypes from the local filesystem.

    Canonical layout:
    ``~/.drclaw/equipments/<equipment_name>/skills/``
    """

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = (root_dir or Path("~/.drclaw/equipments")).expanduser()

    def _normalize_name(self, name: str) -> str:
        normalized = slugify(name).replace("_", "-")
        if not normalized:
            raise ValueError(f"Invalid equipment name: {name!r}")
        return normalized

    def normalize_name(self, name: str) -> str:
        """Normalize user-supplied equipment name to canonical slug."""
        return self._normalize_name(name)

    def get(self, name: str) -> EquipmentPrototype | None:
        normalized = self._normalize_name(name)
        root = self.root_dir / normalized
        skills = root / "skills"
        if not root.is_dir() or not skills.is_dir():
            return None
        return EquipmentPrototype(name=normalized, root_dir=root, skills_dir=skills)

    def create(self, name: str) -> EquipmentPrototype:
        """Create a new equipment prototype skeleton with a skills directory."""
        normalized = self._normalize_name(name)
        root = self.root_dir / normalized
        skills = root / "skills"
        if root.exists():
            raise ValueError(f"Equipment prototype {normalized!r} already exists.")
        skills.mkdir(parents=True, exist_ok=False)
        return EquipmentPrototype(name=normalized, root_dir=root, skills_dir=skills)

    def delete(self, name: str) -> bool:
        """Delete an equipment prototype directory."""
        normalized = self._normalize_name(name)
        root = self.root_dir / normalized
        if not root.is_dir():
            return False
        shutil.rmtree(root)
        return True

    def list(self) -> list[EquipmentPrototype]:
        if not self.root_dir.exists():
            return []
        out: list[EquipmentPrototype] = []
        for entry in sorted(self.root_dir.iterdir(), key=lambda p: p.name):
            if not entry.is_dir():
                continue
            skills = entry / "skills"
            if skills.is_dir():
                out.append(EquipmentPrototype(name=entry.name, root_dir=entry, skills_dir=skills))
        return out
