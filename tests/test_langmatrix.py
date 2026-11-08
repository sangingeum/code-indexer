"""Language coverage matrix tests.

Per-language fixture files with snapshot assertions on chunks + symbols,
the fallback languages, and index-status reporting of
languages_without_ast. All offline (probes hit tree-sitter only).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402
from code_indexer import ts_chunker  # noqa: E402
from code_indexer.langmatrix import (  # noqa: E402
    capabilities, languages_without_ast, matrix, render_markdown)

runner = CliRunner()

# Snapshot facts per language: (ast chunking, symbol extracted, notes-ish).
EXPECTED = {
    "python": (True, True), "javascript": (True, True),
    "typescript": (True, True), "rust": (True, True), "go": (True, True),
    "java": (True, True), "c": (True, True), "cpp": (True, True),
    "csharp": (True, True), "kotlin": (True, True), "lua": (True, True),
    "php": (True, True), "swift": (True, True),
    "bash": (True, False),   # grammar parses but no symbol rows
    # Regex chunker despite a grammar mapping, so nothing in the file is an
    # AST declaration: the symbol table takes AST rows only (accuracy ruling
    # 2026-11-08), so a text window's first line is not a definition.
    "ruby": (False, False),
}


@pytest.mark.parametrize("lang", sorted(EXPECTED))
def test_language_capabilities_snapshot(lang):
    caps = capabilities(lang)
    exp_ast, exp_sym = EXPECTED[lang]
    assert caps["ast_chunking"] is exp_ast, caps
    assert caps["symbols"] is exp_sym, caps
    # The `references` cell names the rows the writer stores for this
    # language: query rows for an AST-reference language, the textual scan
    # (heuristic) for every other source language. The old assertion accepted
    # "none" or "heuristic", which let the column say "none" for a ruby file
    # that stores heuristic rows.
    ast_references = {"python", "javascript", "typescript", "c", "cpp", "go",
                      "csharp"}
    expected_refs = "ast" if lang in ast_references else "heuristic"
    assert caps["references"] == expected_refs, (lang, caps)


def test_visibility_real_languages():
    for lang in ("python", "rust", "csharp"):
        caps = capabilities(lang)
        assert caps["visibility"], f"{lang} should extract visibility"


def test_unsupported_falls_back_cleanly():
    # Unknown extension: window chunker, no crash.
    chunks = ts_chunker.chunk_text("fixture.nolang", "just some text\n")
    assert chunks and chunks[0].source != "ast"


def test_matrix_sorted_and_complete():
    rows = matrix()
    assert [r["language"] for r in rows] == \
        sorted(r["language"] for r in rows)
    assert "python" in {r["language"] for r in rows}
    assert "ruby" in {r["language"] for r in rows}
    assert "bash" in {r["language"] for r in rows}


def test_render_markdown_table():
    md = render_markdown()
    assert md.startswith("# Language coverage matrix")
    assert "| python | py | yes |" in md
    assert "regex chunker fallback despite grammar mapping" in md


def test_prose_and_config_rows_store_no_references():
    # A format the writer gives no reference rows must say "none"; a source
    # language with a textual scan must not say "none" (it stores rows).
    for lang in ("markdown", "json", "yaml", "toml", "html", "css",
                 "csproj", "sln", "props", "gitignore"):
        assert capabilities(lang)["references"] == "none", lang
    for lang in ("proto", "csx", "ruby", "bash"):
        assert capabilities(lang)["references"] == "heuristic", lang


def test_languages_without_ast_snapshot():
    # Every registry language outside the AST-references set. proto, csx, text
    # and log are declared rows: they are indexed for search and answer no
    # symbol question, so the list must name them rather than omit them. The
    # prose and configuration formats are named for the same reason -- a
    # .csproj is indexed and answers no definition.
    expected = sorted({"python", "javascript", "typescript", "tsx", "rust",
                       "go", "java", "c", "cpp", "csharp", "ruby", "php",
                       "bash", "lua", "swift", "kotlin", "markdown", "json",
                       "yaml", "toml", "html", "css", "proto", "text", "log",
                       "csx", "csproj", "sln", "props", "gitignore",
                       "codeindexignore"}
                      - {"python", "typescript", "tsx", "javascript",
                         "c", "cpp", "go", "csharp"})
    assert languages_without_ast() == expected


def test_index_status_reports_languages_without_ast(rig):
    core, reg, entry, project = rig
    result = runner.invoke(app, ["index-status", str(project)])
    assert result.exit_code == 0
    # The registry's list, under its own spelling: the answer-scoped list
    # travels inside `coverage` under a different key (see the field-name
    # test), so this line must not be read as the answer's own languages.
    assert "registry_languages_without_ast=[" in result.output


# ---------------------------------------------------------------------------
# fixture rig: index a polyglot fixture repo, verify chunk/symbol snapshots
# ---------------------------------------------------------------------------

@pytest.fixture()
def rig(tmp_path, monkeypatch):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    project.mkdir()
    fixtures = {
        "a.py": "def greet(name):\n    return name\n",
        "b.js": "function greet(name) { return name; }\n",
        "c.ts": "export function greet(n: string): string { return n; }\n",
        "d.go": "func Greet(name string) string { return name }\n",
        "e.rs": "pub fn greet(name: &str) -> &str { name }\n",
        "f.java": "public class G { public int greet() { return 1; } }\n",
        "g.c": "int add(int a, int b) { return a + b; }\n",
        "h.cpp": "class G { public: int greet() { return 1; } };\n",
        "i.cs": "public class G { public int Greet() { return 1; } }\n",
        "j.rb": "def greet(name)\n  name\nend\n",
        "k.php": "<?php\nfunction greet($n) { return $n; }\n",
        "l.kt": "fun greet(n: String): String { return n }\n",
        "m.sh": "greet() {\n  echo \"$1\"\n}\n",
        "n.nolang": "plain text without structure\n",
    }
    for name, text in fixtures.items():
        (project / name).write_text(text)
    core = Core(cfg)

    class StubEmbedder:
        def dimension(self):
            return 4

        def embed(self, texts):
            return [[0.1] * 4 for _ in texts]

        def query_instruction_text(self, q):
            return q

        def embed_with_errors(self, texts):
            return [[0.1] * 4 for _ in texts], []

        def close(self):
            pass

    class StubStore:
        def collection_exists(self, name):
            return True

        def create_collection(self, name, dim):
            pass

        def upsert_points(self, name, points):
            pass

        def purge_file_points(self, *a, **kw):
            return 0

        def count_points(self, name):
            return 0

        def search(self, *a, **kw):
            return []

    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="langproj")
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    return core, reg, entry, project


def test_polyglot_fixture_symbol_snapshots(rig):
    core, reg, entry, project = rig
    m = core.manifest_for(entry.slug)
    try:
        # AST-chunked languages get named symbols from the fixture.
        for fname, sym in (("a.py", "greet"), ("b.js", "greet"),
                           ("d.go", "Greet"), ("g.c", "add"),
                           ("h.cpp", "greet"), ("i.cs", "Greet")):
            syms = m.symbols_for_file(fname)
            assert any(s.name == sym for s in syms), (fname, syms)
        # Ruby: regex fallback still CHUNKS the file, and yields no symbol
        # row — a window's first line is not a declaration (accuracy ruling
        # 2026-11-08).
        assert m.get_file("j.rb") is not None
        assert m.symbols_for_file("j.rb") == []
        # Bash: no symbol row (weak extraction, documented).
        assert not m.symbols_for_file("m.sh")
    finally:
        m.close()


def _every_known_language() -> list[str]:
    from code_indexer.graph_extract import LANGUAGES_WITH_AST
    return sorted(set(languages_without_ast()) | set(LANGUAGES_WITH_AST))


def test_the_writer_and_the_matrix_publish_the_same_rule():
    """One rule, read by both: what the writer stamps is what the matrix says.

    A property over the language set, not two hand-written expectations: a
    rule re-encoded in one consumer only is exactly how the matrix came to
    say `none` for languages whose files stored heuristic rows.
    """
    from code_indexer.indexer import reference_rows
    from code_indexer.langmatrix import _FIXTURES, _LANG_EXT

    for lang in _every_known_language():
        ext = _LANG_EXT.get(lang, "txt")
        text = _FIXTURES.get(lang, "a note about a function\n")
        imports, refs, confidence = reference_rows(f"fixture.{ext}", text, lang)
        assert confidence in {"none", "ast", "heuristic"}, lang
        assert confidence == capabilities(lang)["references"], lang
        if confidence == "none":
            assert imports == [] and refs == [], lang


def test_every_stored_reference_row_carries_the_columns_confidence(tmp_path):
    """The store itself, not just the rule: rows exist under the stated
    confidence, and a `none` language stores none at all."""
    from code_indexer.langmatrix import _FIXTURES, _LANG_EXT
    from pass_rig import index_project, stub_core

    project = tmp_path / "langs"
    project.mkdir()
    written = {}
    for lang, text in _FIXTURES.items():
        ext = _LANG_EXT.get(lang)
        if not ext:
            continue
        name = f"fixture_{lang}.{ext}"
        (project / name).write_text(text)
        written[name] = lang
    core = stub_core(tmp_path)
    reg, entry = index_project(core, project)
    m = core.manifest_for(entry.slug)
    try:
        rows = m._conn.execute(
            "SELECT file, confidence, COUNT(*) FROM refs GROUP BY file,"
            " confidence").fetchall()
        stored: dict[str, dict[str, int]] = {}
        for file, confidence, n in rows:
            stored.setdefault(file, {})[confidence] = n
    finally:
        m.close()
    for name, lang in written.items():
        column = capabilities(lang)["references"]
        got = stored.get(name, {})
        assert set(got) <= {column}, (lang, column, got)
        if column == "none":
            assert got == {}, (lang, got)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))