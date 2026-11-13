"""A project root this process cannot enter is a tree the check could not see.

``os.walk`` reports NOTHING for a root it cannot enter instead of raising, so
the shared tree diff read "nothing" as "every file is gone": the banner said
``stale [deleted alpha.py, deleted beta.py]`` and the repair it names could not
clear it -- the pass asked the same broken question and kept the same wrong
answer. An unenterable root is a condition the check could not look at, and
the honest verdict is ``unknown``, which is never ``fresh`` and never a reason
to purge rows.

This test drives the real scanner, the real manifest and the real staleness
check against a fixture tree; only the embedding service and the vector store
are stubs, at the same Core seam the rest of the suite uses.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer import staleness  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402


class StubEmbedder:
    """A fixed-width embedder: no test here dials a service."""

    def __init__(self, dim: int = 4):
        self.dim = dim

    def dimension(self) -> int:
        return self.dim

    def embed(self, texts):
        return [[0.1] * self.dim for _ in texts]

    def embed_with_errors(self, texts):
        return [[0.1] * self.dim for _ in texts], []


class StubStore:
    """Enough of the vector store for a pass and a swap."""

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

    def physical_name(self, name: str) -> str | None:
        if name in self.aliases:
            return self.aliases[name]
        return name if name in self.collections else None

    def _alias_exists(self, alias_name: str) -> bool:
        return alias_name in self.aliases

    def upsert_points(self, name: str, points: list) -> int:
        return len(points)

    def purge_file_points(self, *a, **kw) -> int:
        return 0

    def count_points(self, name: str) -> int:
        return 0


def _config(index_root: str) -> Config:
    return Config(ollama_url="", qdrant_url="", embed_model="stub-model",
                  index_root=index_root, embed_batch=48, upsert_batch=256,
                  max_file_bytes=1048576, watch_debounce=3,
                  watch_sweep_interval=300)


@pytest.fixture()
def rig(tmp_path):
    """One project with two indexed files, then a root nobody can enter."""
    index_root = tmp_path / "index"
    index_root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "alpha.py").write_text("def alpha():\n    return 1\n",
                                      encoding="utf-8")
    (project / "beta.py").write_text("def beta():\n    return 2\n",
                                     encoding="utf-8")
    reg = Registry(str(index_root / "registry.db"))
    entry = reg.add(str(project), name="lockedroot")
    core = Core(_config(str(index_root)))
    stub = StubEmbedder()
    core.embedder = stub  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = stub  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    core.registry = reg
    assert core.run_index(entry.slug, str(project), vectors="on")["state"] == "idle"
    assert core.staleness(entry).state == staleness.FRESH
    os.chmod(project, 0o000)
    try:
        with pytest.raises(PermissionError):
            os.listdir(project)
        yield core, entry, project
    finally:
        os.chmod(project, 0o755)
        reg.close()


def _rows(core: Core, entry) -> set[str]:
    manifest = core.read_manifest(entry.slug)
    try:
        return set(manifest.all_files())
    finally:
        manifest.close()


def test_a_root_this_process_cannot_enter_reads_unknown_not_stale(rig):
    """The defect: the diff called every row deleted for a tree it never saw."""
    core, entry, project = rig

    verdict = core.staleness(entry)

    assert verdict.state == staleness.UNKNOWN, verdict
    assert verdict.reasons == (), verdict.reasons
    assert verdict.reason_count == 0
    assert verdict.detail and "unreadable" in verdict.detail, verdict.detail
    # The detail names the thing that could not be read, not just a mood.
    assert project.name in verdict.detail


def test_the_banner_for_an_unenterable_root_prescribes_no_unclearable_repair(rig):
    core, entry, project = rig

    line = staleness.warning_line(core.staleness(entry), str(project))

    assert line is not None
    assert "did not run" in line, line
    assert "index is stale" not in line, line
    assert "reindex-project" not in line, line


def test_reindex_keeps_every_row_when_the_root_cannot_be_entered(rig):
    """The repair the banner names must not purge what it could not read."""
    core, entry, project = rig
    before = _rows(core, entry)
    assert before == {"alpha.py", "beta.py"}

    result = core.reindex_with_swap(entry.slug, str(project))

    assert result["state"] == "idle", result
    assert result["result"]["files_deleted"] == 0, result
    assert _rows(core, entry) == before
    assert core.staleness(entry).state != staleness.STALE


def test_a_vectors_off_reindex_also_deletes_nothing_it_could_not_read(rig):
    """The other arm is a real parse, and it cannot parse what it cannot see.

    Whatever it reports, the rows are the pass's memory of a tree it could not
    reach: nothing is proven absent, so nothing is deleted.
    """
    core, entry, project = rig
    before = _rows(core, entry)

    core.reindex_with_swap(entry.slug, str(project), vectors="off")

    assert _rows(core, entry) == before


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
