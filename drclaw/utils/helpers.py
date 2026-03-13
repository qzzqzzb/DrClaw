"""Utility functions for DrClaw."""

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_AGENT_METADATA_FILE = "AGENT.yaml"


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists, return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(name: str) -> str:
    """Replace unsafe path characters with underscores. Raises ValueError on empty result."""
    result = _UNSAFE_CHARS.sub("_", name).strip()
    if not result:
        raise ValueError(f"Cannot produce a safe filename from {name!r}")
    return result


def slugify(text: str) -> str:
    """Convert text to a URL/filesystem-safe slug (lowercase, hyphens)."""
    return _SLUG_STRIP.sub("-", text.lower()).strip("-")


def timestamp() -> str:
    """Current UTC ISO timestamp."""
    return datetime.now(tz=timezone.utc).isoformat()


def get_data_dir(data_dir: str = "~/.drclaw") -> Path:
    """Return (and ensure) the data directory. Expands ~ in the path."""
    return ensure_dir(Path(data_dir).expanduser())


def _seed_dir_once(target_dir: Path, bundled_dir: Path) -> None:
    """Seed target_dir from bundled_dir only when target_dir does not exist."""
    if target_dir.exists():
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    if not bundled_dir.is_dir():
        return

    for entry in sorted(bundled_dir.iterdir(), key=lambda p: p.name):
        destination = target_dir / entry.name
        if entry.is_dir():
            shutil.copytree(entry, destination)
            continue
        if entry.is_file():
            shutil.copy2(entry, destination)


def _copy_missing_tree(source_dir: Path, target_dir: Path) -> None:
    """Recursively copy files/dirs from source_dir into target_dir without overwriting."""
    if not source_dir.is_dir():
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source_dir.iterdir(), key=lambda p: p.name):
        destination = target_dir / entry.name
        if entry.is_dir():
            _copy_missing_tree(entry, destination)
            continue
        if entry.is_file() and not destination.exists():
            shutil.copy2(entry, destination)


def _seed_missing_agent_hub_avatars(target_hub_dir: Path, bundled_hub_dir: Path) -> None:
    """Backfill missing avatar metadata for existing hub templates."""
    if not target_hub_dir.is_dir() or not bundled_hub_dir.is_dir():
        return
    for bundled_template in sorted(bundled_hub_dir.iterdir(), key=lambda p: p.name):
        if not bundled_template.is_dir():
            continue
        target_meta = target_hub_dir / bundled_template.name / _AGENT_METADATA_FILE
        bundled_meta = bundled_template / _AGENT_METADATA_FILE
        if not target_meta.is_file() or not bundled_meta.is_file():
            continue
        try:
            bundled_data = yaml.safe_load(bundled_meta.read_text(encoding="utf-8"))
            target_data = yaml.safe_load(target_meta.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(bundled_data, dict) or not isinstance(target_data, dict):
            continue
        bundled_avatar = bundled_data.get("avatar")
        if not isinstance(bundled_avatar, str) or not bundled_avatar.strip():
            continue
        target_avatar = target_data.get("avatar")
        if isinstance(target_avatar, str) and target_avatar.strip():
            continue
        target_data["avatar"] = bundled_avatar
        try:
            target_meta.write_text(
                yaml.safe_dump(target_data, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        except OSError:
            continue


def ensure_default_skill_dirs(data_dir: Path) -> None:
    """Ensure default data dirs and seed bundled defaults."""
    global_skills = ensure_dir(data_dir / "skills")
    package_root = Path(__file__).resolve().parents[1]

    # Seed frontend assets into user data dir, filling missing files only.
    _copy_missing_tree(package_root.parent / "assets", data_dir / "assets")

    # Seed local skill hub defaults from bundled templates (first create only).
    _seed_dir_once(data_dir / "local-skill-hub", package_root / "local_skill_hub")

    # Seed agent hub templates from bundled templates (first create only).
    bundled_agent_hub = package_root / "agent_hub" / "templates"
    target_agent_hub = data_dir / "agent-hub"
    _seed_dir_once(target_agent_hub, bundled_agent_hub)
    _seed_missing_agent_hub_avatars(target_agent_hub, bundled_agent_hub)

    # Seed user-global skills from packaged builtins (non-destructive).
    builtin_skills = package_root / "skills"
    if not builtin_skills.is_dir():
        return

    for entry in sorted(builtin_skills.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or not (entry / "SKILL.md").is_file():
            continue
        target = global_skills / entry.name
        if target.exists():
            continue
        shutil.copytree(entry, target)
