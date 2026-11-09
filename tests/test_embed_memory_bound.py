"""The embed pass holds one bounded group of vectors, not the project's.

A pass used to build every text for the whole project, embed them in ONE call
and only then start writing, so the vector set alive grew with the tree and a
large reindex was OOM-killed. These tests measure the two properties that
replace it: the largest single embed request, and the peak number of vector
objects alive at once (weakref finalizers on a list subclass; CPython frees a
list of floats by refcount the moment its last reference drops, so the count
is exact).

``cfg.embed_group`` is set after construction on purpose: the attribute is the
property under test, and a construction-time keyword would make this file
error out at the base commit instead of failing on the property.

Two of these five tests are green at the base commit 78fcd64, and both are
green for a true reason rather than by accident. The base pass already built
every request's texts with ``embed_text(file, chunk)`` over the same stored
chunk rows, so `test_the_grouped_pass_embeds_the_same_construction_as_the_rows`
passed before the rewrite -- what changed is the batching boundary, not the
construction. And the base pass already isolated a per-chunk embed failure:
`test_a_poisoned_chunk_is_recorded_and_skipped_inside_its_group` passed because
the error was recorded in ``index_errors``, the poisoned chunk was skipped, and
a file's row was committed only for chunks that reached the store, so a file
emptied by failures was never claimed. The three failing tests are the new
properties: one bounded request, one bounded group alive, and a file larger
than a group travelling whole.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import weakref

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.embed_text import embed_text  # noqa: E402
from code_indexer.indexer import _chunk_from_row  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402


class Vector(list):
    """A list of floats that can be weak-referenced (builtin list cannot)."""


class RecordingEmbedder:
    def __init__(self, dim: int = 4, poison: str | None = None) -> None:
        self.dim = dim
        self.poison = poison
        self.requests: list[int] = []
        self.texts: list[str] = []
        self._live = 0
        self.peak_live = 0

    def dimension(self) -> int:
        return self.dim

    def _drop(self) -> None:
        self._live -= 1

    def embed_with_errors(self, texts):
        self.requests.append(len(texts))
        self.texts.extend(texts)
        vectors: list[Vector | None] = []
        errors: list[tuple[int, str]] = []
        for idx, text in enumerate(texts):
            if self.poison is not None and self.poison in text:
                errors.append((idx, "embed failed: poisoned"))
                vectors.append(None)
                continue
            vec = Vector([0.1] * self.dim)
            self._live += 1
            if self._live > self.peak_live:
                self.peak_live = self._live
            weakref.finalize(vec, self._drop)
            vectors.append(vec)
        return vectors, errors

    def embed(self, texts):
        return self.embed_with_errors(texts)[0]


class StubStore:
    def __init__(self) -> None:
        self.collections: set[str] = set()
        self.aliases: dict[str, str] = {}
        self.upserts: list[tuple[str, int]] = []

    def collection_exists(self, name: str) -> bool:
        return name in self.collections or name in self.aliases

    def create_collection(self, name: str, dim: int) -> None:
        self.collections.add(name)

    def physical_name(self, name: str) -> str | None:
        if name in self.aliases:
            return self.aliases[name]
        return name if name in self.collections else None

    def swap_collection(self, tmp: str, target: str) -> None:
        old = self.physical_name(target)
        self.aliases[target] = tmp
        if old is not None and old != tmp and old != target:
            self.collections.discard(old)

    def upsert_points(self, name: str, points) -> int:
        self.upserts.append((name, len(points)))
        return len(points)

    def purge_file_points(self, *args, **kwargs) -> int:
        return 0

    def count_points(self, name: str) -> int:
        return sum(n for _, n in self.upserts)

    def search(self, *args, **kwargs):
        return []


def _write_functions(path: str, count: int, prefix: str = "handler") -> None:
    parts = []
    for j in range(count):
        parts.append(
            f"def {prefix}_{j}(value):\n"
            f"    scaled = value * {j + 1}\n"
            f"    return scaled\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))


def _rig(files: int, functions: int, group: int, poison: str | None = None):
    tmp = tempfile.mkdtemp(prefix="ci-bound-")
    project = tempfile.mkdtemp(prefix="ci-bound-proj-")
    for i in range(files):
        _write_functions(os.path.join(project, f"module_{i}.py"), functions,
                         prefix=f"handler_{i}")
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub-model",
                 index_root=tmp, embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=3,
                 watch_sweep_interval=300)
    cfg.embed_group = group
    store = StubStore()
    core = Core(cfg)
    embedder = RecordingEmbedder(poison=poison)
    core.embedder = embedder  # type: ignore[assignment]
    core.store = store  # type: ignore[assignment]
    core.indexer.embedder = embedder  # type: ignore[assignment]
    core.indexer.store = store  # type: ignore[assignment]
    reg = Registry(os.path.join(tmp, "registry.db"))
    entry = reg.add(project, name="boundproj")
    return core, reg, entry, project, embedder, store


@pytest.fixture()
def small_rig():
    core, reg, entry, project, embedder, store = _rig(
        files=10, functions=10, group=40)
    yield core, reg, entry, project, embedder, store
    reg.close()
    shutil.rmtree(core.cfg.index_root, ignore_errors=True)
    shutil.rmtree(project, ignore_errors=True)


def test_the_embed_request_never_receives_the_whole_project(small_rig):
    """One hundred chunks, a forty-chunk group: no request may carry more."""
    core, reg, entry, project, embedder, store = small_rig
    result = core.run_index(entry.slug, project)
    assert result["state"] == "idle"
    assert result["result"]["chunks_embedded"] == 100
    assert len(embedder.requests) > 1, "the pass must embed in more than one call"
    assert max(embedder.requests) <= 40, embedder.requests


def test_only_one_group_of_vectors_is_alive_at_a_time(small_rig):
    """The peak live vector count is the group, not the project."""
    core, reg, entry, project, embedder, store = small_rig
    result = core.run_index(entry.slug, project)
    assert result["state"] == "idle"
    assert embedder.peak_live <= 40, embedder.peak_live


def test_the_grouped_pass_embeds_the_same_construction_as_the_rows(
        small_rig):
    """Every text handed to the embedder is ``embed_text(file, chunk)`` of a
    stored chunk row -- the construction the vectors were built from, byte for
    byte."""
    core, reg, entry, project, embedder, store = small_rig
    assert core.run_index(entry.slug, project)["state"] == "idle"
    manifest = core.manifest_for(entry.slug)
    try:
        expected = sorted(embed_text(row.file, _chunk_from_row(row))
                          for row in manifest.all_chunks())
    finally:
        manifest.close()
    assert sorted(embedder.texts) == expected


def test_a_single_file_larger_than_a_group_still_indexes_whole():
    """A file bigger than the group travels alone: the bound is that file's
    size, the smallest group that keeps the resumability unit whole."""
    tmp = tempfile.mkdtemp(prefix="ci-bound-big-")
    project = tempfile.mkdtemp(prefix="ci-bound-big-proj-")
    try:
        _write_functions(os.path.join(project, "big.py"), 60, prefix="big")
        _write_functions(os.path.join(project, "small.py"), 10, prefix="small")
        cfg = Config(ollama_url="", qdrant_url="", embed_model="stub-model",
                     index_root=tmp, embed_batch=48, upsert_batch=256,
                     max_file_bytes=1048576, watch_debounce=3,
                     watch_sweep_interval=300)
        cfg.embed_group = 40
        core = Core(cfg)
        embedder = RecordingEmbedder()
        store = StubStore()
        core.embedder = embedder  # type: ignore[assignment]
        core.store = store  # type: ignore[assignment]
        core.indexer.embedder = embedder  # type: ignore[assignment]
        core.indexer.store = store  # type: ignore[assignment]
        reg = Registry(os.path.join(tmp, "registry.db"))
        entry = reg.add(project, name="bigproj")
        try:
            result = core.run_index(entry.slug, project)
            assert result["state"] == "idle"
            assert result["result"]["chunks_embedded"] == 70
            assert max(embedder.requests) == 60
        finally:
            reg.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def test_a_poisoned_chunk_is_recorded_and_skipped_inside_its_group():
    """The poisoned-chunk rule survives grouping: the failure is recorded, the
    chunk is skipped, and the file it empties is not claimed by the manifest."""
    core, reg, entry, project, embedder, store = _rig(
        files=6, functions=10, group=40, poison="module_2.py")
    try:
        result = core.run_index(entry.slug, project)
        assert result["state"] == "idle"
        assert result["result"]["chunks_skipped"] == 10
        assert result["result"]["chunks_embedded"] == 50
        manifest = core.manifest_for(entry.slug)
        try:
            rows = manifest.all_files()
            assert "module_2.py" not in rows
            assert len(rows) == 5
            assert manifest.count_index_errors() == 10
            assert all(e["file"] == "module_2.py"
                       for e in manifest.last_index_errors(10))
        finally:
            manifest.close()
    finally:
        reg.close()
        shutil.rmtree(core.cfg.index_root, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)
