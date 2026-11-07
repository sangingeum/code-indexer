"""Generate the command blocks of the agent surface from the CLI registry.

The CLI is the only source of truth for what a command is called, what it
accepts and whether it writes; this module renders that registry into the
markdown regions that document it, and into the roster the entry document
carries. Prose around a generated block (when to reach for a command, worked
examples, remedies) is written by hand -- it is the part a template cannot
guess.

A generated block lives between two marker comment lines:

    <!-- generated:<key>:begin -->
    ...generated text...
    <!-- generated:<key>:end -->

`regenerate()` is the only writer of those regions; `tests/test_docs.py`
rebuilds every block from this module and compares, so a hand edit inside one
fails the suite.

Usage:

    uv run python scripts/generate_skill_docs.py           # rewrite in place
    uv run python scripts/generate_skill_docs.py --check   # exit 1 on drift
"""

from __future__ import annotations

import sys
from pathlib import Path

from typer.main import get_command as typer_get_command

from code_indexer import command_classes

REPO = Path(__file__).resolve().parent.parent

# One reference per concern, and the commands whose flags live in it. The
# order here is the order the sections are written in.
REFERENCES: dict[str, tuple[str, ...]] = {
    "indexing.md": (
        "add-project",
        "index-more",
        "reindex-project",
        "remove-project",
        "sweep-orphans",
        "watch",
        "unwatch",
        "index-status",
        "list-projects",
        "lookup-project",
        "doctor",
    ),
    "query.md": (
        "overview",
        "skeleton",
        "map",
        "outline",
        "file-outline",
        "find-symbol",
        "find-definition",
        "find-references",
        "find-callers",
        "find-callees",
        "get-code-context",
        "deps",
        "changed-symbols",
        "semantic-search",
        "eval",
        "eval-compare",
    ),
    "plans.md": (
        "plan-replace-body",
        "plan-insert-symbol",
        "plan-delete-declaration",
        "plan-validate",
        "plan-show",
        "plan-apply",
    ),
    "machine-output.md": (),
    "failures.md": (),
}

COMMAND_REFERENCE: dict[str, str] = {
    command: reference
    for reference, commands in REFERENCES.items()
    for command in commands
}

# key -> file that carries the block
GENERATED: dict[str, Path] = {
    "roster": REPO / "SKILL-CLI.md",
    "json-commands": REPO / "skill" / "references" / "machine-output.md",
    **{
        f"commands-{name[: -len('.md')]}": REPO / "skill" / "references" / name
        for name, commands in REFERENCES.items()
        if commands
    },
}

BEGIN = "<!-- generated:{key}:begin -->"
END = "<!-- generated:{key}:end -->"


def _marker(key: str, edge: str) -> str:
    return (BEGIN if edge == "begin" else END).format(key=key)


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------

def click_group():
    import code_indexer.cli as cli_mod

    return typer_get_command(cli_mod.app)


def _walk(group, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for name, cmd in group.commands.items():
        out[prefix + name] = cmd
        if hasattr(cmd, "commands"):
            out.update(_walk(cmd, prefix + name + " "))
    return out


def registry() -> dict[str, object]:
    """Every registered command, hidden spellings included, by name."""
    return {name: cmd for name, cmd in _walk(click_group()).items() if " " not in name}


def command_class(name: str) -> str:
    """The read-or-write class ADR-0003 section 1 puts this command in."""
    if name in command_classes.WRITE_ONLY_COMMANDS:
        return "write"
    if name in command_classes.CONDITIONAL_WRITE_COMMANDS:
        return "write (with --apply)"
    if name in command_classes.READ_COMMANDS:
        return "read"
    raise KeyError(f"command {name!r} is in no command class")


def reference_for(name: str) -> str:
    """The one reference whose generated sections carry this command's flags."""
    return COMMAND_REFERENCE[name]


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _one_line(text: str | None, limit: int = 70) -> str:
    """The first sentence of a help string, collapsed and bounded."""
    if not text:
        return ""
    para = text.strip().split("\n\n", 1)[0]
    para = " ".join(para.split())
    sentence = para.split(". ", 1)[0].rstrip(".")
    if len(sentence) > limit:
        sentence = sentence[:limit].rsplit(" ", 1)[0] + " ..."
    return sentence


def _argument(cmd) -> list[str]:
    args: list[str] = []
    for p in cmd.params:
        if type(p).__name__ != "TyperArgument":
            continue
        suffix = " (required)" if getattr(p, "required", False) else " (optional)"
        args.append(f"`{p.opts[0]}`{suffix}")
    return args


def _flags(cmd) -> list[str]:
    flags: list[str] = []
    for p in cmd.params:
        opts = [o for o in getattr(p, "opts", []) if o.startswith("--")]
        if not opts:
            continue
        spelling = " / ".join(f"`{o}`" for o in opts)
        if getattr(p, "is_flag", False):
            flags.append(spelling)
            continue
        default = getattr(p, "default", None)
        shown = ""
        if default not in (None, "", False):
            shown = f" (default `{default}`)"
        flags.append(f"{spelling} <value>{shown}")
    return flags


def has_json(cmd) -> bool:
    return any("--json" in getattr(p, "opts", []) for p in cmd.params)


def command_block(name: str) -> str:
    """The generated section for one command: name, arguments, flags, class, JSON."""
    cmd = registry()[name]
    lines = [f"### `{name}`", ""]
    lines.append(f"- **Class**: {command_class(name)}")
    args = _argument(cmd)
    lines.append(f"- **Arguments**: {', '.join(args) if args else 'none'}")
    lines.append(f"- **Flags**: {', '.join(_flags(cmd)) if _flags(cmd) else 'none'}")
    if has_json(cmd):
        lines.append(
            "- **JSON**: `--json` accepted; envelope and recipes in "
            "[machine-output.md](machine-output.md)."
        )
    else:
        lines.append("- **JSON**: text only, no `--json`.")
    return "\n".join(lines)


def reference_block(reference: str) -> str:
    """All the command sections of one reference, in REFERENCES order."""
    return "\n\n".join(command_block(name) for name in REFERENCES[reference])


def _cell(text: str) -> str:
    """A markdown table cell: a pipe in a help string would break the row."""
    return text.replace("|", "\\|")


def roster_block() -> str:
    """The entry document's roster: name, class, one line, the reference.

    Grouped per reference, so the pointer is one heading per group instead of
    a link repeated on thirty-three rows: the roster has to fit ADR-0009's
    8 KiB entry-document budget along with everything else.
    """
    cmds = registry()
    lines: list[str] = []
    for reference, commands in REFERENCES.items():
        if not commands:
            continue
        lines.append(
            f"**{reference}** -- "
            f"[flags and examples](skill/references/{reference})"
        )
        lines.append("")
        lines.append("| Command | Class | Reach for it when |")
        lines.append("|---|---|---|")
        for name in commands:
            usage = _one_line(cmds[name].help, limit=52) or "--"
            lines.append(f"| `{name}` | {command_class(name)} | {_cell(usage)} |")
        lines.append("")
    return "\n".join(lines).rstrip()


def json_commands_block() -> str:
    """Which commands answer as data: the registry's own `--json` roster."""
    cmds = registry()
    lines = ["| Command | Class | JSON |", "|---|---|---|"]
    for commands in REFERENCES.values():
        for name in commands:
            mode = "`--json`" if has_json(cmds[name]) else "text only"
            lines.append(f"| `{name}` | {command_class(name)} | {mode} |")
    return "\n".join(lines)


def blocks() -> dict[str, str]:
    """Every generated region's body, by key."""
    out = {"roster": roster_block(), "json-commands": json_commands_block()}
    for name, commands in REFERENCES.items():
        if commands:
            out[f"commands-{name[: -len('.md')]}"] = reference_block(name)
    return out


# ---------------------------------------------------------------------------
# rewriting
# ---------------------------------------------------------------------------

def _region(text: str, key: str) -> tuple[int, int]:
    begin, end = _marker(key, "begin"), _marker(key, "end")
    start = text.index(begin) + len(begin)
    stop = text.index(end)
    return start, stop


def rendered(text: str, key: str, body: str) -> str:
    """`text` with the block for `key` replaced by `body`."""
    start, stop = _region(text, key)
    return text[:start] + "\n" + body + "\n" + text[stop:]


def regenerate(check: bool = False) -> list[str]:
    """Rewrite every block; with `check`, return the keys that differ."""
    drifted: list[str] = []
    for key, body in blocks().items():
        path = GENERATED[key]
        text = path.read_text(encoding="utf-8")
        start, stop = _region(text, key)
        current = text[start:stop].strip("\n")
        if current != body:
            drifted.append(key)
            if not check:
                path.write_text(rendered(text, key, body), encoding="utf-8")
    return drifted


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    check = "--check" in args
    drifted = regenerate(check=check)
    if check:
        for key in drifted:
            print(f"drift: {key} ({GENERATED[key].relative_to(REPO)})")
        return 1 if drifted else 0
    for key in drifted:
        print(f"wrote: {key} ({GENERATED[key].relative_to(REPO)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
