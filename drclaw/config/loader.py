"""Config file I/O — JSON load/save with graceful fallbacks."""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger
from pydantic import ValidationError

from drclaw.config.schema import DrClawConfig, TrayConfig

_LEGACY_TRAY_DAEMON_PROGRAMS: tuple[list[str], ...] = (
    ["uv", "run", "drclaw", "daemon", "--debug-full", "-f", "web"],
    ["uv", "run", "drclaw", "daemon", "-f", "web"],
)


def _migrate_legacy_defaults(data: object) -> object:
    """Migrate known legacy config defaults to current defaults."""
    if not isinstance(data, dict):
        return data
    # Migrate old single-provider format to multi-provider format.
    if "provider" in data and "providers" not in data:
        data["providers"] = {"default": data.pop("provider")}
        data.setdefault("active_provider", "default")
    tray = data.get("tray")
    if not isinstance(tray, dict):
        return data
    daemon_program = tray.get("daemon_program")
    if daemon_program not in _LEGACY_TRAY_DAEMON_PROGRAMS:
        return data
    tray["daemon_program"] = TrayConfig().daemon_program
    return data


def load_config(path: Path) -> DrClawConfig:
    """Load config from JSON file. Returns defaults on missing or corrupt file."""
    if not path.exists():
        return DrClawConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data = _migrate_legacy_defaults(data)
        return DrClawConfig.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("Corrupt config at {}: {}", path, exc)
        return DrClawConfig()


def save_config(config: DrClawConfig, path: Path) -> None:
    """Save config to JSON file. Creates parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        config.model_dump_json(indent=2, by_alias=True) + "\n",
        encoding="utf-8",
    )
