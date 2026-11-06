"""Symbol identity (ADR-0002 sections 1 and 2): a line-free key.

A symbol is identified by ``(file, qualified_name, symbol_type, decl_hash)``.
These tests pin the three consequences that matter to a caller: a definition
shifted by a line keeps its identity, two overloads that differ only by their
signature are two symbols, and the surrogate ``id`` is revision-scoped and
reported with the revision it belongs to. One migration test opens a
version-6 manifest, shows it is not re-keyed in place, and shows the ordinary
pass re-keying its rows from a real reparse.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from code_indexer.chunker import declaration_hash  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.graph_extract import EXTRACTION_VERSION  # noqa: E402
from code_indexer.manifest import SCHEMA_VERSION, Manifest  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()

NESTED_FILE = (
    "def helper():\n"
    "    return 1\n"
    "\n"
    "\n"
    "class Core:\n"
    "    def search(self):\n"
    "        return helper()\n"
)

OVERLOAD_FILE = (
    "def add(a):\n"
    "    return a\n"
    "\n"
    "\n"
    "def add(a, b):\n"
    "    return a + b\n"
)


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


def _build_rig(tmp_path, monkeypatch, files: dict[str, str]):
    """Index a throwaway project holding ``files``; return core + entry."""
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    import code_indexer.cli as cli
    cli._core = None
    cli._core_skip = False
    cfg = Config(ollama_url="http://stub", qdrant_url="http://stub",
                 embed_model="stub", index_root=str(tmp_path / "state"),
                 stale_ttl=0, embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=1,
                 watch_sweep_interval=3600)
    core = Core(cfg)
    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    project = tmp_path / "proj"
    project.mkdir()
    for name, text in files.items():
        (project / name).write_text(text)
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="identity")
    core.run_index(entry.slug, str(project))
    return core, reg, entry, project


@pytest.fixture()
def nested_rig(tmp_path, monkeypatch):
    core, reg, entry, project = _build_rig(
        tmp_path, monkeypatch, {"nested.py": NESTED_FILE})
    yield core, entry, project
    reg.close()
    import code_indexer.cli as cli
    cli._core = None
    cli._core_skip = False


def _rows(core: Core, entry, name: str | None = None,
          file: str | None = None):
    m = core.manifest_for(entry.slug)
    try:
        return m.find_symbols(name, substring=False, file=file, limit=None)
    finally:
        m.close()


# ---------------------------------------------------------------------------
# the key is line-free
# ---------------------------------------------------------------------------

def test_a_definition_shifted_by_a_line_keeps_its_identity(nested_rig):
    """A blank line above the file changes the range, never the identity."""
    core, entry, project = nested_rig
    before = {r.qualified_name: r for r in _rows(core, entry)}
    assert "Core.search" in before

    target = project / "nested.py"
    target.write_text("\n\n" + NESTED_FILE)
    core.run_index(entry.slug, str(project))
    after = {r.qualified_name: r for r in _rows(core, entry)}

    assert set(after) == set(before)
    for name, row in after.items():
        assert row.decl_hash == before[name].decl_hash
        assert row.symbol_type == before[name].symbol_type
    shifted = after["Core.search"]
    assert shifted.start_line == before["Core.search"].start_line + 2
    assert shifted.end_line == before["Core.search"].end_line + 2
    # The surrogate id is revision-scoped: the pass re-inserts every row, so
    # an id from the earlier revision proves nothing about this one.
    assert shifted.symbol_id != before["Core.search"].symbol_id


def test_decl_hash_is_the_normalized_signature():
    """The hash is over whitespace-collapsed declaration text, nothing else."""
    assert declaration_hash("def search(self):") == declaration_hash(
        "  def   search(self):  ")
    assert declaration_hash("def search(self):") != declaration_hash(
        "def search(self, x):")
    # No signature at all: the observed first line is the basis, so the key
    # is total for every row.
    assert declaration_hash(None, "class Core:") == declaration_hash(
        "class Core:")


def test_overloads_differing_only_by_signature_are_two_identities(
        tmp_path, monkeypatch):
    core, reg, entry, project = _build_rig(
        tmp_path, monkeypatch, {"overloads.py": OVERLOAD_FILE})
    try:
        rows = _rows(core, entry, name="add")
        assert len(rows) == 2, [(r.start_line, r.decl_hash) for r in rows]
        # Same identity name, different declaration: two identities.
        assert {r.qualified_name for r in rows} == {"add"}
        assert len({r.decl_hash for r in rows}) == 2
        assert len({r.symbol_id for r in rows}) == 2
        assert [r.start_line for r in rows] == [1, 5]
    finally:
        reg.close()
        import code_indexer.cli as cli
        cli._core = None
        cli._core_skip = False


# ---------------------------------------------------------------------------
# qualified name and container
# ---------------------------------------------------------------------------

def test_qualified_name_and_container_nested_and_top_level(nested_rig):
    core, entry, project = nested_rig
    rows = {r.name: r for r in _rows(core, entry)}

    assert rows["helper"].qualified_name == "helper"
    assert rows["helper"].container is None

    assert rows["search"].qualified_name == "Core.search"
    assert rows["search"].container == "Core"
    assert rows["Core"].qualified_name == "Core"
    assert rows["Core"].container is None
    # language is copied from the file's row, so a filter never joins.
    assert rows["search"].language == "python"


def test_symbol_id_is_reported_beside_the_index_revision(nested_rig, monkeypatch):
    core, entry, project = nested_rig
    import code_indexer.cli as cli
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["find-symbol", "search", "--project",
                                 str(project), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    row = payload["symbols"][0]
    assert row["qualified_name"] == "Core.search"
    assert row["container"] == "Core"
    assert row["decl_hash"].startswith("sha256:")
    assert isinstance(row["symbol_id"], int)
    assert payload["index_revision"] == "1"


# ---------------------------------------------------------------------------
# migration: a version-6 manifest, then a real reparse
# ---------------------------------------------------------------------------

_V6_DDL = """
CREATE TABLE files (path TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
    size INTEGER NOT NULL, chunk_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok', mtime_ns INTEGER, inode INTEGER,
    loc INTEGER, language TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE symbols (file TEXT NOT NULL, name TEXT NOT NULL,
    symbol_type TEXT NOT NULL, start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'regex',
    signature TEXT, visibility TEXT, PRIMARY KEY (file, name, start_line));
CREATE TABLE imports (file TEXT NOT NULL, raw_target TEXT NOT NULL,
    resolved_file TEXT, kind TEXT NOT NULL, line INTEGER NOT NULL,
    PRIMARY KEY (file, raw_target, line));
CREATE TABLE refs (file TEXT NOT NULL, line INTEGER NOT NULL,
    from_symbol TEXT, to_name TEXT NOT NULL, relationship TEXT NOT NULL,
    confidence TEXT NOT NULL, receiver TEXT,
    PRIMARY KEY (file, line, to_name, relationship));
CREATE TABLE index_errors (file TEXT NOT NULL, chunk_index INTEGER,
    error TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (file, chunk_index));
CREATE TABLE symbol_refs (file TEXT NOT NULL, line INTEGER NOT NULL,
    src_symbol TEXT, relationship TEXT NOT NULL, target TEXT NOT NULL,
    PRIMARY KEY (file, line, relationship, target));
INSERT INTO meta VALUES ('schema_version', '6');
INSERT INTO symbols VALUES ('pre7.py', 'search', 'function', 1, 2, 'ast',
    'def search(self):', 'public');
"""


def test_a_version_six_manifest_migrates_and_a_reparse_rekeys_its_rows(
        tmp_path, monkeypatch):
    """A v6 manifest opens, migrates, and the pass re-keys its rows.

    The migration cannot re-key a row itself: qualified_name needs the
    container chain the old extractor never stored, and decl_hash needs a
    signature a legacy row may not carry. So the old rows are replaced empty
    and the ordinary pass writes real identities. Counts are asserted, not
    described: one loose v6 row before, three identified rows after.
    """
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    cfg = Config(ollama_url="http://stub", qdrant_url="http://stub",
                 embed_model="stub", index_root=str(tmp_path / "state"),
                 stale_ttl=0, embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=1,
                 watch_sweep_interval=3600)
    core = Core(cfg)
    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pre7.py").write_text("(placeholder)\n")
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="legacy")
    try:
        # A version-6 manifest, hand-built, holding one old-key row.
        mdir = os.path.join(cfg.index_root, entry.slug)
        os.makedirs(mdir, exist_ok=True)
        db = os.path.join(mdir, "manifest.db")
        conn = sqlite3.connect(db)
        conn.executescript(_V6_DDL)
        conn.commit()
        conn.close()

        m = Manifest(db)
        try:
            assert m.get_meta("schema_version") == SCHEMA_VERSION == "7"
            old_rows = int(m._conn.execute(
                "SELECT COUNT(*) FROM symbols").fetchone()[0])
            assert old_rows == 0  # the old-key row was not re-keyed in place
            # The staged target columns exist and are NULL, as promised.
            staged = m._conn.execute(
                "SELECT COUNT(*) FROM refs WHERE target_symbol_id IS NOT NULL"
                " OR resolution IS NOT NULL").fetchone()[0]
            assert staged == 0
        finally:
            m.close()

        # Now the real file, and the ordinary pass that re-keys the rows.
        (project / "pre7.py").write_text(NESTED_FILE)
        core.run_index(entry.slug, str(project))
        m = core.manifest_for(entry.slug)
        try:
            row_count = int(m._conn.execute(
                "SELECT COUNT(*) FROM symbols").fetchone()[0])
            with_hash = int(m._conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE decl_hash LIKE 'sha256:%'"
            ).fetchone()[0])
            with_container = int(m._conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE container IS NOT NULL"
            ).fetchone()[0])
            assert m.get_meta("schema_version") == "7"
            assert m.get_meta("extraction_version") == str(EXTRACTION_VERSION)
        finally:
            m.close()
        # NESTED_FILE holds helper, Core and Core.search: three identities,
        # against the ONE loose row the v6 manifest carried.
        assert row_count == 3, f"rows after reparse={row_count} (v6 had 1)"
        assert with_hash == 3
        assert with_container == 1  # only the method is nested
    finally:
        reg.close()
        import code_indexer.cli as cli
        cli._core = None
        cli._core_skip = False
