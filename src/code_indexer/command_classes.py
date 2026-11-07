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
