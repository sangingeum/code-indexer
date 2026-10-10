"""The documented flag contract: every command accepts --skip-stale-check.

SKILL-CLI.md states the flag is accepted by every command. This pins that
promise against the real click parameters, and pins that the commands whose
path runs the staleness probe actually ROUTE the flag into the Core (a flag
that parses but is ignored is the drift the contract exists to prevent).
"""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.main import get_command as typer_get_command  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()


def _commands() -> dict[str, Any]:
    group = typer_get_command(cli.app)
    out: dict[str, Any] = {}
    for name, cmd in group.commands.items():
        out[name] = cmd
        subcommands = getattr(cmd, "commands", None)
        if subcommands:
            for sub, subcmd in subcommands.items():
                out[f"{name} {sub}"] = subcmd
    return out


def _long_flags(cmd: Any) -> set[str]:
    return {o for p in cmd.params for o in getattr(p, "opts", [])}


def test_every_command_accepts_skip_stale_check():
    missing = [name for name, cmd in sorted(_commands().items())
               if "--skip-stale-check" not in _long_flags(cmd)]
    assert not missing, (
        "SKILL-CLI.md says every command accepts --skip-stale-check, but "
        f"these do not: {missing}")


@pytest.fixture()
def captured(tmp_path, monkeypatch):
    """A core whose _get_core records the skip_stale_check argument."""
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text(
        "def refresh_token():\n    return 1\n"
        "\n"
        "def run():\n    refresh_token()\n    return 0\n")
    core = Core(cfg, skip_stale_check=True)
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="flagproj")
    core.registry = reg
    core.manifest_for(entry.slug).close()  # materialize the manifest
    seen: list[bool] = []
    monkeypatch.setattr(cli, "_get_core", lambda skip: seen.append(skip) or core)
    yield seen, core, entry, project
    reg.close()


@pytest.mark.parametrize("argv", [
    ["find-callers", "refresh_token"],
    ["find-callees", "run"],
    ["deps", "a.py"],
    ["overview"],
])
def test_probe_commands_route_the_flag_into_the_core(captured, argv):
    seen, core, entry, project = captured
    result = runner.invoke(
        app, [*argv, "--project", str(project), "--skip-stale-check"])
    assert result.exit_code == 0, result.output
    assert seen == [True], (
        f"{argv[0]} accepted --skip-stale-check but did not route it to "
        f"the Core (saw {seen})")


def test_flag_defaults_to_false(captured):
    seen, core, entry, project = captured
    result = runner.invoke(app, ["find-callers", "refresh_token",
                                 "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert seen == [False]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
