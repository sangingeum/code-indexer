"""Language coverage matrix tests (CI-25).

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
    "ruby": (False, True),   # regex fallback despite grammar mapping
}


@pytest.mark.parametrize("lang", sorted(EXPECTED))
def test_language_capabilities_snapshot(lang):
    caps = capabilities(lang)
    exp_ast, exp_sym = EXPECTED[lang]
    assert caps["ast_chunking"] is exp_ast, caps
    assert caps["symbols"] is exp_sym, caps
    if lang in ("python", "typescript", "javascript", "c", "cpp", "go"):
        assert caps["references"] == "ast", caps
    else:
        assert caps["references"] in ("heuristic", "none"), caps


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


def test_languages_without_ast_snapshot():
    # Every EXT_LANG language outside the AST-references set.
    expected = sorted({"python", "javascript", "typescript", "tsx", "rust",
                       "go", "java", "c", "cpp", "csharp", "ruby", "php",
                       "bash", "lua", "swift", "kotlin", "markdown", "json",
                       "yaml", "toml", "html", "css"}
                      - {"python", "typescript", "tsx", "javascript",
                         "c", "cpp", "go"})
    assert languages_without_ast() == expected


def test_index_status_reports_languages_without_ast(rig):
    core, reg, entry, project = rig
    result = runner.invoke(app, ["index-status", str(project)])
    assert result.exit_code == 0
    assert "languages_without_ast" in result.output


# ---------------------------------------------------------------------------
# fixture rig: index a polyglot fixture repo, verify chunk/symbol snapshots
# ---------------------------------------------------------------------------

@pytest.fixture()
def rig(tmp_path, monkeypatch):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
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
        # Ruby: regex fallback still yields a symbol row.
        assert any(s.name == "greet" for s in m.symbols_for_file("j.rb"))
        # Bash: no symbol row (weak extraction, documented).
        assert not m.symbols_for_file("m.sh")
    finally:
        m.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))