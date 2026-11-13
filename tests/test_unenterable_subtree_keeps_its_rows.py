"""A subtree this process cannot enter is not a subtree that emptied.

``os.walk`` reports NOTHING for a directory it cannot open and does not raise,
so the walk before this fix read a whole directory's rows as deleted: the
diff said ``stale [deleted sub/b.py]``, the pass purged the row and its points,
wrote no ``index_errors`` row, and the next ``index-status`` read ``fresh`` --
``fresh`` by forgetting, applied to a directory instead of a file. The root
probe added earlier covers the root only, and a subtree failure has no natural
depth (a stale mount, a changed group membership, a ``chmod`` on a vendored
tree).

The safety property pinned here: a row whose path lies under a directory this
process could not enter is NEVER a ``deleted`` reason and is NEVER deleted by a
pass, on either arm. The verdict shape: ``unknown`` when the gap leaves no
proven change, ``stale`` with the proven reasons plus the gap beside them when
something else provably moved.
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
from code_indexer.scanner import (ScanAccounting, read_scan_accounting,
                                 scan_project)  # noqa: E402


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
    """Two indexed files, one of them under a directory nobody can enter."""
    index_root = tmp_path / "index"
    index_root.mkdir()
    project = tmp_path / "project"
    (project / "sub").mkdir(parents=True)
    (project / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (project / "sub" / "b.py").write_text("def b():\n    return 2\n",
                                           encoding="utf-8")
    reg = Registry(str(index_root / "registry.db"))
    entry = reg.add(str(project), name="lockedsub")
    core = Core(_config(str(index_root)))
    stub = StubEmbedder()
    core.embedder = stub  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = stub  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    core.registry = reg
    assert core.run_index(entry.slug, str(project), vectors="on")["state"] == "idle"
    assert core.staleness(entry).state == staleness.FRESH
    sub = project / "sub"
    os.chmod(sub, 0o000)
    try:
        with pytest.raises(PermissionError):
            os.listdir(sub)
        yield core, entry, project
    finally:
        os.chmod(sub, 0o755)
        reg.close()


def _rows(core: Core, entry) -> set[str]:
    manifest = core.read_manifest(entry.slug)
    try:
        return set(manifest.all_files())
    finally:
        manifest.close()


def _error_rows(core: Core, entry) -> int:
    manifest = core.read_manifest(entry.slug)
    try:
        return manifest.count_index_errors()
    finally:
        manifest.close()


def test_the_walk_records_the_directory_it_could_not_enter(rig):
    """The defect at its source: the walk dropped the subtree and said nothing."""
    core, entry, project = rig
    accounting = ScanAccounting()

    scanned = scan_project(str(project), accounting=accounting)

    assert [f.path for f in scanned] == ["a.py"]
    assert accounting.unenterable_dirs == {"sub"}, accounting.unenterable_dirs
    # The failed directory is not a file bucket: nothing was seen inside it.
    assert "sub/b.py" not in accounting.could_not_read
    assert accounting.walked == 1
    assert (accounting.walked
            == accounting.indexed + accounting.uncommitted
            + accounting.skipped_total)


def test_a_subtree_it_could_not_enter_is_not_reportable_as_deleted(rig):
    """The row under the closed directory is an evidence gap, not a deletion."""
    core, entry, project = rig

    verdict = core.staleness(entry)

    assert verdict.state == staleness.UNKNOWN, verdict
    assert [(r.kind, r.file) for r in verdict.reasons] == [], verdict.reasons
    assert verdict.reason_count == 0
    assert verdict.unenterable_count == 1
    assert verdict.unenterable == ("sub",)
    assert verdict.detail and "could not be entered" in verdict.detail
    assert "sub" in verdict.detail


def test_fresh_is_never_returned_over_an_unenterable_directory(rig):
    """No proven change and an unlooked-at subtree is NO evidence, not fresh."""
    core, entry, project = rig

    verdict = core.staleness(entry)
    line = staleness.warning_line(verdict, str(project))

    assert verdict.state != staleness.FRESH
    assert line is not None
    assert "index is stale" not in line, line
    assert "deleted" not in line, line


def test_the_state_block_carries_the_unenterable_fact(rig):
    """A machine reader gets the count, the capped paths and the phrase."""
    core, entry, project = rig

    block = staleness.block(core.staleness(entry))

    assert block["unenterable_count"] == 1
    assert block["unenterable_dirs"] == ["sub"]
    assert "could not be entered" in (block["unenterable_summary"] or "")
    assert block["stale"] is False
    assert block["stale_known"] is False


def test_a_pass_keeps_the_rows_under_an_unenterable_directory(rig):
    """The repair the banner names must not purge what it could not walk."""
    core, entry, project = rig
    before = _rows(core, entry)
    assert before == {"a.py", "sub/b.py"}

    result = core.run_index(entry.slug, str(project), vectors="on")

    assert result["state"] == "idle", result
    assert result["result"]["files_deleted"] == 0, result
    assert _rows(core, entry) == before
    assert _error_rows(core, entry) == 0
    assert core.staleness(entry).state != staleness.STALE


def test_a_forced_full_pass_also_keeps_them(rig):
    """The force_full arm deletes from its own scan set too: same exclusion."""
    core, entry, project = rig
    before = _rows(core, entry)

    result = core.run_index(entry.slug, str(project), force=True, vectors="on")

    assert result["state"] == "idle", result
    assert result["result"]["files_deleted"] == 0, result
    assert _rows(core, entry) == before
    assert _error_rows(core, entry) == 0


def test_the_pass_accounting_reconciles_against_the_walk_it_could_make(rig):
    """Rows never walked are not `indexed`: the identity stays true."""
    core, entry, project = rig

    assert core.run_index(entry.slug, str(project), vectors="on")["state"] == "idle"
    manifest = core.read_manifest(entry.slug)
    try:
        acc = read_scan_accounting(manifest)
    finally:
        manifest.close()

    assert acc is not None
    assert acc["walked"] == (acc["indexed"] + acc["uncommitted"]
                             + acc["skipped_total"]), acc


def test_a_proven_change_elsewhere_reads_stale_with_the_gap_beside_it(rig):
    """Suppressing a repair a tree provably needs would be the worse answer."""
    core, entry, project = rig
    (project / "c.py").write_text("def c():\n    return 3\n", encoding="utf-8")

    verdict = core.staleness(entry)
    line = staleness.warning_line(verdict, str(project))

    assert verdict.state == staleness.STALE, verdict
    assert [(r.kind, r.file) for r in verdict.reasons] == [("added", "c.py")]
    assert verdict.unenterable_count == 1
    assert "could not be entered" in (line or ""), line
    assert "added" in (line or ""), line


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
