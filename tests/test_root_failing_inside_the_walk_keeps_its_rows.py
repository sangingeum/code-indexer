"""A root that fails INSIDE the walk is the root, not a directory named ``.``.

``scan_project`` probes its own root before walking, so a root this process
cannot open is an ``OSError`` the read side answers ``unknown`` and a pass
writes nothing for. The probe and the walk are two opens of the same directory
microseconds apart, and the window between them is real: a concurrent unmount,
an autofs expiry or a stale mount takes the root away after the probe
succeeded. The walk's own failure then reaches ``on_walk_error``, whose
project-relative name for the root is ``"."`` -- and ``"."`` matches no row
under the one predicate (``path == d or path.startswith(d + "/")``). The
pass's exclusion set stayed empty and every row was deleted: the whole-project
purge read as "the tree emptied", at the one depth the recorded name cannot
express. The defect was measured (root probe succeeding, the walk's scandir
failing a moment later) with ``scanned: []``, ``unenterable: ['.']`` and every
row in ``deleted``.

The fixture makes exactly that case: ``os.scandir`` answers the probe and
fails for the walk's own open of the same root. A ``chmod 000`` on the root
cannot reproduce it -- the probe meets the same failure first and the walk is
never reached.

The safety property pinned here: when the walk cannot enter the root, the
failure is raised, the pass writes nothing, every row survives, and the read
side answers ``unknown`` -- never ``stale`` with ``deleted`` reasons and never
``fresh``.
"""

from __future__ import annotations

import errno
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer import scanner, staleness  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402
from code_indexer.scanner import ScanAccounting, scan_project  # noqa: E402

_REAL_SCANDIR = os.scandir


class UnmountingScandir:
    """``os.scandir`` that opens the root once and fails every time after.

    The first open of the root is the scanner's own probe; the next one is the
    walk's, and failing THAT is what a mount that went away between the two
    produces. Every other path is handed to the real ``os.scandir``.
    """

    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)
        self.opens = 0

    def reset(self) -> None:
        self.opens = 0

    def __call__(self, path="."):
        if os.path.abspath(os.fspath(path)) == self.root:
            self.opens += 1
            if self.opens > 1:
                raise PermissionError(errno.EACCES, "Permission denied",
                                      self.root)
        return _REAL_SCANDIR(path)


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
    """Two indexed files, one of them a level down, under a live root."""
    index_root = tmp_path / "index"
    index_root.mkdir()
    project = tmp_path / "project"
    (project / "sub").mkdir(parents=True)
    (project / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (project / "sub" / "c.py").write_text("def c():\n    return 3\n",
                                           encoding="utf-8")
    reg = Registry(str(index_root / "registry.db"))
    entry = reg.add(str(project), name="vanishingroot")
    core = Core(_config(str(index_root)))
    stub = StubEmbedder()
    core.embedder = stub  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = stub  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    core.registry = reg
    assert core.run_index(entry.slug, str(project), vectors="on")["state"] == "idle"
    assert core.staleness(entry).state == staleness.FRESH
    try:
        yield core, entry, project
    finally:
        reg.close()


@pytest.fixture()
def vanishing_root(monkeypatch):
    """An ``os.scandir`` the caller resets before each walk it arms."""

    def arm(project) -> UnmountingScandir:
        fake = UnmountingScandir(str(project))
        fake.reset()
        monkeypatch.setattr(scanner.os, "scandir", fake)
        return fake

    return arm


def _rows(core: Core, entry) -> set[str]:
    manifest = core.read_manifest(entry.slug)
    try:
        return set(manifest.all_files())
    finally:
        manifest.close()


def test_the_walk_raises_when_the_root_it_could_not_enter_is_the_root(
        rig, vanishing_root):
    """The defect at its source: no directory named ``.`` is recorded."""
    core, entry, project = rig
    fake = vanishing_root(project)
    accounting = ScanAccounting()

    with pytest.raises(PermissionError):
        scan_project(str(project), accounting=accounting)

    # The probe opened the root and the walk did not: the failure is the one
    # the probe cannot catch, and nothing was filed under a name no row has.
    assert fake.opens == 2, fake.opens
    assert accounting.unenterable_dirs == set(), accounting.unenterable_dirs


def test_the_read_side_answers_unknown_for_a_root_lost_inside_the_walk(
        rig, vanishing_root):
    core, entry, project = rig
    fake = vanishing_root(project)

    verdict = core.staleness(entry)

    assert fake.opens == 2, fake.opens
    assert verdict.state == staleness.UNKNOWN, verdict
    assert verdict.reasons == (), verdict.reasons
    assert verdict.reason_count == 0
    assert [r.kind for r in verdict.reasons if r.kind == "deleted"] == []
    assert verdict.detail and "unreadable" in verdict.detail, verdict.detail
    block = staleness.block(verdict)
    assert block["stale"] is False
    assert block["stale_known"] is False


def test_a_pass_writes_nothing_when_the_root_fails_inside_the_walk(
        rig, vanishing_root):
    """The incremental arm: the pass refuses, and every row survives."""
    core, entry, project = rig
    before = _rows(core, entry)
    assert before == {"a.py", "sub/c.py"}
    fake = vanishing_root(project)

    result = core.run_index(entry.slug, str(project), vectors="on")

    assert fake.opens == 2, fake.opens
    assert result["state"] == "error", result
    assert "Permission denied" in result["error"], result
    # A pass that wrote nothing reports no deletions; the row set is the
    # proof, and it is the same set it had before the walk failed.
    assert result.get("result", {}).get("files_deleted", 0) == 0, result
    assert _rows(core, entry) == before
    assert core.staleness(entry).state != staleness.STALE


def test_a_forced_full_pass_writes_nothing_either(rig, vanishing_root):
    """The force_full arm deletes from its own scan set: same refusal."""
    core, entry, project = rig
    before = _rows(core, entry)
    fake = vanishing_root(project)

    result = core.run_index(entry.slug, str(project), force=True, vectors="on")

    assert fake.opens == 2, fake.opens
    assert result["state"] == "error", result
    assert result.get("result", {}).get("files_deleted", 0) == 0, result
    assert _rows(core, entry) == before
    assert core.staleness(entry).state != staleness.STALE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
