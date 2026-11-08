"""The top-level listing is grouped; the command surface is not.

ADR-0009 keeps the entry document to one page, so the way a new agent learns
which commands matter is the listing itself: ``Core`` holds the six every
survey respondent used, ``Advanced`` every other registered command. These
checks read the panel assignment from the command registry the CLI builds --
not from a list copied here -- so a command added without a panel fails the
suite instead of shipping under a default heading, and they pin every
registered command's own ``--help`` to the base tip so the grouping cannot
reach one.
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
# this module's own helper (CliRunner, COLUMNS=100). The KEY SET is not a
# hand-maintained list: ``test_the_pin_set_is_the_command_registry`` compares
# it with the typer registry, so every registered command -- hidden alias
# spellings included -- carries a pin and none can be silently exempt. A
# grouping edit must not move one byte of a command's own help; re-record a
# value only when that command's own help is deliberately edited.
HELP_SHA256 = {
    "add-project": "3c40042d1e809ef9a945dec81b4097de2b883f58a976ab982f9080af822932e5",
    "changed-symbols": "c32e58a721460d757cf9a1f8edcaecf994e819bc436c56393da5646db3397c1a",
    "deps": "b273519b0803f7abad57dcd8e267d9e59caf82d9d2349d85ac5861267d89dc77",
    "doctor": "e876133ea4e7a4a956a496e8c3eba1247d290368d571cb01f69bd1d9be6357cb",
    "eval": "2905d9158ec52cccc6cf8aef721192ebe7f357867378ca812d1435c370283afc",
    "eval-compare": "298652501bfcbf1a9ba91e6083375aa3c67779bcd200f2c561faf52d259dbfc4",
    "file-outline": "0fd2dacf4af1dc9c18b64ea3472705a073590ab8901340029cc6f6f75ea61c3d",
    "find-callees": "29f43f252176e490c2e9af1b979c9aeedc091fdecec44e6a04f2e48cd99a14e2",
    "find-callers": "3cd94f65f32e757ace545143426743c5b7f45f2d2a94069b8df67e73e89567f3",
    "find-definition": "942d1b25ff86c60190b6a746964b45de7ed3007cef8949986ade85d2d313b507",
    "find-references": "5c9f6d3ad0ad64a3c9fd61a5a6868620f68214efd75bfcb8d4aa1195bfe2d80b",
    "find-symbol": "18c1f1871ec4170e7b9f9bad259216f3192168af6b26ce07d7b2120951933c9a",
    "get-code-context": "4dd9e032d6bcb8219681fd63388b4735072eddd0b664057d8b9976afe5cbb4e3",
    "index-more": "1fe062648cb4bf63c16cc5e5393f6e4f5cb8d384a18e0ed949a0e932683f5ca8",
    "index-status": "cb1c55293291b32c4522baa4258323be4bba9db223abbfec50c8970ba02ca3ca",
    "list-projects": "b5f210cdca0c74f18eb5853a67cdc9627db653b7a6c8198dc83f852959b92b92",
    "lookup-project": "3a5e8aad291e70641cf1af94dcabaea7af38aaa151984216d8950a700336c0bc",
    "map": "f995e7d5b5be510f91c42e90c883ef4b3cc8ab92613c2f598ac9232d9b8e3f93",
    "outline": "fa81b8f04b9dcb7eed962bf4a3d7d097305de710cbfba3de5b35438fc85c5943",
    "overview": "34b6bdc776bb130eff4a7fdf8e8c21e62cbdf52a30862ec578a28859e8b94353",
    "plan-apply": "1c71a2d7c364f2f6f1d1d42da8de91834c59e2a0839ba4df1798af0d164d81cb",
    "plan-delete-declaration": "de9aa8113aa81a9f8718fa346a330790b971bdcda1486159efdd4ea8a36078ed",
    "plan-insert-symbol": "64cca8d8e8bdc68f09e18689bff87e6742a62ee592f224442871f99ce82a6328",
    "plan-replace-body": "738d981b80fd5c6d9cd0f878384064d5e22938c5f58c124a6b44ac43f2fedc8b",
    "plan-show": "8a64f4b605f36f7d95954b4ac23ecda6db9a0be17978dd71a3922aa1345b95fc",
    "plan-validate": "8b17537af3ee8b74d1b5db2e6d3fcd88c95d83fff18f4546ee0a8b3be84900c6",
    "reindex-project": "e76915ae52ec590aa645770ace3a970fb840bf0ff0865cb9ab4d5351ed9f941a",
    "remove-project": "1a37f2ef24f760adfafb6babed971e196d9ba84dd756edf31e4154c4634479f4",
    "semantic-search": "098d41eb03a885f3aa36598cef49e1997a15ea7e7a1fa07aa6c1acfe83c15b82",
    "skeleton": "4859e3b704822dbef3bc3a70c80578a21c6f8bcc7e5b6889f2ad4862a8163384",
    "sweep-orphans": "b690817aa571ec8fd303926dd6b9b4d1739eda170a5f6c9bd1c4c914409ab587",
    "unwatch": "15e1a02f5903d9fe8ceaefeda0796ad790521546c7111a6743a15c1a45c0d53b",
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


def test_the_pin_set_is_the_command_registry():
    """Every registered command has a pin, and no pin names a phantom."""
    assert set(HELP_SHA256) == set(_click_commands()), (
        "unpinned: "
        f"{sorted(set(_click_commands()) - set(HELP_SHA256))}; pinned but not "
        f"registered: {sorted(set(HELP_SHA256) - set(_click_commands()))}")


@pytest.mark.parametrize("name", sorted(HELP_SHA256))
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
    assert hashlib.sha256(captured[0]).hexdigest() == HELP_SHA256[name]


@pytest.mark.parametrize("name", sorted(HELP_SHA256))
def test_a_command_help_is_byte_identical_to_the_base_tip(name):
    """Every registered command's own --help matches the base tip byte for byte."""
    data = _help_bytes(name)
    assert hashlib.sha256(data).hexdigest() == HELP_SHA256[name], (
        f"{name} --help moved away from the base tip; the grouping is a "
        "top-level listing change only")


def test_no_command_help_carries_a_panel_title():
    """The panel names live on the group listing, never inside a command."""
    for name in _click_commands():
        text = _help_bytes(name).decode("utf-8")
        for panel in command_classes.HELP_PANEL_ORDER:
            assert f"─ {panel} ─" not in text, f"{name} --help shows {panel}"
