"""The two command classes: write commands and read commands.

Every registered command is in exactly one class. A read command opens no
write transaction, runs no incremental index pass, and contacts no service to
reach a staleness verdict: it answers from the index as of
``meta.last_indexed`` and reports the verdict. A write command changes the
manifest, the vector store or the sources.

The roster is closed. A cli command that is in neither list is a defect in
the roster, never a third class -- ``tests/test_read_contract.py`` compares
this roster with the typer registry so the defect cannot land silently.

``--skip-stale-check`` is accepted by every command and is inert; the flag
that turns a read invocation into a write is ``--refresh`` (one incremental
pass before the answer).
"""

from __future__ import annotations

from collections.abc import Iterable

WRITE_ONLY_COMMANDS: frozenset[str] = frozenset({
    "add-project",
    "index-more",
    # The three plan writers: each writes one plan document into the plan
    # directory and nothing else, and nothing at all under --print.
    "plan-delete-declaration",
    "plan-insert-symbol",
    "plan-replace-body",
    # The apply command: it writes the sources through filekeeper, and its
    # --dry-run shape writes nothing at all. Plan-apply and its receipt are
    # the only commands in this roster that touch a source file.
    "plan-apply",
    "remove-project",
    "reindex-project",
    "watch",
    "unwatch",
})

# ``sweep-orphans`` reads the manifest in its default dry-run shape and
# writes only when ``--apply`` is passed, so it is classified by invocation.
CONDITIONAL_WRITE_COMMANDS: frozenset[str] = frozenset({"sweep-orphans"})

READ_COMMANDS: frozenset[str] = frozenset({
    "changed-symbols",
    "deps",
    "doctor",
    "eval",
    "eval-compare",
    "file-outline",
    "find-callees",
    "find-callers",
    "find-definition",
    "find-references",
    "find-symbol",
    "get-code-context",
    "index-status",
    "list-projects",
    "lookup-project",
    "map",
    "outline",
    "overview",
    # Plan reads write nothing: validate and show open no transaction and
    # touch no file, and the reserved rename verb only refuses.
    "plan-show",
    "plan-validate",
    "semantic-search",
    "skeleton",
})

# The read commands that also have an honest compact rendering: one line per
# record, built only from fields the text form already prints. Every other
# read command takes ``--format text|json`` and REFUSES ``compact`` with a
# usage error naming what it does support (documented in the entry document),
# so a missing compact form is a stated refusal, never a silent fallback.
COMPACT_FORMAT_COMMANDS: frozenset[str] = frozenset({
    "find-definition",
    "find-symbol",
    "index-status",
    "outline",
    "overview",
    "semantic-search",
    "skeleton",
})

# The flag that makes a read invocation write (ADR-0003 section 4).
REFRESH_FLAG = "--refresh"


def is_write_command(name: str, *, sweep_apply: bool = False) -> bool:
    """True when this invocation writes.

    ``--refresh`` on a read command is a write invocation; the caller knows
    that flag, this helper classifies the command itself.
    """
    if name in WRITE_ONLY_COMMANDS:
        return True
    if name in CONDITIONAL_WRITE_COMMANDS:
        return sweep_apply
    return False


def all_commands() -> frozenset[str]:
    """The closed roster, alias spellings included, exactly once each."""
    return WRITE_ONLY_COMMANDS | CONDITIONAL_WRITE_COMMANDS | READ_COMMANDS


# ---------------------------------------------------------------------------
# Help panels: the listing a new agent reads, never the command set.
#
# Nothing here adds, removes, renames or re-flags a command. A panel is a
# heading on the top-level listing only: each command's own --help is built
# from its callback and never sees these names.
# ---------------------------------------------------------------------------

CORE_PANEL = "Core"
ADVANCED_PANEL = "Advanced"

# Panel order in ``code-indexer --help``. The entry point is listed first, so a
# reader meets the six commands a new agent needs before the long tail.
HELP_PANEL_ORDER: tuple[str, ...] = (CORE_PANEL, ADVANCED_PANEL)

# Every registered command, exactly one panel each. This dict is the single
# source of the assignment: ``cli.apply_help_panels`` reads it at import, and
# ``tests/test_help_panels.py`` compares it with the typer registry -- so a
# command added without a panel fails the suite instead of shipping under a
# default heading.
HELP_PANEL_BY_COMMAND: dict[str, str] = {
    # Core: orientation, structure, lookup, context, meaning, state. These are
    # the six commands every survey respondent used; nothing else qualifies.
    "overview": CORE_PANEL,
    "skeleton": CORE_PANEL,
    "find-symbol": CORE_PANEL,
    "get-code-context": CORE_PANEL,
    "semantic-search": CORE_PANEL,
    "index-status": CORE_PANEL,
    # Advanced: everything else. Registration order inside this panel already
    # clusters by task family, so it reads as: diagnostic and change reads,
    # graph reads, registry, index maintenance, freshness, file structure, the
    # plan family, then measurement.
    "doctor": ADVANCED_PANEL,
    "changed-symbols": ADVANCED_PANEL,
    "find-callers": ADVANCED_PANEL,
    "find-callees": ADVANCED_PANEL,
    "deps": ADVANCED_PANEL,
    "add-project": ADVANCED_PANEL,
    "index-more": ADVANCED_PANEL,
    "remove-project": ADVANCED_PANEL,
    "list-projects": ADVANCED_PANEL,
    "lookup-project": ADVANCED_PANEL,
    "sweep-orphans": ADVANCED_PANEL,
    "reindex-project": ADVANCED_PANEL,
    "watch": ADVANCED_PANEL,
    "unwatch": ADVANCED_PANEL,
    "outline": ADVANCED_PANEL,
    "find-definition": ADVANCED_PANEL,
    "find-references": ADVANCED_PANEL,
    "plan-replace-body": ADVANCED_PANEL,
    "plan-insert-symbol": ADVANCED_PANEL,
    "plan-delete-declaration": ADVANCED_PANEL,
    "plan-validate": ADVANCED_PANEL,
    "plan-show": ADVANCED_PANEL,
    "plan-apply": ADVANCED_PANEL,
    "eval": ADVANCED_PANEL,
    "eval-compare": ADVANCED_PANEL,
    # The two hidden alias spellings never render, but the registry holds
    # them, so they carry a panel like every other registered command.
    "map": ADVANCED_PANEL,
    "file-outline": ADVANCED_PANEL,
}


def help_panel(name: str) -> str:
    """The help panel for ``name``; an unpanelled command is a roster defect."""
    try:
        return HELP_PANEL_BY_COMMAND[name]
    except KeyError:
        raise KeyError(
            f"command {name!r} has no help panel: add it to "
            "HELP_PANEL_BY_COMMAND in command_classes.py"
        ) from None


def unpanelled(names: Iterable[str]) -> list[str]:
    """The names in ``names`` carrying no panel assignment, sorted."""
    return sorted(name for name in names if name not in HELP_PANEL_BY_COMMAND)
