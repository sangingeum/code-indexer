"""A path that exists but is not a regular file is not `unreadable`.

Two spellings of one fact: a FIFO (or socket, device, or a directory wearing a
file's name) was counted under ``skipped['unreadable']`` and never added to
``could_not_read``. So the bucket meant two different things, and the diff
could not tell them apart: measured, ``skipped={'unreadable': 1}
could_not_read=[] verdict=stale reasons=[('deleted', 'a.py')]`` -- while a file
this process genuinely could not READ reads ``fresh`` with
``unreadable=['a.py']`` and keeps its row.

The decision this pins: a non-regular path gets its own skip reason
(``not-a-file``) and is NOT ``unreadable`` and NOT ``could_not_read``, and a
row for such a path MAY be deleted -- the pass looked and found nothing it
could ever parse. ``unreadable`` then means exactly one thing: the pass tried
and could not look.
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
from code_indexer.scanner import ScanAccounting, scan_project  # noqa: E402


def _config(index_root: str) -> Config:
    return Config(ollama_url="", qdrant_url="", embed_model="stub-model",
                  index_root=index_root, embed_batch=48, upsert_batch=256,
                  max_file_bytes=1048576, watch_debounce=3,
                  watch_sweep_interval=300)


@pytest.fixture()
def rig(tmp_path):
    """One indexed file, which is then replaced by a FIFO."""
    index_root = tmp_path / "index"
    index_root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (project / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    reg = Registry(str(index_root / "registry.db"))
    entry = reg.add(str(project), name="notafile")
    core = Core(_config(str(index_root)))
    core.registry = reg
    assert core.run_index(entry.slug, str(project), vectors="off")["state"] == "idle"
    (project / "a.py").unlink()
    os.mkfifo(project / "a.py")
    try:
        yield core, entry, project
    finally:
        reg.close()


def _rows(core: Core, entry) -> set[str]:
    manifest = core.read_manifest(entry.slug)
    try:
        return set(manifest.all_files())
    finally:
        manifest.close()


def test_a_fifo_is_counted_not_a_file_and_not_unreadable(rig):
    """The defect at its source: one bucket, two spellings, no way to tell."""
    core, entry, project = rig
    accounting = ScanAccounting()

    scanned = scan_project(str(project), accounting=accounting)

    assert [f.path for f in scanned] == ["b.py"]
    assert accounting.skipped["not-a-file"] == 1, dict(accounting.skipped)
    assert "unreadable" not in accounting.skipped, dict(accounting.skipped)
    assert accounting.could_not_read == set()


def test_the_old_reading_is_gone_the_row_is_not_called_deleted_by_a_gap(rig):
    """The path is not an evidence gap: the pass really did look at it."""
    core, entry, project = rig

    verdict = core.staleness(entry)

    assert [(r.kind, r.file) for r in verdict.reasons] == [("deleted", "a.py")]
    assert verdict.state == staleness.STALE
    assert [u.file for u in verdict.unreadable] == []


def test_a_row_for_a_path_that_is_no_longer_a_file_may_be_deleted(rig):
    """Deleting it is a DECISION: nothing indexable is there any more."""
    core, entry, project = rig

    result = core.run_index(entry.slug, str(project), vectors="off")

    assert result["state"] == "idle", result
    assert result["result"]["files_deleted"] == 1, result
    assert _rows(core, entry) == {"b.py"}
    assert core.staleness(entry).state == staleness.FRESH


def test_unreadable_still_means_the_pass_could_not_read(tmp_path):
    """The control: a file this process cannot open keeps its meaning."""
    project = tmp_path / "tree"
    project.mkdir()
    (project / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    os.chmod(project / "a.py", 0o000)
    accounting = ScanAccounting()
    try:
        scan_project(str(project), accounting=accounting)
    finally:
        os.chmod(project / "a.py", 0o600)

    assert accounting.skipped["unreadable"] == 1, dict(accounting.skipped)
    assert "not-a-file" not in accounting.skipped, dict(accounting.skipped)
    assert accounting.could_not_read == {"a.py"}


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
