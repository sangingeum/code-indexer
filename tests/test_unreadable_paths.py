"""A rowless path the check cannot OPEN is not a staleness reason.

The scan and the diff are two moments: a path can be scanned (stat, binary
sniff, hash) and then be unopenable when the diff re-reads its prefix --
permissions changed, a symlink target went away, an I/O error. Folding that
into ``added`` produced a permanent stale banner (no reindex can index a file
it cannot read), so the state is its own fact, reported beside the verdict the
way a withheld file is.

These tests drive the real machinery: a real index pass, the real scanner,
the real sensitive diff. The only simulation is the timing -- the scan rows are
captured while the file is readable, then the file is made unreadable.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer import coverage, staleness  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402
from code_indexer.scanner import scan_project  # noqa: E402


def _config(index_root: str) -> Config:
    return Config(ollama_url="", qdrant_url="", embed_model="stub-model",
                  index_root=index_root, embed_batch=48, upsert_batch=256,
                  max_file_bytes=1048576, watch_debounce=3,
                  watch_sweep_interval=300)


@pytest.fixture()
def rig():
    """One real index pass, then a scanned path made unreadable."""
    tmp = tempfile.mkdtemp(prefix="ci-unreadable-")
    project = tempfile.mkdtemp(prefix="ci-unreadable-proj-")
    with open(os.path.join(project, "a.py"), "w", encoding="utf-8") as f:
        f.write("def a():\n    return 1\n")
    reg = Registry(os.path.join(tmp, "registry.db"))
    entry = reg.add(project, name="unreadableproj")
    core = Core(_config(tmp))
    core.registry = reg
    assert core.run_index(entry.slug, project, vectors="off")["state"] == "idle"

    locked = os.path.join(project, "locked.py")
    with open(locked, "w", encoding="utf-8") as f:
        f.write("def locked():\n    return 2\n")
    # The walk sees it while it is readable; the diff meets it afterwards.
    scanned = scan_project(project)
    os.chmod(locked, 0o000)
    try:
        yield core, reg, entry, project, scanned
    finally:
        os.chmod(locked, 0o600)
        reg.close()
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def _detect(core: Core, entry, scanned):
    m = core.read_manifest(entry.slug)
    try:
        rows = m.all_files()
        return staleness.detect(rows, scanned, last_indexed=None,
                                index_revision=None), rows
    finally:
        m.close()


def test_an_unreadable_rowless_path_is_not_a_stale_reason(rig):
    core, reg, entry, project, scanned = rig
    verdict, _ = _detect(core, entry, scanned)

    assert verdict.state == staleness.FRESH
    assert verdict.reasons == ()
    assert verdict.reason_count == 0
    assert [s.file for s in verdict.unreadable] == ["locked.py"]
    assert verdict.unreadable_count == 1
    # Not a withheld file either: the sensitive layer never saw its content.
    assert verdict.withheld_count == 0


def test_the_unreadable_state_is_counted_and_named(rig):
    core, reg, entry, project, scanned = rig
    verdict, _ = _detect(core, entry, scanned)
    block = staleness.block(verdict)

    assert block["stale"] is False
    assert block["unreadable_count"] == 1
    assert block["unreadable_files"] == [{"file": "locked.py"}]
    assert block["unreadable_summary"] == \
        "1 file could not be read (permission or I/O error)"


def test_a_fresh_verdict_with_an_unreadable_file_is_explained(rig):
    """``stale: false`` with a non-zero unreadable count must not read as
    "everything was checked": the text line says what went unchecked."""
    core, reg, entry, project, scanned = rig
    verdict, _ = _detect(core, entry, scanned)
    line = staleness.warning_line(verdict, project)

    assert line is not None
    assert "could not be read" in line
    assert "not checked" in line
    # It is not a stale banner and prescribes no reindex.
    assert "index is stale" not in line
    assert "reindex-project" not in line


def test_a_clean_fresh_verdict_stays_silent(rig):
    core, reg, entry, project, scanned = rig
    # Make the file readable again and index it: now nothing is rowless and
    # nothing is unreadable, so the fresh verdict stays quiet.
    os.chmod(os.path.join(project, "locked.py"), 0o600)
    assert core.run_index(entry.slug, project, vectors="off")["state"] == "idle"
    verdict = core.staleness(entry)
    assert verdict.state == staleness.FRESH
    assert verdict.unreadable_count == 0
    assert staleness.warning_line(verdict, project) is None


def test_a_genuinely_added_readable_file_still_reports_added(rig):
    """The negative control: the unreadable state must not swallow a file the
    check really did read and find new."""
    core, reg, entry, project, scanned = rig
    added = os.path.join(project, "b.py")
    with open(added, "w", encoding="utf-8") as f:
        f.write("def b():\n    return 3\n")
    # The locked path is in the scan from before it went unreadable; the walk
    # of the current tree cannot see it (the scanner skips what it cannot
    # open), so the captured row is what the diff must still meet.
    rows = scan_project(project) + [f for f in scanned
                                    if f.path == "locked.py"]
    verdict, _ = _detect(core, entry, rows)

    assert verdict.state == staleness.STALE
    assert [(r.kind, r.file) for r in verdict.reasons] == [("added", "b.py")]
    assert [s.file for s in verdict.unreadable] == ["locked.py"]
    assert verdict.unreadable_count == 1


def test_the_read_answer_coverage_counts_it(rig):
    core, reg, entry, project, scanned = rig
    verdict, indexed = _detect(core, entry, scanned)
    block = staleness.block(verdict)
    cov = coverage.block(
        list(indexed.values()), scope=coverage.SCOPE_PROJECT,
        freshness=coverage.freshness(block),
        withheld_count=coverage.withheld_in_scope(block),
        unreadable_count=coverage.unreadable_in_scope(block))

    assert cov["files_unreadable"] == 1
    assert cov["complete"] is False
    assert "could not be read" in coverage.text_line(cov)
