"""Main-agent tools for scoped environment variable administration."""

from __future__ import annotations

from typing import Any

from drclaw.config.env_store import EnvStore
from drclaw.tools.base import Tool


def _mask(value: str) -> str:
    if value == "":
        return "(empty)"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}***{value[-2:]}"


class SetEnvVarTool(Tool):
    """Set one environment variable in a configured scope."""

    def __init__(self, env_store: EnvStore) -> None:
        self._env_store = env_store

    @property
    def name(self) -> str:
        return "set_env_var"

    @property
    def description(self) -> str:
        return (
            "Set an environment variable in scope global/main/proj/equip. "
            "Supports scopes: global, main, proj:*, proj:<id>, equip:*, "
            "equip:<prototype>:*, equip:<prototype>:<runtime_id>."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
                "key": {"type": "string"},
                "value": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "required": ["scope", "key", "value"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        try:
            self._env_store.set_var(
                scope=params["scope"],
                key=params["key"],
                value=params["value"],
                overwrite=bool(params.get("overwrite", True)),
            )
        except ValueError as exc:
            return f"Error: {exc}"
        return (
            f"Set env var {params['key']!r} in scope {params['scope']!r} "
            f"(value={_mask(params['value'])})."
        )


class UnsetEnvVarTool(Tool):
    """Remove one environment variable from a configured scope."""

    def __init__(self, env_store: EnvStore) -> None:
        self._env_store = env_store

    @property
    def name(self) -> str:
        return "unset_env_var"

    @property
    def description(self) -> str:
        return "Unset an environment variable from a scope."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
                "key": {"type": "string"},
            },
            "required": ["scope", "key"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        try:
            removed = self._env_store.unset_var(scope=params["scope"], key=params["key"])
        except ValueError as exc:
            return f"Error: {exc}"
        if not removed:
            return (
                f"No-op: key {params['key']!r} was not present in scope {params['scope']!r}."
            )
        return f"Unset env var {params['key']!r} from scope {params['scope']!r}."


class ListEnvVarsTool(Tool):
    """List environment variables in a scope."""

    def __init__(self, env_store: EnvStore) -> None:
        self._env_store = env_store

    @property
    def name(self) -> str:
        return "list_env_vars"

    @property
    def description(self) -> str:
        return "List environment variables for one scope (masked by default)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "Defaults to global"},
                "reveal_values": {"type": "boolean"},
            },
        }

    async def execute(self, params: dict[str, Any]) -> str:
        scope = str(params.get("scope", "global"))
        reveal = bool(params.get("reveal_values", False))
        try:
            values = self._env_store.list_scope_vars(scope)
        except ValueError as exc:
            return f"Error: {exc}"
        if not values:
            return f"No environment variables configured in scope {scope!r}."
        lines = [f"Environment variables in scope {scope!r}:"]
        for key in sorted(values):
            value = values[key]
            shown = value if reveal else _mask(value)
            lines.append(f"- {key}={shown}")
        return "\n".join(lines)


class ListEnvScopesTool(Tool):
    """List configured environment-variable scopes."""

    def __init__(self, env_store: EnvStore) -> None:
        self._env_store = env_store

    @property
    def name(self) -> str:
        return "list_env_scopes"

    @property
    def description(self) -> str:
        return "List all scopes that currently have environment configuration."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, params: dict[str, Any]) -> str:
        scopes = self._env_store.list_scopes()
        if len(scopes) == 1:
            return "Configured env scopes: global only."
        return "Configured env scopes: " + ", ".join(scopes)
