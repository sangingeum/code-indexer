"""Every writer that reaches stderr is a case the read contract names.

A substring pin cannot keep that contract true: the gate's own count scanned
`typer.echo(..., err=True)` sites and missed the logging handler, which fires
at exit 0 on a `get-code-context` over a file edited after its last index, and
the sentence then denied it. So this module WALKS the source -- an AST walk
over the modules that can write -- and fails when a writer site, or a stream
choice, is not one the contract names. The sixth case is also measured end to
end, so the contract is checked against a run and not only against a list.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "code_indexer"
JSON_SCHEMA = ROOT / "docs" / "json-schema.md"

# The leading text each documented case writes, in the order the contract
# lists them. A site whose first argument starts with one of these is that
# case; the order matters where one prefix contains another.
_PREFIXES = (
    ("Warning: index is stale", "staleness banner"),
    ("coverage: ", "coverage line"),
    ("fingerprint meaning: ", "fingerprint meaning line"),
    ("note: this index is scoped to ", "scoped-index note"),
    ("note: ", "answer notes"),
    ("watch: ", "watcher family"),
    ("unwatch: ", "watcher family"),
    ("wrote ", "eval --out"),
)

# Sites whose first argument is computed at run time (a variable, a name built
# from fields), so no text is in the source. The (module, function) pair is the
# site's identity: moving a line inside its function is free, adding a site is
# a new row here -- and a row with no contract case fails the test below.
_COMPUTED_SITES = {
    ("cli", "_read_staleness"): "staleness banner",
    ("cli", "_read_staleness_many"): "staleness banner",
    ("cli", "semantic_search"): "staleness banner",
    ("cli", "get_code_context"): "staleness banner",
    ("cli", "plan_validate"): "failure line",
    ("cli", "plan_apply"): "failure line",
    ("watcher", "_run_pass"): "watcher family",
}

# The TWO writers that pick their stream per answer rather than passing literal
# `err=True`. The contract states the count, so the code must keep it.
_STREAM_CHOOSERS = {("cli", "_announce_coverage"), ("cli", "_emit_graph")}

# What each case must be findable AS in the contract section.
_CONTRACT_PHRASE = {
    "staleness banner": "Warning: index is stale",
    "coverage line": "`coverage:` line",
    "fingerprint meaning line": "`fingerprint meaning:` line",
    "scoped-index note": "`note: this index is scoped to",
    "answer notes": "`note: ...` lines of a graph answer",
    "watcher family": "print their result on stdout and their notes on stderr",
    "eval --out": "`eval --out` prints `wrote <path>` on stderr",
    "failure line": "`ErrorType: description`",
    "log note": "log note",
}


def _error_contract_section() -> str:
    text = JSON_SCHEMA.read_text(encoding="utf-8")
    section = text.split("## Error contract (non-JSON)", 1)[1]
    return section.split("\n## ", 1)[0]


def _literal_prefix(node: ast.Call) -> str:
    """The leading literal text of a call's first argument, if any."""

    def text(part: ast.AST) -> str:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            return part.value
        if isinstance(part, ast.JoinedStr):
            out: list[str] = []
            for piece in part.values:
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                    out.append(piece.value)
                else:
                    break
            return "".join(out)
        if isinstance(part, ast.BinOp):
            return text(part.left)
        return ""

    return text(node.args[0]) if node.args else ""


def _echo_sites(module: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(enclosing function, leading text) for every stderr echo, plus the
    writers whose stream is chosen at run time."""
    tree = ast.parse((SRC / f"{module}.py").read_text(encoding="utf-8"))
    literal: list[tuple[str, str]] = []
    stream_choice: list[tuple[str, str]] = []
    stack: list[str] = []

    class Walk(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute)
                    else getattr(func, "id", ""))
            entries = {kw.arg: kw.value for kw in node.keywords}
            if name == "echo" and "err" in entries:
                owner = stack[-1] if stack else ""
                value = entries["err"]
                is_true = (isinstance(value, ast.Constant)
                           and value.value is True)
                if is_true:
                    literal.append((owner, _literal_prefix(node)))
                else:
                    stream_choice.append((owner, ast.unparse(value)))
            self.generic_visit(node)

    Walk().visit(tree)
    return literal, stream_choice


def test_every_stderr_writer_is_a_case_the_contract_names():
    """The enumeration is the code's own list: a new site fails this suite."""
    section = _error_contract_section()
    flat = " ".join(section.split())
    unlisted: list[str] = []
    cases: set[str] = {"log note"}
    choosers: set[tuple[str, str]] = set()

    for module in ("cli", "watcher"):
        literal, stream_choice = _echo_sites(module)
        choosers.update((module, owner) for owner, _expr in stream_choice)
        for owner, prefix in literal:
            case = next((known for start, known in _PREFIXES
                         if prefix.startswith(start)), None)
            if case is None and not prefix:
                case = _COMPUTED_SITES.get((module, owner))
            if case is None:
                unlisted.append(f"{module}.{owner}: {prefix!r}")
            else:
                cases.add(case)

    assert not unlisted, (
        "stderr writers the read contract does not enumerate", unlisted)
    assert choosers == _STREAM_CHOOSERS, sorted(choosers)

    missing = [case for case in sorted(cases)
               if _CONTRACT_PHRASE[case] not in flat]
    assert not missing, ("contract does not name these cases", missing)

    # The two channels outside the enumeration, named by the contract.
    assert "errors.fail()" in flat
    assert "logging handler" in flat


def test_failure_lines_are_the_one_ErrorType_channel():
    """`print(..., file=sys.stderr)` exists once, in the fail() seam."""
    offenders: list[str] = []
    for path in sorted(SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if (kw.arg == "file" and isinstance(kw.value, ast.Attribute)
                        and kw.value.attr == "stderr"):
                    offenders.append(f"{path.name}:{node.lineno}")
    assert len(offenders) == 1, offenders
    assert offenders[0].startswith("errors.py:"), offenders
    flat = " ".join(_error_contract_section().split())
    assert "Failures print exactly one line" in flat


def test_the_log_note_a_successful_read_writes_is_the_documented_one(tmp_path):
    """Measured, not assumed: the sixth writer fires at exit 0."""
    project = tmp_path / "proj"
    project.mkdir()
    source = project / "mod.py"
    source.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    env = dict(os.environ, INDEX_ROOT=str(tmp_path / "state"))
    command = [sys.executable, "-m", "code_indexer.cli"]

    added = subprocess.run([*command, "add-project", str(project),
                            "--vectors", "off"],
                           capture_output=True, text=True, env=env)
    assert added.returncode == 0, added.stderr

    source.write_text("def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n",
                      encoding="utf-8")
    read = subprocess.run([*command, "get-code-context", "mod.py",
                           "--project", str(project),
                           "--start-line", "1", "--end-line", "3"],
                          capture_output=True, text=True, env=env)
    assert read.returncode == 0, read.stderr
    assert read.stdout.strip(), "the read answered nothing"

    note = "file changed since last index; line numbers may be shifted"
    lines = [line for line in read.stderr.splitlines() if line.strip()]
    assert lines, "the log note did not fire: the case is untested"
    documented = ("Warning: ", "coverage: ", "fingerprint meaning: ", "note: ",
                  "watch: ", "unwatch: ")
    for line in lines:
        assert line.startswith(documented) or line == note, line

    flat = " ".join(_error_contract_section().split())
    assert note in flat, "the contract does not name the note it prints"
