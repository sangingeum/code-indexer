"""A rows-current project whose TREE moved on is still reparsed by reindex-project.

The staleness banner names ``reindex-project`` as the repair. Its vectors-on
arm took a shortcut whenever ``vectors_policy.rows_current`` held, and that is
a BOOKKEEPING fact -- a pass completed and its extraction version is this
build's. It says nothing about the tree. So a project with current rows
embedded the stored chunks, walked nothing, and returned ``files_indexed: 0``
while the tree had gained and changed files: the warning the command exists to
clear could never be cleared.

The tests drive the real scanner, the real manifest and the real staleness
check against a fixture tree. Only the embedding service and the vector store
are stubs, at the same Core seam the rest of the suite uses.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer import indexer as indexer_mod  # noqa: E402
from code_indexer import staleness  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402


class StubEmbedder:
    """A fixed-width embedder: the pass never dials a service."""

    def __init__(self, dim: int = 4):
        self.dim = dim

    def dimension(self) -> int:
        return self.dim

    def embed(self, texts):
        return [[0.1] * self.dim for _ in texts]


class StubStore:
    """Enough of the vector store for a pass: no points survive in it.

    ``points_for_file`` is deliberately absent, so every chunk is embedded and
    nothing is carried: this file is about what the pass READS, not about
    vector reuse accounting.
    """

    def __init__(self) -> None:
        self.collections: set[str] = set()
        self.aliases: dict[str, str] = {}

    def collection_exists(self, name: str) -> bool:
        return name in self.collections or name in self.aliases

    def create_collection(self, name: str, dim: int) -> None:
        self.collections.add(name)

    def drop_collection(self, name: str) -> None:
        self.collections.discard(name)
        self.aliases.pop(name, None)

    def swap_collection(self, tmp: str, target: str) -> None:
        self.collections.add(tmp)
        self.aliases[target] = tmp

    def upsert_points(self, name: str, points: list) -> int:
        return len(points)

    def purge_file_points(self, *a, **kw) -> int:
        return 0

    def count_points(self, name: str) -> int:
        return 0

    def search(self, name: str, vector: list[float], limit: int = 8,
               file_filter=None, symbol_type=None, language=None):
        return []


def _config(index_root: str) -> Config:
    return Config(ollama_url="", qdrant_url="", embed_model="stub-model",
                  index_root=index_root, embed_batch=48, upsert_batch=256,
                  max_file_bytes=1048576, watch_debounce=3,
                  watch_sweep_interval=300)


def _wire(cfg: Config, registry: Registry) -> Core:
    core = Core(cfg)
    stub = StubEmbedder()
    core.embedder = stub  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = stub  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    core.registry = registry
    return core


@pytest.fixture()
def rig(tmp_path):
    """One project, one completed vectors-on pass, rows current."""
    index_root = tmp_path / "index"
    index_root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "alpha.py").write_text("def alpha():\n    return 1\n",
                                      encoding="utf-8")
    reg = Registry(str(index_root / "registry.db"))
    entry = reg.add(str(project), name="drifty")
    core = _wire(_config(str(index_root)), reg)
    assert core.run_index(entry.slug, str(project), vectors="on")["state"] == "idle"
    assert core.staleness(entry).state == staleness.FRESH
    try:
        yield core, entry, project
    finally:
        reg.close()


def test_reindex_project_parses_a_tree_that_moved_on_from_current_rows(rig):
    """The repro: added and modified files, current rows, one reindex."""
    core, entry, project = rig
    (project / "beta.py").write_text("def beta():\n    return 2\n",
                                     encoding="utf-8")
    (project / "alpha.py").write_text("def alpha():\n    return 3\n",
                                     encoding="utf-8")
    before = core.staleness(entry)
    assert before.stale
    assert {reason.kind for reason in before.reasons} == {"added", "modified"}

    result = core.reindex_with_swap(entry.slug, str(project))

    assert result["state"] == "idle", result
    assert result["result"]["files_indexed"] >= 2, result
    manifest = core.read_manifest(entry.slug)
    try:
        assert "beta.py" in manifest.all_files()
    finally:
        manifest.close()
    assert core.staleness(entry).state == staleness.FRESH


def test_a_matching_tree_still_rebuilds_vectors_without_parsing_source(rig,
                                                                       monkeypatch):
    """The cheap path stays cheap when the tree genuinely matches the rows.

    A vector-identity rebuild exists so it does not reparse a whole project;
    the parser is made to explode here so only a real parse can fail the test.
    """
    core, entry, project = rig

    def _explode(*args, **kwargs):
        raise AssertionError("the vector rebuild parsed source it did not need")

    monkeypatch.setattr(indexer_mod, "_parse_dispatch", _explode)

    result = core.reindex_with_swap(entry.slug, str(project))

    assert result["state"] == "idle", result
    assert result["result"]["files_indexed"] == 0, result
    assert core.staleness(entry).state == staleness.FRESH


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
