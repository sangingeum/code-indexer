"""Packaging invariants: watchdog must be a main dependency, not a group.

The watch daemon's polling fallback (no watchdog installed) is a silent CPU
hog — a full hash scan of every registered project per quiet tick — so every
install path (uv tool install, uv sync, uv run) must carry watchdog.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_watchdog_is_a_main_dependency_not_a_group() -> None:
    data = tomllib.loads(PYPROJECT.read_text())
    deps = data["project"]["dependencies"]
    assert any(d.split(">")[0].split("<")[0].strip() == "watchdog" for d in deps), (
        "watchdog must appear in [project].dependencies"
    )
    groups = data.get("dependency-groups", {})
    assert "watch" not in groups, "the optional 'watch' dependency-group must not exist"


def test_requires_python_is_pinned_to_a_single_minor() -> None:
    data = tomllib.loads(PYPROJECT.read_text())
    spec = data["project"]["requires-python"]
    # Single-minor pin keeps uv.lock one-branch: no per-Python-version
    # resolution forks (qdrant-client/numpy previously split on 3.13).
    assert spec.startswith(">=3.1"), f"unexpected requires-python: {spec}"
    assert ",<3.13" in spec, "requires-python must cap below 3.13"