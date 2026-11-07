"""The top-level listing is grouped; the command surface is not.

ADR-0009 keeps the entry document to one page, so the way a new agent learns
which commands matter is the listing itself: ``Core`` holds the six every
survey respondent used, ``Advanced`` every other registered command. These
checks read the panel assignment from the command registry the CLI builds --
not from a list copied here -- so a command added without a panel fails the
suite instead of shipping under a default heading, and they pin a sample of
commands' own ``--help`` to the base tip so the grouping cannot reach one.
"""

from __future__ import annotations

import contextlib
import hashlib
import re

import pytest
import typer.rich_utils as typer_rich_utils
from typer.main import get_command as typer_get_command
from typer.models import CommandInfo
from typer.testing import CliRunner

import code_indexer.cli as cli_mod
from code_indexer import command_classes

# A bare command name starts a row; rich indents continuation lines into the
# description column, so they never match.
ROW = re.compile(r"^│ ([a-z][a-z0-9-]*) ")

# The width every capture is rendered at. It is pinned on the console itself
# (see _pinned_help_console), not merely passed in the environment: typer's
# help console decides its own width from ambient state.
HELP_WIDTH = 100
# Fixed width, dumped terminal: rich formats help from both, and a pinned hash
# must be measured under the same conditions every time.
HELP_ENV = {"COLUMNS": str(HELP_WIDTH), "TERM": "dumb"}
RUNNER = CliRunner()

CORE_NAMES = frozenset({
    "overview",
    "skeleton",
    "find-symbol",
    "get-code-context",
    "semantic-search",
    "index-status",
})

# ``code-indexer <name> --help`` as captured from the base tip 6ae37ad with
# this module's own helper (CliRunner, COLUMNS=100). A grouping edit must not
# move one byte of a command's own help; re-record only when that command's
# own help is deliberately edited.
BASE_TIP_HELP_SHA256 = {
    "overview": "34b6bdc776bb130eff4a7fdf8e8c21e62cbdf52a30862ec578a28859e8b94353",
    "find-symbol": "18c1f1871ec4170e7b9f9bad259216f3192168af6b26ce07d7b2120951933c9a",
    "semantic-search": "098d41eb03a885f3aa36598cef49e1997a15ea7e7a1fa07aa6c1acfe83c15b82",
    "plan-apply": "1c71a2d7c364f2f6f1d1d42da8de91834c59e2a0839ba4df1798af0d164d81cb",
    "watch": "2acdcaf83c46dec5a1437b164bb0012f92e6eb5b6f5317246d50e58b3168c429",
}


def _click_commands() -> dict[str, object]:
    """Every registered command, hidden alias spellings included."""
    return dict(typer_get_command(cli_mod.app).commands)


def _root_help() -> tuple[int, str]:
    result = _invoke(["--help"])
    return result.exit_code, result.output


def _panel_rows(output: str, panel: str) -> list[str]:
    """The command names rendered inside one panel, in listing order."""
    lines = output.splitlines()
    start = next(i for i, line in enumerate(lines) if f"─ {panel} ─" in line)
    end = next(i for i in range(start + 1, len(lines))
               if lines[i].startswith("╰"))
    return [match.group(1) for line in lines[start + 1:end]
            if (match := ROW.match(line))]


@contextlib.contextmanager
def _pinned_help_console():
    """Render help at one width and one colour scheme on every machine.

    typer's help console is built from module constants it reads when it is
    imported: ``MAX_WIDTH`` from ``TERMINAL_WIDTH``, and ``FORCE_TERMINAL``
    true whenever ``GITHUB_ACTIONS``, ``FORCE_COLOR`` or ``PY_COLORS`` is set.
    A CI runner sets the first, so there rich is told it has a terminal and,
    with ``TERM=dumb``, it fixes the width at 80 -- the same help wrapped
    differently than on a developer machine. Pin both for the capture instead
    of inheriting either.
    """
    saved = (typer_rich_utils.MAX_WIDTH, typer_rich_utils.FORCE_TERMINAL)
    typer_rich_utils.MAX_WIDTH = HELP_WIDTH
    typer_rich_utils.FORCE_TERMINAL = False
    try:
        yield
    finally:
        typer_rich_utils.MAX_WIDTH = saved[0]
        typer_rich_utils.FORCE_TERMINAL = saved[1]


def _invoke(argv: list[str], env: dict[str, str] = HELP_ENV):
    """Invoke the CLI with the help console pinned (see _pinned_help_console)."""
    with _pinned_help_console():
        return RUNNER.invoke(cli_mod.app, argv, env=env)


def _help_bytes(name: str, env: dict[str, str] = HELP_ENV) -> bytes:
    result = _invoke([name, "--help"], env=env)
    assert result.exit_code == 0, (name, result.exit_code, result.output[:400])
    return result.stdout.encode()


def test_every_registered_command_carries_exactly_one_panel():
    """The assignment is the registry's roster, one panel per command."""
    commands = _click_commands()
    mapping = command_classes.HELP_PANEL_BY_COMMAND
    assert not command_classes.unpanelled(commands), (
        "registered but unpanelled: "
        f"{command_classes.unpanelled(commands)} -- add each to "
        "HELP_PANEL_BY_COMMAND")
    assert not set(mapping) - set(commands), (
        f"panelled but not registered: {sorted(set(mapping) - set(commands))}")
    assert len(mapping) == len(commands), (
        f"{len(mapping)} assignments for {len(commands)} commands")
    for name, command in commands.items():
        assert command.rich_help_panel == mapping[name], (
            f"{name} renders under {command.rich_help_panel!r}, the map says "
            f"{mapping[name]!r}")
        assert command.rich_help_panel in command_classes.HELP_PANEL_ORDER


def test_a_planted_unpanelled_command_fails_the_assignment():
    """A command registered without a panel is caught, not defaulted."""
    def planted() -> None:  # pragma: no cover - never invoked
        """A probe command that no panel names."""

    probe = CommandInfo(name="planted-probe", callback=planted)
    with pytest.raises(KeyError) as excinfo:
        cli_mod.apply_help_panels([*cli_mod.app.registered_commands, probe])
    assert "planted-probe" in str(excinfo.value)
    assert command_classes.unpanelled([*_click_commands(), "planted-probe"]) == [
        "planted-probe"]


def test_root_help_renders_both_panels_at_exit_zero():
    exit_code, output = _root_help()
    assert exit_code == 0
    assert "─ Core ─" in output
    assert "─ Advanced ─" in output
    advanced = _panel_rows(output, command_classes.ADVANCED_PANEL)
    assert "plan-apply" in advanced
    assert "watch" in advanced
    assert "eval-compare" in advanced


def test_the_six_core_names_are_the_core_panel():
    """Core holds exactly the six entry commands, and lists them first."""
    exit_code, output = _root_help()
    assert exit_code == 0
    core = _panel_rows(output, command_classes.CORE_PANEL)
    assert set(core) == CORE_NAMES
    assert len(core) == len(CORE_NAMES)
    # The entry panel is rendered before the long tail.
    assert output.index("─ Core ─") < output.index("─ Advanced ─")
    rendered = core + _panel_rows(output, command_classes.ADVANCED_PANEL)
    assert set(rendered) == {
        name for name, command in _click_commands().items()
        if not command.hidden
    }


@pytest.mark.parametrize("name", sorted(BASE_TIP_HELP_SHA256))
def test_the_help_capture_does_not_read_the_ambient_width(name, monkeypatch):
    """Forty columns and two hundred give one hash: the width is pinned."""
    captured = []
    for ambient in ("40", "200"):
        monkeypatch.setenv("COLUMNS", ambient)
        # No COLUMNS in the invocation environment on purpose: the ambient
        # value is what a renderer that reads it would see.
        captured.append(_help_bytes(name, env={"TERM": "dumb"}))
    assert captured[0] == captured[1], (
        f"{name} --help rendered differently under COLUMNS=40 and COLUMNS=200")
    assert hashlib.sha256(captured[0]).hexdigest() == BASE_TIP_HELP_SHA256[name]


@pytest.mark.parametrize("name", sorted(BASE_TIP_HELP_SHA256))
def test_a_command_help_is_byte_identical_to_the_base_tip(name):
    """Five commands' own --help match the base tip byte for byte."""
    data = _help_bytes(name)
    assert hashlib.sha256(data).hexdigest() == BASE_TIP_HELP_SHA256[name], (
        f"{name} --help moved away from the base tip; the grouping is a "
        "top-level listing change only")


def test_no_command_help_carries_a_panel_title():
    """The panel names live on the group listing, never inside a command."""
    for name in _click_commands():
        text = _help_bytes(name).decode("utf-8")
        for panel in command_classes.HELP_PANEL_ORDER:
            assert f"─ {panel} ─" not in text, f"{name} --help shows {panel}"
