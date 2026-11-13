"""A scanned path that is GONE by the read is out of the scan set, not an error.

The walk and the read are two moments. A tool creates a temp file inside the
project, the walk stats and hashes it, and the file is deleted again before
the pass opens it. That is not an unreadable file: nothing is there to read,
and the tree the pass is describing does not contain it. Recording it as an
index error left ``error_count=1`` on a clean index -- a permanent error row
about a file the tool itself had already removed.

A permission or I/O error keeps the existing ``unreadable``/error row path;
only "the file is gone" is dropped. These tests make the file disappear
between the walk and the read by wrapping the pass's scanner, and then assert
both consequences the issue names: no error row, and no ``deleted`` staleness
reason on the next read.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer import indexer as indexer_mod  # noqa: E402
from code_indexer import scanner, staleness  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402


def _config(index_root: str) -> Config:
    return Config(ollama_url="", qdrant_url="", embed_model="stub-model",
                  index_root=index_root, embed_batch=48, upsert_batch=256,
                  max_file_bytes=1048576, watch_debounce=3,
                  watch_sweep_interval=300)


@pytest.fixture()
def rig(tmp_path):
    """One project with one indexed file; the pass is vectors-off (no service)."""
    index_root = tmp_path / "index"
    index_root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "alpha.py").write_text("def alpha():\n    return 1\n",
                                      encoding="utf-8")
    reg = Registry(str(index_root / "registry.db"))
    entry = reg.add(str(project), name="vanishing")
    core = Core(_config(str(index_root)))
    core.registry = reg
    try:
        yield core, entry, project
    finally:
        reg.close()


def _vanish_after_the_walk(project, name: str):
    """The pass's scanner: return its rows, then delete ``name`` from disk."""
    real = indexer_mod.scan_project

    def scan_then_vanish(*args, **kwargs):
        rows = real(*args, **kwargs)
        assert any(row.path == name for row in rows), \
            "the walk must see the file before it vanishes"
        os.unlink(os.path.join(str(project), name))
        return rows

    return scan_then_vanish


def test_a_new_file_that_vanishes_before_the_read_is_not_an_index_error(
        rig, monkeypatch):
    """The reported case: a tool's own temp file, created then removed."""
    core, entry, project = rig
    keeper_tmp = project / "keeper-tmp-8f2c"
    keeper_tmp.write_text("scratch\n", encoding="utf-8")
    monkeypatch.setattr(indexer_mod, "scan_project",
                        _vanish_after_the_walk(project, keeper_tmp.name))

    result = core.run_index(entry.slug, str(project), vectors="off")
    monkeypatch.undo()

    assert result["state"] == "idle", result
    manifest = core.read_manifest(entry.slug)
    try:
        assert manifest.count_index_errors() == 0, \
            manifest.last_index_errors()
        assert keeper_tmp.name not in manifest.all_files()
        accounting = scanner.read_scan_accounting(manifest)
    finally:
        manifest.close()
    assert accounting is not None
    assert accounting["skipped"].get("unreadable", 0) == 0, accounting
    assert accounting["uncommitted"] == 0, accounting
    assert core.staleness(entry).state == staleness.FRESH


def test_a_row_that_vanishes_before_the_read_leaves_no_deleted_reason(
        rig, monkeypatch):
    """A file that HAD a row: the row goes with the file, not into a banner."""
    core, entry, project = rig
    beta = project / "beta.py"
    beta.write_text("def beta():\n    return 2\n", encoding="utf-8")
    assert core.run_index(entry.slug, str(project), vectors="off")["state"] == "idle"
    beta.write_text("def beta():\n    return 3\n", encoding="utf-8")
    assert core.staleness(entry).stale  # the changed file is the work to do

    monkeypatch.setattr(indexer_mod, "scan_project",
                        _vanish_after_the_walk(project, beta.name))
    result = core.run_index(entry.slug, str(project), vectors="off")
    monkeypatch.undo()

    assert result["state"] == "idle", result
    manifest = core.read_manifest(entry.slug)
    try:
        assert manifest.count_index_errors() == 0, \
            manifest.last_index_errors()
        assert beta.name not in manifest.all_files()
    finally:
        manifest.close()
    verdict = core.staleness(entry)
    assert verdict.state == staleness.FRESH, verdict.reasons
    assert "deleted" not in {reason.kind for reason in verdict.reasons}


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
