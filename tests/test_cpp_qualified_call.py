"""A C++ call through a qualified name writes a caller edge.

`geo::place()` parses as a call_expression whose FUNCTION is a
qualified_identifier, so the plain `identifier` call pattern never saw it and
the call half of a namespace-qualified call was invisible: the project could
call `geo::place()` from anywhere and `find-callers place` answered nothing.
The query now captures the qualified callee; the scope half stays a type use,
which the resolver keeps only when it binds a type -- so the fix must not turn
a qualified TYPE use into a call.

Offline: real chunker + queries, stub embedder and store, no network.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer import graph  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

GEO_HEADER = """\
#pragma once

namespace geo {

struct Point {
    int x;
};

void place();

template <typename T>
T make();

}
"""

GEO_USER = """\
#include "geo.h"

void use() {
    geo::place();
    geo::Point p;
    p.x = 1;
}
"""

# A DEFINITION, not only the header's declaration: a declaration writes no
# symbol row, so the call could never resolve against the header alone.
GEO_DEFINITIONS = """\
#include "geo.h"

namespace geo {

void place() {
}

}
"""


def _rig(tmp_path, files: dict[str, str], name: str):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        (project / rel).write_text(text, encoding="utf-8")
    core = Core(cfg, skip_stale_check=True)

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

        def swap_collection(self, tmp, target):
            pass

        def count_points(self, name):
            return 0

        def search(self, *a, **kw):
            return []

    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name=name)
    core.registry = reg
    core.run_index(entry.slug, str(project))
    return core, entry, reg


@pytest.fixture()
def qualified_project(tmp_path):
    core, entry, reg = _rig(tmp_path, {"geo.h": GEO_HEADER,
                                       "geo.cpp": GEO_DEFINITIONS,
                                       "use.cpp": GEO_USER}, name="qualified")
    yield core, entry
    reg.close()


def test_a_call_through_a_qualified_name_records_a_call_row_naming_the_callee(
        qualified_project):
    """`geo::place()` produced no `call` row at all: the callee sat one node
    below the call's function field, inside a qualified_identifier."""
    core, entry = qualified_project
    m = core.read_manifest(entry.slug)
    try:
        rows = m._conn.execute(
            "SELECT file, line, relationship, target_symbol_id FROM refs"
            " WHERE to_name = 'place' AND relationship = 'call'").fetchall()
    finally:
        m.close()
    assert rows, "a call through a namespace still has no caller edge"
    assert {r[0] for r in rows} == {"use.cpp"}, rows
    assert {r[1] for r in rows} == {4}, rows
    # The caller EDGE, not just the row: the def in geo.cpp is what makes the
    # call resolvable, and the header's declaration alone never did.
    assert all(r[3] is not None for r in rows), rows


def test_a_qualified_type_use_stays_a_type_row_and_never_becomes_a_call(
        qualified_project):
    """The new pattern is anchored on a call's function field, so
    `geo::Point p;` -- a qualified TYPE use -- keeps its `type` row and adds
    no call row for either half of the name."""
    core, entry = qualified_project
    m = core.read_manifest(entry.slug)
    try:
        point = m._conn.execute(
            "SELECT relationship FROM refs WHERE to_name = 'Point'").fetchall()
        calls = m._conn.execute(
            "SELECT to_name FROM refs WHERE relationship = 'call'"
            " AND to_name IN ('Point', 'geo')").fetchall()
    finally:
        m.close()
    assert {r[0] for r in point} == {"type"}, point
    assert calls == [], calls


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n.children)


def test_find_callers_returns_the_call_site_of_a_qualified_call(
        qualified_project):
    """The resolved edge is what `find-callers place` walks: the caller is
    the `use.cpp` line the fixture writes, not an unresolved row."""
    core, entry = qualified_project
    result = graph.find_callers(core, entry.slug, "place")
    sites = [(n.file, n.line) for n in _walk(result.nodes)
             if n.relationship == "call"]
    assert ("use.cpp", 4) in sites, [n.label() for n in _walk(result.nodes)]
