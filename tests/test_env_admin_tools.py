"""Tests for env admin tools exposed to the main agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from drclaw.config.env_store import EnvStore
from drclaw.config.loader import save_config
from drclaw.config.schema import DrClawConfig
from drclaw.tools.env_admin_tools import (
    ListEnvScopesTool,
    ListEnvVarsTool,
    SetEnvVarTool,
    UnsetEnvVarTool,
)


def _make_store(tmp_path: Path) -> EnvStore:
    data_dir = tmp_path / "data"
    config_path = data_dir / "config.json"
    cfg = DrClawConfig(data_dir=str(data_dir))
    save_config(cfg, config_path)
    return EnvStore(config=cfg, config_path=config_path)


@pytest.mark.asyncio
async def test_set_list_unset_env_tools(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    setter = SetEnvVarTool(store)
    lister = ListEnvVarsTool(store)
    unsetter = UnsetEnvVarTool(store)

    res = await setter.execute(
        {"scope": "proj:p1", "key": "SERPER_API_KEY", "value": "sk-abc123"}
    )
    assert "Set env var" in res
    assert "sk-abc123" not in res

    listed = await lister.execute({"scope": "proj:p1"})
    assert "SERPER_API_KEY=sk-abc123" not in listed
    assert "SERPER_API_KEY=" in listed

    reveal = await lister.execute({"scope": "proj:p1", "reveal_values": True})
    assert "SERPER_API_KEY=sk-abc123" in reveal

    removed = await unsetter.execute({"scope": "proj:p1", "key": "SERPER_API_KEY"})
    assert "Unset env var" in removed


@pytest.mark.asyncio
async def test_list_env_scopes_tool(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _ = await SetEnvVarTool(store).execute(
        {"scope": "proj:*", "key": "SERPER_API_KEY", "value": "k1"}
    )
    result = await ListEnvScopesTool(store).execute({})
    assert "global" in result
    assert "proj:*" in result


@pytest.mark.asyncio
async def test_invalid_scope_errors(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    result = await SetEnvVarTool(store).execute(
        {"scope": "badscope", "key": "SERPER_API_KEY", "value": "k1"}
    )
    assert result.startswith("Error:")
