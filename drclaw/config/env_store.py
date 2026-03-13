"""Runtime store for scoped environment variables."""

from __future__ import annotations

import re
from pathlib import Path

from drclaw.config.loader import save_config
from drclaw.config.schema import DrClawConfig

_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_PROJ_SCOPE_RE = re.compile(r"^proj:[A-Za-z0-9._-]+$")
_EQUIP_SCOPE_RE = re.compile(r"^equip:[A-Za-z0-9._-]+:[A-Za-z0-9._-]+$")
_EQUIP_WILDCARD_SCOPE_RE = re.compile(r"^equip:[A-Za-z0-9._-]+:\*$")


class EnvStore:
    """Provides scoped env-var resolution and persistence through config.json."""

    def __init__(self, *, config: DrClawConfig, config_path: Path) -> None:
        self._config = config
        self._config_path = config_path

    def list_scopes(self) -> list[str]:
        """Return all configured scopes, including 'global'."""
        return ["global", *sorted(self._config.env.scoped.keys())]

    def list_scope_vars(self, scope: str) -> dict[str, str]:
        """Return scope vars as a shallow copy."""
        normalized = self._normalize_scope(scope)
        if normalized == "global":
            return dict(self._config.env.global_)
        return dict(self._config.env.scoped.get(normalized, {}))

    def set_var(self, *, scope: str, key: str, value: str, overwrite: bool = True) -> None:
        """Set one env var in a scope and persist config."""
        normalized = self._normalize_scope(scope)
        valid_key = self._normalize_key(key)
        if normalized == "global":
            bucket = self._config.env.global_
        else:
            bucket = self._config.env.scoped.setdefault(normalized, {})

        if not overwrite and valid_key in bucket:
            raise ValueError(f"Key {valid_key!r} already exists in scope {normalized!r}.")
        bucket[valid_key] = value
        self._persist()

    def unset_var(self, *, scope: str, key: str) -> bool:
        """Remove one env var from a scope and persist config."""
        normalized = self._normalize_scope(scope)
        valid_key = self._normalize_key(key)
        if normalized == "global":
            bucket = self._config.env.global_
        else:
            bucket = self._config.env.scoped.get(normalized)
            if bucket is None:
                return False

        removed = bucket.pop(valid_key, None) is not None
        if normalized != "global" and bucket == {}:
            self._config.env.scoped.pop(normalized, None)
        if removed:
            self._persist()
        return removed

    def get_effective_env(self, agent_id: str) -> dict[str, str]:
        """Resolve merged env map for one agent."""
        merged = dict(self._config.env.global_)
        for scope in self._candidate_scopes(agent_id):
            scoped = self._config.env.scoped.get(scope)
            if scoped:
                merged.update(scoped)
        return merged

    def _candidate_scopes(self, agent_id: str) -> list[str]:
        if agent_id == "main":
            return ["main"]
        if agent_id.startswith("proj:"):
            return ["proj:*", agent_id]
        if agent_id.startswith("equip:"):
            parts = agent_id.split(":", 2)
            if len(parts) == 3:
                prototype = parts[1]
                return ["equip:*", f"equip:{prototype}:*", agent_id]
            return ["equip:*", agent_id]
        return [agent_id]

    def _persist(self) -> None:
        save_config(self._config, self._config_path)

    def _normalize_scope(self, scope: str) -> str:
        normalized = (scope or "").strip()
        if not normalized:
            raise ValueError("scope is required")
        if normalized == "global":
            return normalized
        if normalized in {"main", "proj:*", "equip:*"}:
            return normalized
        if _PROJ_SCOPE_RE.fullmatch(normalized):
            return normalized
        if _EQUIP_SCOPE_RE.fullmatch(normalized):
            return normalized
        if _EQUIP_WILDCARD_SCOPE_RE.fullmatch(normalized):
            return normalized
        raise ValueError(
            "Invalid scope. Use one of: global, main, proj:*, proj:<id>, "
            "equip:*, equip:<prototype>:*, equip:<prototype>:<runtime_id>."
        )

    def _normalize_key(self, key: str) -> str:
        normalized = (key or "").strip()
        if not _ENV_KEY_RE.fullmatch(normalized):
            raise ValueError("Invalid env key. Use pattern ^[A-Z_][A-Z0-9_]*$.")
        return normalized
