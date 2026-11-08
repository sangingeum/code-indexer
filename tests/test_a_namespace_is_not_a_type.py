"""A qualified C++ name records the TYPE it scopes, never the namespace.

`geo::Point p;` spells `geo` in a qualifier position, and the C++ query
captures it -- as an INTERIM `type_use` row, which the index pass converts to
`type` only when the name it resolves to really is a type. A project-defined
namespace resolves to a symbol of its own, so a resolver that converted on
"any definition binds" turned the namespace into a type row. The guard lives
in the resolver, so the captures stay as they are and a real type reached
through a qualified name keeps its row.

Offline: real chunker + queries, stub embedder and store, no network.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

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

}
"""

GEO_USER = """\
#include "geo.h"

void use() {
    geo::Point p;
    p.x = 1;
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
                                       "use.cpp": GEO_USER}, name="qualified")
    yield core, entry
    reg.close()


def test_a_namespace_in_a_qualifier_is_not_a_type_reference(qualified_project):
    """`geo` is a namespace the project defines, so it resolves -- and the
    resolver converted every resolved name to `type`, which made a namespace
    read as a type."""
    core, entry = qualified_project
    m = core.read_manifest(entry.slug)
    try:
        rows = m._conn.execute(
            "SELECT file, line, relationship FROM refs WHERE to_name = 'geo'"
        ).fetchall()
    finally:
        m.close()
    assert rows == [], rows


def test_a_type_reached_through_a_qualified_name_keeps_its_row(
        qualified_project):
    """The guard rejects the namespace, not the qualifier: `geo::Point` still
    records `Point` as a type, attributed to its definition."""
    core, entry = qualified_project
    m = core.read_manifest(entry.slug)
    try:
        rows = m._conn.execute(
            "SELECT relationship, resolution, target_symbol_id FROM refs"
            " WHERE to_name = 'Point'").fetchall()
    finally:
        m.close()
    assert rows, "a type reached through a qualifier lost its row"
    assert {r[0] for r in rows} == {"type"}, rows
    assert all(r[2] is not None for r in rows), rows
