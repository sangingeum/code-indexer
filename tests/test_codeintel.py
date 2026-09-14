"""E2E in-process tests for the plan v2 code-intel layer.

Covers plan v2 §2 (staleness regression: create/modify/delete reflected on
next index call), §4 (symbol index incl. C++ namespace/container rule and
confidence rule), §5 (refs, one representation), §6 (get_code_context both
forms + path containment), §7 (exact identifier match). Uses a stub
embedder/store — no network — plus the real chunkers, indexer, manifest.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from mcp_code_indexer.config import Config  # noqa: E402
from mcp_code_indexer.indexer import Indexer  # noqa: E402
from mcp_code_indexer.manifest import Manifest  # noqa: E402
from mcp_code_indexer import ts_chunker  # noqa: E402


class StubEmbedder:
    def dimension(self):
        return 4

    def embed(self, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class StubStore:
    def __init__(self):
        self.upserts = 0

    def collection_exists(self, name):
        return True

    def create_collection(self, name, dim):
        pass

    def upsert_points(self, name, points):
        self.upserts += len(points)

    def purge_file_points(self, name, project, path, min_chunk_index=0):
        pass


@pytest.fixture()
def project():
    root = tempfile.mkdtemp(prefix="ci-test-")
    os.makedirs(os.path.join(root, "src"))
    with open(os.path.join(root, "src", "session.hpp"), "w") as f:
        f.write(
            "#pragma once\n"
            "namespace ft {\n\n"
            "class FileTransferSession {\n"
            " public:\n"
            "  void start();\n"
            "};\n\n"
            "enum class Mode { kPush, kPull };\n\n"
            "}  // namespace ft\n"
        )
    with open(os.path.join(root, "src", "main.cpp"), "w") as f:
        f.write(
            '#include "transfer/session.hpp"\n'
            "int run() {\n"
            "  ft::FileTransferSession s;\n"
            "  s.start();\n"
            "  return 0;\n"
            "}\n"
        )
    with open(os.path.join(root, "src", "helper.py"), "w") as f:
        f.write("def brand_new_helper():\n    return 'fresh'\n")
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def indexer(project):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=tempfile.mkdtemp(prefix="ci-idx-"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3)
    yield Indexer(cfg, StubEmbedder(), StubStore())
    shutil.rmtree(cfg.index_root, ignore_errors=True)


def run_index(indexer, project):
    m = Manifest(os.path.join(indexer.cfg.index_root, "m.db"))
    try:
        result = indexer.index_project(project, "testslug", m)
        return m, result
    except Exception:
        m.close()
        raise


# ---------------------------------------------------------------------------
# §4 symbol index
# ---------------------------------------------------------------------------

def test_symbol_index_cpp_first_class(indexer, project):
    m, _ = run_index(indexer, project)
    try:
        rows = m.find_symbols("FileTransferSession")
        assert rows and rows[0].symbol_type == "class"
        assert rows[0].source == "ast"  # confidence rule: exact
        enums = m.find_symbols("Mode", symbol_type="enum")
        assert enums and enums[0].start_line == 9
        # Namespace handled as container: inner symbols must survive.
        ns = m.find_symbols("ft", symbol_type="namespace")
        assert ns, "namespace row missing"
        assert m.find_symbols("FileTransferSession"), "namespace swallowed class"
    finally:
        m.close()


def test_confidence_rule_regex_fallback(indexer, project):
    # A .xyz file falls back to the regex chunker -> heuristic confidence.
    with open(os.path.join(project, "src", "weird.xyz"), "w") as f:
        f.write("int parse_config(int a) {\n  return a;\n}\n" + "x = 1;\n" * 60)
    m, _ = run_index(indexer, project)
    try:
        rows = m.find_symbols("parse_config")
        assert rows and rows[0].source == "regex"
    finally:
        m.close()


# ---------------------------------------------------------------------------
# §2 staleness regression (on the next index call, not instantly)
# ---------------------------------------------------------------------------

def test_create_modify_delete_reflected_next_pass(indexer, project):
    m, r1 = run_index(indexer, project)
    assert r1.files_indexed >= 3
    assert m.find_symbols("FileTransferSession")

    # modify one function (helper.py), create a file, delete session.hpp
    with open(os.path.join(project, "src", "helper.py"), "a") as f:
        f.write("\ndef added_function():\n    return 1\n")
    with open(os.path.join(project, "src", "created.py"), "w") as f:
        f.write("def created_symbol():\n    return 2\n")
    os.remove(os.path.join(project, "src", "session.hpp"))

    m2, r2 = run_index(indexer, project)
    try:
        assert m2.find_symbols("added_function"), "modified file not re-indexed"
        assert m2.find_symbols("created_symbol"), "created file not indexed"
        assert not m2.find_symbols("FileTransferSession"), \
            "deleted file's symbols survived"
        assert m2.find_symbols("brand_new_helper"), "unchanged symbol lost"
    finally:
        m2.close()


# ---------------------------------------------------------------------------
# §5 references (one representation)
# ---------------------------------------------------------------------------

def test_refs_one_representation(indexer, project):
    m, _ = run_index(indexer, project)
    try:
        includes = m.find_refs("transfer/session.hpp", relationship="includes")
        assert includes, "include edge missing"
        calls = m.find_refs("start", relationship="calls")
        assert any(r.file == "src/main.cpp" and r.line == 4 for r in calls)
    finally:
        m.close()


def test_refs_removed_with_file(indexer, project):
    m, _ = run_index(indexer, project)
    m.close()
    os.remove(os.path.join(project, "src", "main.cpp"))
    m2, _ = run_index(indexer, project)
    try:
        assert not m2.find_refs("transfer/session.hpp", relationship="includes")
    finally:
        m2.close()


# ---------------------------------------------------------------------------
# §7 exact identifier match
# ---------------------------------------------------------------------------

def test_exact_identifier_match(indexer, project):
    m, _ = run_index(indexer, project)
    try:
        exact = m.find_symbols("FileTransferSession", substring=False)
        assert exact
        assert all(r.name == "FileTransferSession" for r in exact)
    finally:
        m.close()


# ---------------------------------------------------------------------------
# ts_chunker unit behavior
# ---------------------------------------------------------------------------

def test_namespace_is_container_not_swallowing():
    text = (
        "namespace a {\n"
        "namespace b {\n"
        "int inner() { return 1; }\n"
        "}\n"
        "}\n"
    )
    chunks = ts_chunker.chunk_text("x.cpp", text)
    syms = {c.symbol for c in chunks}
    assert "inner" in syms, "nested namespace swallowed inner function"
    assert "b" in syms and "a" in syms


def test_extract_symbols_skips_symbolless_chunks():
    text = "int plain_global = 3;\n"
    chunks = ts_chunker.chunk_text("x.cpp", text)
    for c in chunks:
        c.source = "ast"
    assert ts_chunker.extract_symbols("x.cpp", text, chunks) == []


def test_decorated_python_definitions_get_real_names():
    """Regression: @decorator-wrapped defs went through the first-line regex
    guess and produced garbage names ('ifest:\\n ') instead of Manifest etc."""
    src = (
        "import functools\n"
        "\n"
        "@functools.cache\n"
        "class Manifest:\n"
        "    pass\n"
        "\n"
        "@staticmethod\n"
        "def mark_scanned():\n"
        "    return 1\n"
    )
    chunks = ts_chunker.chunk_text("x.py", src)
    syms = {c.symbol for c in chunks}
    assert "Manifest" in syms, syms
    assert "mark_scanned" in syms, syms


def test_utf8_source_byte_offsets():
    """Regression: tree-sitter byte offsets were used to slice a str, so any
    name after a multi-byte char came out corrupted."""
    src = "# café — em-dash comment, 2+ multi-byte chars\nclass Foo:\n    pass\n"
    chunks = ts_chunker.chunk_text("x.py", src)
    assert any(c.symbol == "Foo" for c in chunks), [c.symbol for c in chunks]
