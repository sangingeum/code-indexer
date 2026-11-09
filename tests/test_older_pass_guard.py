"""A pass whose code is older than the recorded fingerprint must not delete.

Measured failure: a watcher started on older code kept running after the fleet
was rebuilt. It refused the fingerprint re-stamp but its incremental pass still
ran a delete step derived from its OWN scan set, so files the older scanner
could not see lost their rows (vivarium-sim 823 -> 811). The fix refuses the
whole pass: a pass that cannot vouch for its scan set must neither delete nor
write, and the refusal is the rebuild class (exit 4) naming the remedy.

The legitimate direction -- manifest built by OLDER code, this process newer --
must still rebuild, delete what is genuinely gone, and re-stamp.

Two tests are green at the base commit 78fcd64, and both are the UPGRADE
direction -- the one the refusal must not over-reach. On the vectors-on arm
`test_a_manifest_from_older_code_still_rebuilds_and_deletes` passed because the
base code already took exactly that path: `manifest_newer_than` returned False
for a LOWER recorded version, so the else-branch compared the row axes, saw the
extraction-version difference and set `rebuild`, the ordinary pass then purged the
genuinely gone file and re-stamped the current fingerprint. On the vectors-off arm
`test_a_vectors_off_manifest_from_older_code_still_deletes_and_restamps_incrementally`
also passed, for a different reason, and that reason was the hole: with no
embedder the base built no `current_fp`, so `row_diffs` came back empty and the
older axes were never COMPARED -- the pass ran incrementally and the row-side
stamp at the end of the pass wrote the current versions unconditionally. The test
asserted exactly that, so it was green by way of the missing comparison. The
refusal added by the rewrite is the NEWER direction only. The OLDER direction on
that arm is now a forced REPARSE (owner ruling): the recorded version
keys are compared directly, a lower value rebuilds every row, and the pass still
embeds nothing because the vectors-off arm never queues a chunk. The cases below
are the guard in both directions.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import code_indexer.indexer as indexer_mod  # noqa: E402
import pytest  # noqa: E402

from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.fingerprint import read_fingerprint  # noqa: E402
from code_indexer.graph_extract import EXTRACTION_VERSION  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402


class StubEmbedder:
    """Counts every embed request, so "it embedded nothing" is witnessed."""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.embed_calls: list[int] = []

    def dimension(self) -> int:
        return self.dim

    def embed(self, texts):
        self.embed_calls.append(len(texts))
        return [[0.1] * self.dim for _ in texts]


class StubStore:
    def __init__(self) -> None:
        self.collections: set[str] = set()
        self.aliases: dict[str, str] = {}
        self.upserts: list[tuple[str, int]] = []
        self.purges: list[tuple[str, str]] = []

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

    def purge_file_points(self, name: str, project_path: str, path: str,
                          **kwargs) -> int:
        self.purges.append((name, path))
        return 0

    def count_points(self, name: str) -> int:
        return sum(n for _, n in self.upserts) - len(self.purges)

    def search(self, *args, **kwargs):
        return []


def _write_functions(path: str, count: int) -> None:
    parts = []
    for j in range(count):
        parts.append(
            f"def handler_{j}(value):\n"
            f"    return value + {j}\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))


@pytest.fixture()
def rig():
    tmp = tempfile.mkdtemp(prefix="ci-guard-")
    project = tempfile.mkdtemp(prefix="ci-guard-proj-")
    _write_functions(os.path.join(project, "alpha.py"), 3)
    # The multibyte-straddle name the older scanner lost: it exists, and the
    # recorded (newer) index holds a row for it.
    _write_functions(os.path.join(project, "caf\u00e9_na\u00efve.py"), 3)
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub-model",
                 index_root=tmp, embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=3,
                 watch_sweep_interval=300)
    store = StubStore()
    core = Core(cfg)
    embedder = StubEmbedder()
    core.embedder = embedder  # type: ignore[assignment]
    core.store = store  # type: ignore[assignment]
    core.indexer.embedder = embedder  # type: ignore[assignment]
    core.indexer.store = store  # type: ignore[assignment]
    reg = Registry(os.path.join(tmp, "registry.db"))
    entry = reg.add(project, name="guardproj")
    yield core, reg, entry, project, store, embedder
    reg.close()
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(project, ignore_errors=True)


def _set_extraction_version(core, entry, version: int) -> None:
    manifest = core.manifest_for(entry.slug)
    try:
        manifest.set_meta("extraction_version", str(version))
    finally:
        manifest.close()


def _stored_extraction_version(core, entry) -> int | None:
    manifest = core.manifest_for(entry.slug)
    try:
        fp = read_fingerprint(manifest)
        return fp.extraction_version if fp is not None else None
    finally:
        manifest.close()


def _hide_a_file_from_the_scan(monkeypatch, hidden: str) -> None:
    """Stand in for the older scanner's blind spot: the file exists, but this
    process's scan set does not return it."""
    real = indexer_mod.scan_project

    def narrowed(*args, **kwargs):
        return [f for f in real(*args, **kwargs) if f.path != hidden]

    monkeypatch.setattr(indexer_mod, "scan_project", narrowed)


def _snapshot(core, entry) -> tuple[set[str], int, list]:
    manifest = core.manifest_for(entry.slug)
    try:
        return (set(manifest.all_files()), len(manifest.all_chunks()),
                [dict(r) for r in manifest.last_index_errors(50)])
    finally:
        manifest.close()


def _drop_a_symbol_row(core, entry, path: str, name: str) -> None:
    """Leave the row set an older extraction would have left: one symbol
    short. A re-stamp cannot repair it; only a reparse re-extracts it."""
    manifest = core.manifest_for(entry.slug)
    try:
        kept = [s for s in manifest.symbols_for_file(path) if s.name != name]
        manifest.replace_file_symbols(path, kept)
    finally:
        manifest.close()


def _symbol_names(core, entry, path: str) -> set[str]:
    manifest = core.manifest_for(entry.slug, writable=False)
    try:
        return {s.name for s in manifest.symbols_for_file(path)}
    finally:
        manifest.close()


def test_a_newer_manifest_pass_deletes_no_row(rig, monkeypatch):
    """The recorded-newer pass refuses: the row for the file its older scan
    cannot see survives, and the store is never told to purge it."""
    core, reg, entry, project, store, _embedder = rig
    assert core.run_index(entry.slug, project)["state"] == "idle"
    files_before, chunks_before, _errors = _snapshot(core, entry)
    assert "caf\u00e9_na\u00efve.py" in files_before
    _set_extraction_version(core, entry, EXTRACTION_VERSION + 1)
    store.upserts.clear()
    store.purges.clear()
    _hide_a_file_from_the_scan(monkeypatch, "caf\u00e9_na\u00efve.py")

    result = core.run_index(entry.slug, project)

    files_after, chunks_after, _errors = _snapshot(core, entry)
    assert files_after == files_before, "a pass under a newer manifest deleted"
    assert chunks_after == chunks_before
    assert store.purges == []
    assert result["state"] == "error"


def test_a_newer_manifest_pass_writes_no_row_or_vector(rig, monkeypatch):
    """Nor does it embed: the embedder sees no request at all, nothing reaches
    the store, no chunk error is recorded, and the recorded (newer)
    fingerprint still stands."""
    core, reg, entry, project, store, embedder = rig
    assert core.run_index(entry.slug, project)["state"] == "idle"
    newer = EXTRACTION_VERSION + 1
    _set_extraction_version(core, entry, newer)
    store.upserts.clear()
    embedder.embed_calls.clear()
    _hide_a_file_from_the_scan(monkeypatch, "alpha.py")

    result = core.run_index(entry.slug, project)

    # Measured on the embedder itself: a refusal that read the manifest and
    # then embedded anyway would leave a call here.
    assert embedder.embed_calls == [], "the refused pass must not embed"
    assert result["state"] == "error"
    assert store.upserts == []
    assert _stored_extraction_version(core, entry) == newer


def test_the_refusal_names_the_manifest_and_the_remedy(rig, monkeypatch):
    core, reg, entry, project, store, _embedder = rig
    assert core.run_index(entry.slug, project)["state"] == "idle"
    newer = EXTRACTION_VERSION + 1
    _set_extraction_version(core, entry, newer)

    result = core.run_index(entry.slug, project)

    message = result["error"]
    assert result["state"] == "error"
    assert "NEWER code" in message
    assert entry.slug in message
    assert f"extr={newer}" in message
    assert "unwatch" in message
    assert "reindex-project" in message


def test_the_refusal_raises_the_rebuild_class_that_exits_four(rig):
    """The pass raises the rebuild class the exit table gives 4, with the
    remedy in its own message (core reports a pass failure as state=error)."""
    from code_indexer.errors import RebuildRequiredError

    core, reg, entry, project, store, _embedder = rig
    assert core.run_index(entry.slug, project)["state"] == "idle"
    newer = EXTRACTION_VERSION + 1
    _set_extraction_version(core, entry, newer)
    manifest = core.manifest_for(entry.slug, writable=True)
    upserts_before = list(store.upserts)
    try:
        with pytest.raises(RebuildRequiredError) as caught:
            core.indexer.index_project(project, entry.slug, manifest)
    finally:
        manifest.close()
    assert store.upserts == upserts_before
    assert caught.value.exit_code == 4
    assert "NEWER code" in str(caught.value)
    assert "unwatch" in str(caught.value)
    assert "reindex-project" in str(caught.value)


def test_a_manifest_from_older_code_still_rebuilds_and_deletes(rig):
    """The legitimate upgrade: older recorded axes rebuild, a file genuinely
    gone still loses its row, and the fingerprint is re-stamped to current."""
    core, reg, entry, project, store, _embedder = rig
    assert core.run_index(entry.slug, project)["state"] == "idle"
    _set_extraction_version(core, entry, EXTRACTION_VERSION - 1)
    os.remove(os.path.join(project, "alpha.py"))
    store.upserts.clear()

    result = core.run_index(entry.slug, project)

    assert result["state"] == "idle"
    assert result["result"]["files_deleted"] == 1
    assert result["result"]["chunks_embedded"] > 0, "upgrade path must re-embed"
    files_after, _chunks, _errors = _snapshot(core, entry)
    assert files_after == {"caf\u00e9_na\u00efve.py"}
    assert _stored_extraction_version(core, entry) == EXTRACTION_VERSION


def test_a_vectors_off_pass_under_a_newer_manifest_deletes_no_row(
        rig, monkeypatch):
    """The service-free arm of the same guard.

    A vectors-off pass builds no fingerprint, but its harm is vectors-free:
    the delete step runs from this process's own scan set and
    ``write_row_side`` stamps this older process's chunker/extraction
    versions over the newer ones recorded. The version ordering is still
    answerable without a service, so this pass must refuse too.
    """
    core, reg, entry, project, store, _embedder = rig
    assert core.run_index(entry.slug, project,
                          vectors="off")["state"] == "idle"
    files_before, chunks_before, _errors = _snapshot(core, entry)
    assert "caf\u00e9_na\u00efve.py" in files_before
    newer = EXTRACTION_VERSION + 1
    _set_extraction_version(core, entry, newer)
    store.purges.clear()
    _hide_a_file_from_the_scan(monkeypatch, "caf\u00e9_na\u00efve.py")

    result = core.run_index(entry.slug, project, vectors="off")

    files_after, chunks_after, _errors = _snapshot(core, entry)
    assert files_after == files_before, (
        "a vectors-off pass under a newer manifest deleted a row")
    assert chunks_after == chunks_before
    assert store.purges == []
    assert result["state"] == "error"
    assert "NEWER code" in result["error"]
    manifest = core.manifest_for(entry.slug)
    try:
        assert manifest.get_meta("extraction_version") == str(newer), (
            "the refused pass regressed the recorded extraction version")
    finally:
        manifest.close()


def test_a_vectors_off_pass_under_a_newer_full_fingerprint_also_refuses(
        rig, monkeypatch):
    """A vectors-on manifest, then a vectors-off pass: ``current_fp`` is None
    on that arm, so the recorded version keys must be compared directly."""
    core, reg, entry, project, store, _embedder = rig
    assert core.run_index(entry.slug, project)["state"] == "idle"
    files_before, _chunks, _errors = _snapshot(core, entry)
    newer = EXTRACTION_VERSION + 1
    _set_extraction_version(core, entry, newer)
    store.purges.clear()
    _hide_a_file_from_the_scan(monkeypatch, "caf\u00e9_na\u00efve.py")

    result = core.run_index(entry.slug, project, vectors="off")

    files_after, _chunks, _errors = _snapshot(core, entry)
    assert files_after == files_before
    assert store.purges == []
    assert result["state"] == "error"
    assert "NEWER code" in result["error"]


def test_a_vectors_off_pass_over_older_axes_reparses_and_embeds_nothing(rig):
    """The legitimate direction on the vectors-off arm forces a REPARSE.

    A manifest whose recorded row axes are older still lets the pass run (the
    NEWER direction is what refuses), but the pass must not re-stamp the axes
    current over rows the older extraction produced: it reparses every file,
    so the rows it leaves behind are this build's. The tree itself is
    unchanged: the work is the reparse, and a vectors-off pass never embeds.
    """
    core, reg, entry, project, _store, embedder = rig
    assert core.run_index(entry.slug, project,
                          vectors="off")["state"] == "idle"
    files_before, chunks_before, _errors = _snapshot(core, entry)
    # What the fixture's older extraction would have left behind: one symbol
    # row short. No version stamp can bring it back; only a reparse re-extracts
    # it, so this is the row-level fact that says "reparsed, not re-stamped".
    _drop_a_symbol_row(core, entry, "caf\u00e9_na\u00efve.py", "handler_2")
    _set_extraction_version(core, entry, EXTRACTION_VERSION - 1)
    embedder.embed_calls.clear()

    result = core.run_index(entry.slug, project, vectors="off")

    assert result["state"] == "idle"
    assert result["result"]["files_deleted"] == 0
    assert result["result"]["files_indexed"] == 2, (
        "older row axes must force a reparse of every file, not a re-stamp")
    assert result["result"]["chunks_embedded"] == 0, (
        "a vectors-off pass must embed nothing")
    assert embedder.embed_calls == [], "the vectors-off arm embedded"
    files_after, chunks_after, _errors = _snapshot(core, entry)
    assert files_after == files_before
    assert chunks_after == chunks_before
    assert _symbol_names(core, entry, "caf\u00e9_na\u00efve.py") == {
        "handler_0", "handler_1", "handler_2"}, (
        "the dropped symbol row is not back: the pass re-stamped without "
        "reparsing")
    manifest = core.manifest_for(entry.slug)
    try:
        assert manifest.get_meta("extraction_version") == str(
            EXTRACTION_VERSION)
    finally:
        manifest.close()


def test_a_vectors_off_upgrade_still_deletes_a_gone_file(rig):
    """The older direction on the vectors-off arm still purges a file that is
    genuinely gone -- the reparse must not turn the pass into a no-op."""
    core, reg, entry, project, _store, _embedder = rig
    assert core.run_index(entry.slug, project,
                          vectors="off")["state"] == "idle"
    _set_extraction_version(core, entry, EXTRACTION_VERSION - 1)
    os.remove(os.path.join(project, "alpha.py"))

    result = core.run_index(entry.slug, project, vectors="off")

    assert result["state"] == "idle"
    assert result["result"]["files_deleted"] == 1
    assert result["result"]["chunks_embedded"] == 0
    files_after, _chunks, _errors = _snapshot(core, entry)
    assert files_after == {"caf\u00e9_na\u00efve.py"}


def test_an_unchanged_tree_at_current_axes_reparses_nothing(rig):
    """The other direction of the same comparison: a tree already at the
    recorded row axes has no work, so the pass is genuinely incremental
    (files_indexed 0) and still embeds nothing."""
    core, reg, entry, project, _store, embedder = rig
    assert core.run_index(entry.slug, project,
                          vectors="off")["state"] == "idle"
    embedder.embed_calls.clear()

    result = core.run_index(entry.slug, project, vectors="off")

    assert result["state"] == "idle"
    assert result["result"]["files_indexed"] == 0
    assert result["result"]["chunks_embedded"] == 0
    assert embedder.embed_calls == []


def test_a_refused_pass_exits_four_from_reindex_project(tmp_path,
                                                        monkeypatch):
    """The class survives the pass seam: a caller sees the rebuild exit code
    (4), not the generic failure (1), so a refusal is distinguishable from an
    internal fault."""
    from typer.testing import CliRunner

    import code_indexer.cli as cli
    from code_indexer.cli import app

    project = tmp_path / "proj"
    project.mkdir()
    _write_functions(str(project / "alpha.py"), 3)
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub-model",
                 index_root=str(tmp_path / "state"), embed_batch=48,
                 upsert_batch=256, max_file_bytes=1048576, watch_debounce=3,
                 watch_sweep_interval=300)
    store = StubStore()
    core = Core(cfg)
    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = store  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    try:
        entry = reg.add(str(project), name="refused")
        assert core.run_index(entry.slug, str(project),
                              vectors="off")["state"] == "idle"
        _set_extraction_version(core, entry, EXTRACTION_VERSION + 1)
        monkeypatch.setattr(cli, "_get_core", lambda skip: core)
        result = CliRunner().invoke(
            app, ["reindex-project", str(project), "--vectors", "off"])
    finally:
        reg.close()
    assert result.exit_code == 4, (result.output, result.stderr)
    assert result.stdout == ""
    line = result.stderr.strip().splitlines()[-1]
    assert line.startswith("RebuildRequiredError: "), result.stderr
    assert "NEWER code" in line
