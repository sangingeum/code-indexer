"""The symbol and reference tables hold declarations, never prose.

Only a declaration parsed from source with an AST may enter the symbol table.
Prose and configuration formats (markdown, plain text, logs, json, yaml,
toml, html, css) keep their chunks -- they are searchable text -- and a file
row, and contribute NOTHING to symbols or references. Before this rule a
markdown file's headings and sentences landed in the symbol table, so
``find-symbol`` answered ``ARCHITECTURE.md:211-234 registry (function)``.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer import ts_chunker  # noqa: E402
from code_indexer.chunker import Chunk  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from pass_rig import index_project, stub_core  # noqa: E402

runner = CliRunner()

ARCHITECTURE = (
    "# Architecture\n"
    "\n"
    "The registry stores every project row; see below.\n"
    "\n"
    "## The registry (function)\n"
    "\n"
    "`registry` maps a slug to a path (and nothing else).\n"
    "\n"
    "class Registry: owns the database handle\n"
    "\n"
    "    def load(path) -> rows\n"
)

SOURCE = "def greet(name):\n    return name\n"


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "ARCHITECTURE.md").write_text(ARCHITECTURE)
    (project / "NOTES.txt").write_text("registry (function)\n\nsome prose\n")
    (project / "run.log").write_text("registry: started\nregistry: stopped\n")
    (project / "app.py").write_text(SOURCE)
    core = stub_core(tmp_path)
    reg, entry = index_project(core, project)
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    return core, entry, project


@pytest.mark.parametrize("name", ["ARCHITECTURE.md", "NOTES.txt", "run.log"])
def test_a_prose_file_contributes_no_symbols(rig, name):
    core, entry, _ = rig
    m = core.manifest_for(entry.slug)
    try:
        assert m.symbols_for_file(name) == []
    finally:
        m.close()


@pytest.mark.parametrize("name", ["ARCHITECTURE.md", "NOTES.txt", "run.log"])
def test_a_prose_file_contributes_no_references(rig, name):
    core, entry, _ = rig
    m = core.manifest_for(entry.slug)
    try:
        assert m.callees_of_file_symbol(name, None) == []
    finally:
        m.close()


@pytest.mark.parametrize("name", ["ARCHITECTURE.md", "NOTES.txt", "run.log"])
def test_a_prose_file_keeps_its_chunks_and_its_file_row(rig, name):
    core, entry, _ = rig
    m = core.manifest_for(entry.slug)
    try:
        row = m.get_file(name)
        assert row is not None
        assert row.chunk_count > 0
        symbols = [r[0] for r in m._conn.execute(
            "SELECT symbol FROM chunks WHERE file = ?", (name,))]
        assert set(symbols) == {None}
    finally:
        m.close()


def test_find_symbol_answers_nothing_for_a_prose_heading(rig):
    core, entry, _ = rig
    result = runner.invoke(app, ["find-symbol", "registry", "--project", str(entry.path)])
    assert result.exit_code == 0
    assert "ARCHITECTURE.md" not in result.output
    assert "NOTES.txt" not in result.output


def test_skeleton_lists_a_prose_file_with_zero_symbols(rig):
    core, entry, _ = rig
    payload = json.loads(runner.invoke(
        app, ["skeleton", "--project", str(entry.path), "--json"]).output)
    files = {f["file"]: f for f in payload["files"]}
    assert files["ARCHITECTURE.md"]["total_symbols"] == 0
    assert files["ARCHITECTURE.md"]["symbols"] == []
    # the python file still carries its declaration
    assert [s["name"] for s in files["app.py"]["symbols"]] == ["greet"]


def test_a_regex_chunk_cannot_become_a_symbol_row():
    # The rule, at the seam: a guess from a text window is not a declaration.
    chunk = Chunk(text="def greet(name):\n    return name\n",
                  chunk_hash="sha256:x", symbol="greet", start_line=1,
                  end_line=2, chunk_index=0, source="regex")
    assert ts_chunker.extract_symbols("app.py", chunk.text, [chunk]) == []


def test_an_ast_chunk_still_becomes_a_symbol_row():
    text = SOURCE
    chunks, verdict = ts_chunker.parse_file("app.py", text)
    rows = ts_chunker.extract_symbols("app.py", text, chunks)
    assert verdict == "parsed"
    assert [r["name"] for r in rows] == ["greet"]
