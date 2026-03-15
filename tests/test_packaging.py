"""Packaging and installer regression tests."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_install_script_only_references_defined_extras() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    defined_extras = set(pyproject["project"]["optional-dependencies"])
    install_script = (ROOT / "install.sh").read_text(encoding="utf-8")

    referenced_extras = set(re.findall(r"--extra\s+([A-Za-z0-9_-]+)", install_script))

    assert referenced_extras <= defined_extras
