"""Config file I/O — JSON load/save with graceful fallbacks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import ValidationError

from drclaw.config.schema import DrClawConfig, TrayConfig

_LEGACY_TRAY_DAEMON_PROGRAMS: tuple[list[str], ...] = (
    ["uv", "run", "drclaw", "daemon", "--debug-full", "-f", "web"],
    ["uv", "run", "drclaw", "daemon", "-f", "web"],
)


class ConfigLoadError(ValueError):
    """Raised when config exists but cannot be parsed or validated."""


def _parse_config_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigLoadError(
            f"Invalid config JSON at {path}:{exc.lineno}:{exc.colno} ({exc.msg})"
        ) from exc


def _format_validation_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(part) for part in err.get("loc", ())) or "<root>"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def _validate_config_model(path: Path, data: object) -> DrClawConfig:
    try:
        config = DrClawConfig.model_validate(data)
        # Access runtime-dependent alias so invalid active_provider fails early.
        _ = config.active_provider_config
        return config
    except ValidationError as exc:
        raise ConfigLoadError(
            f"Invalid config schema at {path} ({_format_validation_error(exc)})"
        ) from exc
    except ValueError as exc:
        raise ConfigLoadError(f"Invalid config at {path} ({exc})") from exc


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
        data = _parse_config_json(path)
        data = _migrate_legacy_defaults(data)
        return _validate_config_model(path, data)
    except ConfigLoadError as exc:
        logger.warning("Corrupt config at {}: {}", path, exc)
        return DrClawConfig()


def load_config_strict(path: Path) -> DrClawConfig:
    """Load config from JSON file and raise if the file cannot be parsed or validated."""
    if not path.exists():
        return DrClawConfig()
    data = _parse_config_json(path)
    data = _migrate_legacy_defaults(data)
    return _validate_config_model(path, data)


def save_config(config: DrClawConfig, path: Path) -> None:
    """Save config to JSON file. Creates parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        config.model_dump_json(indent=2, by_alias=True) + "\n",
        encoding="utf-8",
    )
