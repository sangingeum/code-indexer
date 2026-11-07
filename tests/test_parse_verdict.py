"""Per-file parse verdicts (ADR-0004 section 7).

The verdict is the parser's own result, never the chunker's: the chunker
cannot fail, so a clean chunk list says nothing about whether the file parsed.
These tests pin the three values and their source, including the two cases a
chunker-derived verdict would get wrong: a file with a syntax error still
chunks, and a file the regex fallback handled is NOT "parsed".
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from code_indexer import ts_chunker  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()

CLEAN = "def one():\n    return 1\n"
# A syntax error the parser recovers from: the first function still yields a
# chunk, so the file is NOT on the regex fallback — the chunk list is fine and
# the verdict still has to be parse_error.
BROKEN = "def good():\n    return 1\n\n\ndef broken(:\n    return 2\n"
# No grammar for .zig in the language map: the regex fallback runs.
NO_GRAMMAR = "fn main() void {\n    return;\n}\n"
# A syntactically clean Python file with no declaration units: the AST walk
# finds nothing, so the chunker falls back to the regex chunker.
DECLARATION_FREE = "# a note, no declarations\n"


class StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]

    def embed_with_errors(self, texts):
        return [[0.1] * 4 for _ in texts], []

    def embed_query_once(self, text):
        return [0.1] * 4

    def query_instruction_text(self, query: str) -> str:
        return query

    def close(self) -> None:
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


@pytest.fixture()
def verdict_rig(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    import code_indexer.cli as cli
    cli._core = None
    cli._core_skip = False
    cfg = Config(ollama_url="http://stub", qdrant_url="http://stub",
                 embed_model="stub", index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=1,
                 watch_sweep_interval=3600)
    core = Core(cfg)
    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    project = tmp_path / "proj"
    project.mkdir()
    (project / "clean.py").write_text(CLEAN)
    (project / "broken.py").write_text(BROKEN)
    (project / "notes.zig").write_text(NO_GRAMMAR)
    (project / "decls_free.py").write_text(DECLARATION_FREE)
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="verdicts")
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    yield core, entry, project
    reg.close()
    cli._core = None
    cli._core_skip = False


def _verdicts(core: Core, entry) -> dict[str, str]:
    m = core.manifest_for(entry.slug)
    try:
        return {r[0]: r[1] for r in m._conn.execute(
            "SELECT path, parse_verdict FROM files")}
    finally:
        m.close()


# ---------------------------------------------------------------------------
# the three verdicts, per file
# ---------------------------------------------------------------------------

def test_a_clean_file_is_parsed(verdict_rig):
    core, entry, project = verdict_rig
    assert _verdicts(core, entry)["clean.py"] == "parsed"


def test_a_syntax_error_is_parse_error_even_though_it_chunks(verdict_rig):
    """The chunker recovers; the verdict must not inherit that recovery."""
    core, entry, project = verdict_rig
    chunks, verdict = ts_chunker.parse_file("broken.py", BROKEN)
    assert chunks, "the chunker produced chunks for this file"
    assert verdict == "parse_error"
    assert _verdicts(core, entry)["broken.py"] == "parse_error"


def test_a_language_without_a_grammar_is_parse_unavailable(verdict_rig):
    core, entry, project = verdict_rig
    assert "zig" not in ts_chunker.EXT_LANG.values()
    assert _verdicts(core, entry)["notes.zig"] == "parse_unavailable"


def test_the_regex_fallback_is_never_parsed(verdict_rig):
    """A clean file the AST walk produced no chunks for took the fallback."""
    core, entry, project = verdict_rig
    assert _verdicts(core, entry)["decls_free.py"] == "parse_unavailable"


def test_every_indexed_file_carries_exactly_one_verdict(verdict_rig):
    core, entry, project = verdict_rig
    values = _verdicts(core, entry)
    assert len(values) == 4, values
    assert set(values.values()) <= {"parsed", "parse_error",
                                    "parse_unavailable"}


# ---------------------------------------------------------------------------
# index-status counts them
# ---------------------------------------------------------------------------

def test_index_status_counts_the_verdicts(verdict_rig):
    core, entry, project = verdict_rig
    result = runner.invoke(app, ["index-status", str(project), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema"] == 1
    assert payload["parsed"] == 1
    assert payload["parse_error"] == 1
    assert payload["parse_unavailable"] == 2

    text = runner.invoke(app, ["index-status", str(project)])
    assert text.exit_code == 0, text.output
    assert "parse=parsed:1,parse_error:1,parse_unavailable:2" in text.stdout


def test_the_counts_are_always_all_three_keys(verdict_rig):
    """A zero count is reported, never omitted."""
    core, entry, project = verdict_rig
    m = core.manifest_for(entry.slug)
    try:
        counts = m.parse_verdict_counts()
    finally:
        m.close()
    assert counts == {"parsed": 1, "parse_error": 1,
                      "parse_unavailable": 2}
    assert list(counts) == ["parsed", "parse_error", "parse_unavailable"]
