"""Every language the index takes must say what it gives back.

``proto`` files are indexed (their text embeds and searches) but carry no AST
chunks, no symbols and no references. The registry that answers "which
languages yield no AST" did not list proto, so a reader was told the language
was covered when ``find-definition`` could not answer for it at all.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer import ts_chunker  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.langmatrix import capabilities, languages_without_ast  # noqa: E402
from pass_rig import index_project, stub_core  # noqa: E402

runner = CliRunner()

PROTO = (
    'syntax = "proto3";\n'
    "package demo;\n"
    "\n"
    "message Greeting {\n"
    "  string name = 1;\n"
    "}\n"
    "\n"
    "service Greeter {\n"
    "  rpc Greet (Greeting) returns (Greeting);\n"
    "}\n"
)


def test_proto_is_named_as_a_language_without_ast():
    assert "proto" in languages_without_ast()


def test_proto_matrix_row_promises_no_symbols():
    caps = capabilities("proto")
    assert caps["language"] == "proto"
    assert caps["symbols"] is False
    assert caps["ast_chunking"] is False


def test_a_proto_file_indexes_with_no_symbol_rows(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "greeting.proto").write_text(PROTO)
    (project / "app.py").write_text("def greet(name):\n    return name\n")
    core = stub_core(tmp_path)
    reg, entry = index_project(core, project)
    m = core.manifest_for(entry.slug)
    try:
        row = m.get_file("greeting.proto")
        assert row is not None
        assert row.language == "proto"
        assert row.parse_verdict == "parse_unavailable"
        assert m.symbols_for_file("greeting.proto") == []
    finally:
        m.close()
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    payload = json.loads(runner.invoke(
        app, ["index-status", str(entry.path), "--json"]).output)
    assert "proto" in payload["registry_languages_without_ast"]
    assert "proto" in payload["coverage"]["answer_languages_without_ast"]


def test_proto_parse_verdict_is_honest():
    chunks, verdict = ts_chunker.parse_file("greeting.proto", PROTO)
    assert chunks
    assert verdict == "parse_unavailable"
    assert ts_chunker.extract_symbols("greeting.proto", PROTO, chunks) == []
