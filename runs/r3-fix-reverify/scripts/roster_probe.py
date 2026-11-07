"""Who holds the flag pair: the derivation, the read roster, and the CLI help.

Usage: python roster_probe.py <tree>

Prints three independent enumerations so the parameterised ids can be checked
against something this round did not write:
  1. the typer registration intersected with READ_COMMANDS (the suite's rule),
  2. every command whose --help or parameter list carries --json,
  3. the top-level command names from `code-indexer --help`.
"""

from __future__ import annotations

import os
import sys

TREE = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(TREE, "src"))

import typer  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from code_indexer.cli import app  # noqa: E402
from code_indexer.command_classes import (  # noqa: E402
    COMPACT_FORMAT_COMMANDS, READ_COMMANDS,
)

group = typer.main.get_command(app)
commands = group.commands

derived: set[str] = set()
json_holders: list[str] = []
format_holders: list[str] = []
for name, command in sorted(commands.items()):
    opts = {opt for param in command.params for opt in getattr(param, "opts", ())}
    if "--json" in opts:
        json_holders.append(name)
    if "--format" in opts:
        format_holders.append(name)
    if "--json" in opts and "--format" in opts and name in READ_COMMANDS:
        derived.add(name)

print(f"tree={TREE}")
print(f"registered_commands={len(commands)}")
print(f"read_roster={len(READ_COMMANDS)} {sorted(READ_COMMANDS)}")
print(f"compact_roster={len(COMPACT_FORMAT_COMMANDS)} {sorted(COMPACT_FORMAT_COMMANDS)}")
print(f"json_holders={len(json_holders)} {json_holders}")
print(f"format_holders={len(format_holders)} {format_holders}")
print(f"derived_pair_roster={len(derived)} {sorted(derived)}")
print(f"read_commands_without_the_pair={sorted(set(READ_COMMANDS) - derived)}")
print(f"json_holders_outside_read_roster={sorted(set(json_holders) - set(READ_COMMANDS))}")

help_result = CliRunner().invoke(app, ["--help"])
names = []
for line in (help_result.stdout or "").splitlines():
    stripped = line.strip()
    if stripped and not stripped.startswith("--") and " " not in stripped:
        names.append(stripped)
print(f"help_listed_commands={len(names)} {names}")
print(f"registered_minus_help={sorted(set(commands) - set(names))}")
print(f"help_minus_registered={sorted(set(names) - set(commands))}")
