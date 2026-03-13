"""Tests for scoped env storage and resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from drclaw.config.env_store import EnvStore
from drclaw.config.loader import load_config, save_config
from drclaw.config.schema import DrClawConfig


def _make_store(tmp_path: Path) -> tuple[EnvStore, Path]:
    data_dir = tmp_path / "data"
    config_path = data_dir / "config.json"
    cfg = DrClawConfig(data_dir=str(data_dir))
    save_config(cfg, config_path)
    return EnvStore(config=cfg, config_path=config_path), config_path


def test_set_and_get_effective_env_precedence(tmp_path: Path) -> None:
    store, _ = _make_store(tmp_path)
    store.set_var(scope="global", key="API_KEY", value="global")
    store.set_var(scope="proj:*", key="API_KEY", value="proj-all")
    store.set_var(scope="proj:p1", key="API_KEY", value="proj-p1")
    store.set_var(scope="equip:*", key="API_KEY", value="equip-all")
    store.set_var(scope="equip:analyzer:*", key="API_KEY", value="equip-analyzer")

    assert store.get_effective_env("main")["API_KEY"] == "global"
    assert store.get_effective_env("proj:p2")["API_KEY"] == "proj-all"
    assert store.get_effective_env("proj:p1")["API_KEY"] == "proj-p1"
    assert store.get_effective_env("equip:writer:r1")["API_KEY"] == "equip-all"
    assert store.get_effective_env("equip:analyzer:r1")["API_KEY"] == "equip-analyzer"


def test_scope_listing_and_unset(tmp_path: Path) -> None:
    store, _ = _make_store(tmp_path)
    assert store.list_scopes() == ["global"]

    store.set_var(scope="proj:p1", key="FOO", value="bar")
    assert store.list_scopes() == ["global", "proj:p1"]

    assert store.unset_var(scope="proj:p1", key="FOO") is True
    assert store.list_scopes() == ["global"]
    assert store.unset_var(scope="proj:p1", key="FOO") is False


def test_validation(tmp_path: Path) -> None:
    store, _ = _make_store(tmp_path)
    with pytest.raises(ValueError):
        store.set_var(scope="proj", key="API_KEY", value="x")
    with pytest.raises(ValueError):
        store.set_var(scope="global", key="bad-key", value="x")


def test_persistence_to_config_json(tmp_path: Path) -> None:
    store, config_path = _make_store(tmp_path)
    store.set_var(scope="global", key="API_KEY", value="g")
    store.set_var(scope="proj:p1", key="API_KEY", value="p1")

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert raw["env"]["global"]["API_KEY"] == "g"
    assert raw["env"]["scoped"]["proj:p1"]["API_KEY"] == "p1"

    loaded = load_config(config_path)
    assert loaded.env.global_["API_KEY"] == "g"
    assert loaded.env.scoped["proj:p1"]["API_KEY"] == "p1"
