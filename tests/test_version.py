"""The package ``__version__`` must match the version declared in ``pyproject.toml``.

Pure, offline test — reads the committed ``pyproject.toml`` (no network, no build).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import uijudge

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_version_matches_pyproject():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    pyproject_version = data["project"]["version"]
    assert uijudge.__version__ == pyproject_version
