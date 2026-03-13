"""Shared test fixtures and helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.mocks import MockProvider


def read_jsonl_entries(path: Path) -> list[dict]:
    """Read a JSONL file and return parsed entries."""
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Provide a temporary data directory for tests."""
    d = tmp_path / "drclaw_data"
    d.mkdir()
    return d


@pytest.fixture
def mock_provider() -> MockProvider:
    """Return a fresh MockProvider instance."""
    return MockProvider()
