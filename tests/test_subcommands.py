"""Phase-1 token-reduction subcommand tests (subcommand design §4).

Covers: schema v3 migration (v2 db -> columns added, old rows NULL, version
bumped exactly once), signature extraction (python + cpp fixtures, decorated
defs, qualified names, >120-char truncation), visibility rules, regex
fallback, command-level skeleton/outline/find-symbol dense text + JSON
contract, --skip-stale-check, WAL-concurrency guard, and the §4 edge cases.
"""

import json
import os
import shutil
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.chunker import chunk as fallback_chunk  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core, MIGRATION_HINT  # noqa: E402
from code_indexer.indexer import Indexer  # noqa: E402
from code_indexer.manifest import Manifest, SCHEMA_VERSION  # noqa: E402
from code_indexer import ts_chunker  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402


class StubEmbedder:
    def dimension(self):
        return 4

    def embed(self, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class StubStore:
    def collection_exists(self, name):
        return True

    def create_collection(self, name, dim):
        pass

    def upsert_points(self, name, points):
        pass

    def purge_file_points(self, name, project, path, min_chunk_index=0):
        pass


@pytest.fixture()
def project():
    root = tempfile.mkdtemp(prefix="ci-sub-")
    os.makedirs(os.path.join(root, "src"))
    with open(os.path.join(root, "src", "session.py"), "w") as f:
        f.write(
            "class FileTransferSession:\n"
            '    """Session doc."""\n'
            "    def start(self):\n"
            "        pass\n"
            "    def _hidden(self):\n"
            "        pass\n"
            "\n"
            "def public_fn(a, b):\n"
            "    return a + b\n"
        )
    with open(os.path.join(root, "src", "empty.py"), "w") as f:
        f.write("x = 1\n")  # file with no declarations
    with open(os.path.join(root, "src", "empty0.py"), "w") as f:
        f.write("")  # zero-byte file
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def cfg():
    return Config(ollama_url="", qdrant_url="", embed_model="stub",
                  index_root=tempfile.mkdtemp(prefix="ci-subidx-"),
                  stale_ttl=60, embed_batch=48, upsert_batch=256,
                  max_file_bytes=1048576, watch_debounce=3,
                  watch_sweep_interval=300)


@pytest.fixture()
def indexed(cfg, project):
    """Index the fixture project into a manifest + registry-backed core."""
    m = Manifest(os.path.join(cfg.index_root, "m.db"))
    indexer = Indexer(cfg, StubEmbedder(), StubStore())
    indexer.index_project(project, "sub", m)
    yield m
    m.close()
    shutil.rmtree(cfg.index_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Unit: schema v3 migration (§4)
# ---------------------------------------------------------------------------

def _make_v2_manifest(db_path):
    """Hand-build a schema-v2 manifest with old rows and version=2."""
    conn = __import__("sqlite3").connect(db_path)
    conn.executescript("""
        CREATE TABLE files (path TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
            size INTEGER NOT NULL, chunk_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'ok');
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE symbols (file TEXT NOT NULL, name TEXT NOT NULL,
            symbol_type TEXT NOT NULL, start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'regex',
            PRIMARY KEY (file, name, start_line));
        CREATE TABLE symbol_refs (file TEXT NOT NULL, line INTEGER NOT NULL,
            src_symbol TEXT, relationship TEXT NOT NULL, target TEXT NOT NULL,
            PRIMARY KEY (file, line, relationship, target));
        INSERT INTO meta VALUES ('schema_version', '2');
        INSERT INTO symbols VALUES ('src/old.py', 'legacy', 'function', 1, 2, 'ast');
    """)
    conn.commit()
    conn.close()


def test_v2_to_v3_migration(tmp_path):
    db = str(tmp_path / "old.db")
    _make_v2_manifest(db)
    m = Manifest(db)
    try:
        cols = {r[1] for r in m._conn.execute("PRAGMA table_info(symbols)")}
        assert {"signature", "visibility"} <= cols, "columns not added"
        # version bumped exactly once (2 -> 3, not futher)
        assert m.get_meta("schema_version") == SCHEMA_VERSION == "3"
        # old rows survive with NULL signature/visibility
        rows = m.find_symbols("legacy")
        assert rows and rows[0].signature is None
        assert rows[0].visibility is None
        # reopening does not bump again or duplicate the ALTER
        m.close()
        m2 = Manifest(db)
        assert m2.get_meta("schema_version") == "3"
        rows2 = m2.find_symbols("legacy")
        assert rows2 and rows2[0].signature is None
    finally:
        m.close()


def test_fresh_manifest_is_v3(indexed):
    row = indexed.find_symbols("public_fn")
    assert row and row[0].signature is not None
    assert row[0].visibility == "public"


# ---------------------------------------------------------------------------
# Unit: signature extraction (§4)
# ---------------------------------------------------------------------------

def test_signature_python_basic_and_decorated():
    src = (
        "import functools\n"
        "\n"
        "@functools.cache\n"
        "class Manifest:\n"
        "    pass\n"
        "\n"
        "def plain_fn(a, b=1) -> int:\n"
        "    return a\n"
    )
    chunks = ts_chunker.chunk_text("x.py", src)
    syms = ts_chunker.extract_symbols("x.py", src, chunks)
    by_name = {s["name"]: s for s in syms}
    assert by_name["plain_fn"]["signature"] == "def plain_fn(a, b=1) -> int:"
    assert by_name["Manifest"]["signature"] == "class Manifest:"
    assert by_name["plain_fn"]["visibility"] == "public"
    assert by_name["Manifest"]["visibility"] == "public"


def test_signature_cpp_qualified_and_truncation():
    src = (
        "namespace ft {\n"
        "class Session {\n"
        "  void abort();\n"
        "};\n"
        "int very_long_function_name_" + "x" * 200 + "(int a) {\n"
        "  return a;\n"
        "}\n"
        "}\n"
    )
    chunks = ts_chunker.chunk_text("x.cpp", src)
    syms = ts_chunker.extract_symbols("x.cpp", src, chunks)
    long_syms = [s for s in syms if s["name"].startswith("very_long")]
    assert long_syms, syms
    assert len(long_syms[0]["signature"]) <= ts_chunker.SIG_CAP
    assert long_syms[0]["signature"].startswith("int very_long")


def test_signature_capped_at_120():
    src = "def f(" + ", ".join(f"arg{i}" for i in range(60)) + "):\n    pass\n"
    chunks = ts_chunker.chunk_text("x.py", src)
    syms = ts_chunker.extract_symbols("x.py", src, chunks)
    assert all(len(s["signature"]) <= ts_chunker.SIG_CAP for s in syms)


# ---------------------------------------------------------------------------
# Unit: visibility rules (§2.5/§3)
# ---------------------------------------------------------------------------

def test_visibility_python_underscore():
    from code_indexer.ts_chunker import _visibility_from_name as vis
    assert vis("_private", "function_definition", "def _private():", "function") == "private"
    assert vis("__mangled", "method_definition", "", "method") == "private"
    assert vis("public_fn", "function_definition", "", "function") == "public"


def test_visibility_rust_pub():
    from code_indexer.ts_chunker import _visibility_from_name as vis
    assert vis("open", "function_item", "pub fn open() {", "function") == "public"
    assert vis("closed", "function_item", "fn closed() {", "function") == "private"


def test_visibility_cpp_default_public():
    from code_indexer.ts_chunker import _visibility_from_name as vis
    assert vis("abort", "function_definition", "", "method") == "public"


def test_visibility_regex_fallback_python_rule_only():
    text = "int parse_config(int a) {\n  return a;\n}\n" + "x = 1;\n" * 60
    chunks = fallback_chunk(text)
    syms = ts_chunker.extract_symbols("weird.xyz", text, chunks)
    assert syms and syms[0]["source"] == "regex"
    assert syms[0]["signature"]  # first-line best-effort
    py_private = ts_chunker.extract_symbols(
        "w.py", "def _hidden_fn():\n    pass\n", fallback_chunk("def _hidden_fn():\n    pass\n"))
    assert py_private[0]["visibility"] == "private"


# ---------------------------------------------------------------------------
# Command-level: skeleton / outline (dense text + JSON contract)
# ---------------------------------------------------------------------------

@pytest.fixture()
def core(cfg, project):
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(project, name="subproj")
    core = Core(cfg)
    core.maybe_refresh = lambda slug, path: {"state": "fresh"}
    # Index into the slug-keyed manifest the core will read.
    m = core.manifest_for(entry.slug)
    try:
        Indexer(cfg, StubEmbedder(), StubStore()).index_project(
            project, entry.slug, m)
    finally:
        m.close()
    yield core, entry
    reg.close()


def test_skeleton_dense_text(core):
    c, entry = core
    data = c.skeleton(entry)
    text = c.format_skeleton(data, "text")
    assert "" not in text.split("\n"), "blank line in dense output"
    lines = text.split("\n")
    assert any(ln.startswith("src/session.py  (python, ") for ln in lines)
    sym_lines = [ln for ln in lines if ln.startswith("  ")]
    assert all(ln.split(":")[0].strip() for ln in sym_lines)
    # signature included (schema v3)
    assert any("def public_fn(a, b):" in ln for ln in lines)


def test_skeleton_json_contract(core):
    c, entry = core
    data = c.skeleton(entry)
    parsed = json.loads(c.format_skeleton(data, "json"))
    assert parsed["files"]
    f = next(f for f in parsed["files"] if f["file"] == "src/session.py")
    assert f["lang"] == "python"
    names = {s["name"] for s in f["symbols"]}
    assert "FileTransferSession" in names and "public_fn" in names
    for s in f["symbols"]:
        assert set(s) == {"name", "type", "start_line", "end_line", "signature"}
    fn = next(s for s in f["symbols"] if s["name"] == "public_fn")
    assert fn["signature"] == "def public_fn(a, b):"


def test_skeleton_prefix_and_limit_and_no_signatures(core):
    c, entry = core
    data = c.skeleton(entry, prefix="src", limit=1)
    f = next(g for g in data["files"] if g["file"] == "src/session.py")
    assert len(f["symbols"]) == 1, "--limit caps symbols per file"
    data2 = c.skeleton(entry, include_signatures=False)
    assert all(s["signature"] is None
               for g in data2["files"] for s in g["symbols"])


def test_skeleton_limit_header_reflects_cap(core):
    c, entry = core
    data = c.skeleton(entry, prefix="src", limit=1)
    text = c.format_skeleton(data, "text")
    header = next(ln for ln in text.split("\n")
                  if ln.startswith("src/session.py"))
    assert "showing 1" in header, header
    assert "symbols, showing" in header, header
    session = next(g for g in data["files"]
                   if g["file"] == "src/session.py")
    total = session["total_symbols"]
    assert total >= 2, "fixture file has several symbols"
    assert len(session["symbols"]) == 1


def test_skeleton_tree_projection(core):
    c, entry = core
    data = c.skeleton(entry, tree_mode=True, limit=1)
    assert "dirs" in data, "tree mode returns dir projection"
    assert "files" not in data, "no symbols listed in tree mode"
    dirs = {d["dir"]: d for d in data["dirs"]}
    assert "src" in dirs
    d = dirs["src"]
    assert d["dominant"] == "python"
    assert d["symbols"] > 0, "per-dir symbol count"
    assert "files" not in d and "symbols" not in dir(d)
    text = c.format_skeleton(data, "text")
    assert "" not in text.split("\n")
    assert "src/  (" in text and "python" in text
    assert "def public_fn" not in text, "no symbol lines in tree output"
    parsed = json.loads(c.format_skeleton(data, "json"))
    assert parsed["dirs"] == data["dirs"]


def test_skeleton_empty_file_and_no_symbols_still_listed(core):
    c, entry = core
    data = c.skeleton(entry)
    paths = {f["file"] for f in data["files"]}
    assert "src/empty.py" in paths, "zero-symbol file must still be listed"
    assert "src/empty0.py" in paths, "empty file must still be listed"


def test_skeleton_prefix_no_match_exit_ok(core):
    c, entry = core
    data = c.skeleton(entry, prefix="nonexistent")
    assert data["files"] == []  # empty output, not an error


def test_outline_text_and_json(core):
    c, entry = core
    data = c.outline(entry, "src/session.py")
    text = c.format_outline(data, "text")
    assert "" not in text.split("\n"), "blank line in dense outline"
    assert "class FileTransferSession:1-" in text
    assert "def public_fn(a, b):" in text
    parsed = json.loads(c.format_outline(data, "json"))
    assert parsed["file"] == "src/session.py"
    for d in parsed["declarations"]:
        assert set(d) >= {"name", "type", "start_line", "end_line", "signature"}


def test_outline_docstrings(core):
    c, entry = core
    data = c.outline(entry, "src/session.py", include_docstrings=True)
    cls = next(d for d in data["declarations"]
               if d["name"] == "FileTransferSession")
    assert cls.get("doc", "").strip() == "Session doc."


def test_outline_absolute_path_normalized(core):
    c, entry = core
    data = c.outline(entry, os.path.join(entry.path, "src", "session.py"))
    assert data["file"] == "src/session.py"


def test_outline_file_not_in_manifest_errors(core):
    c, entry = core
    with pytest.raises(ValueError, match="error:"):
        c.outline(entry, "src/nope.py")


def test_find_symbol_browse_mode_with_filters(core):
    c, entry = core
    m = c.manifest_for(entry.slug)
    try:
        rows = m.find_symbols(None, symbol_type="class")
        assert [r.name for r in rows] == ["FileTransferSession"]
        rows = m.find_symbols(None, file="src/session.py", limit=25)
        assert rows and all(r.file == "src/session.py" for r in rows)
        # nameless query is capped
        rows = m.find_symbols(None, limit=2)
        assert len(rows) <= 2
        # signature comes through the extended row
        fn = next(r for r in m.find_symbols(None) if r.name == "public_fn")
        assert fn.signature == "def public_fn(a, b):"
    finally:
        m.close()


def test_skip_stale_check_short_circuit(cfg, project):
    core = Core(cfg, skip_stale_check=True)
    calls = []
    core.maybe_refresh = lambda slug, path: calls.append(1) or {"state": "fresh"}
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(project, name="sc")
    core.skeleton(entry)
    assert not calls, "--skip-stale-check must skip the refresh probe"
    reg.close()
    shutil.rmtree(cfg.index_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Concurrency guard: skeleton under a concurrent index pass reads
# WAL-consistently — no torn rows, no blocking (§4).
# ---------------------------------------------------------------------------

def test_skeleton_wal_concurrent_index(cfg, project):
    m = Manifest(os.path.join(cfg.index_root, "m.db"))
    Indexer(cfg, StubEmbedder(), StubStore()).index_project(project, "sub", m)
    m.close()

    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(project, name="wal")
    c = Core(cfg)
    c.maybe_refresh = lambda slug, path: {"state": "fresh"}

    writer = Indexer(cfg, StubEmbedder(), StubStore())
    stop = threading.Event()
    errors = []

    def rewrite():
        # Simulate a concurrent index pass rewriting symbol rows (atomic
        # per file). Readers must never see torn/partial file row sets.
        mm = Manifest(os.path.join(cfg.index_root, "m.db"))
        while not stop.is_set():
            writer.index_project(project, "sub", mm, force_full=True)
        mm.close()

    t = threading.Thread(target=rewrite, daemon=True)
    t.start()
    try:
        for _ in range(5):
            data = c.skeleton(entry)
            for f in data["files"]:
                for s in f["symbols"]:
                    assert s["start_line"] <= s["end_line"], "torn row"
    finally:
        stop.set()
        t.join(timeout=30)
        reg.close()
        shutil.rmtree(cfg.index_root, ignore_errors=True)
    assert not errors


# ---------------------------------------------------------------------------
# Edge: pre-v3 manifest renders hint, no crash (§4)
# ---------------------------------------------------------------------------

def test_pre_v3_manifest_hint_on_skeleton(tmp_path, project):
    db = str(tmp_path / "m.db")
    _make_v2_manifest(db)
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=tempfile.mkdtemp(prefix="ci-prev3-"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    os.makedirs(cfg.index_root, exist_ok=True)
    shutil.copy(db, os.path.join(cfg.index_root, "m.db"))
    m = Manifest(os.path.join(cfg.index_root, "m.db"))
    assert m._migrated_from == "2"
    m.close()
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(project, name="prev3")
    c = Core(cfg)
    c.maybe_refresh = lambda slug, path: {"state": "fresh"}
    data = c.skeleton(entry)
    text = c.format_skeleton(data, "text") + "\n" + MIGRATION_HINT
    assert "reindex_project" in text  # hint present, no crash
    reg.close()
    shutil.rmtree(cfg.index_root, ignore_errors=True)
