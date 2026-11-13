"""An existing file this process cannot read is unreadable, never binary.

The binary sniff answered ``True`` for an OSError, so a file the process could
not open was dropped from the scan set as a BINARY file: its rows were deleted
(``files_deleted 1``, ``files 0``), no error row was written, and
``index-status`` then reported a clean index. The index silently forgot a file
that still exists -- ``fresh`` by forgetting.

A genuinely binary file stays ``binary``; only the unreadable class moves.
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
    """Two indexed files, one of which the process is then denied."""
    index_root = tmp_path / "index"
    index_root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (project / "d.py").write_text("def d():\n    return 2\n", encoding="utf-8")
    reg = Registry(str(index_root / "registry.db"))
    entry = reg.add(str(project), name="denied")
    core = Core(_config(str(index_root)))
    core.registry = reg
    assert core.run_index(entry.slug, str(project), vectors="off")["state"] == "idle"
    denied = project / "d.py"
    os.chmod(denied, 0o000)
    try:
        yield core, entry, project, denied
    finally:
        os.chmod(denied, 0o600)
        reg.close()


def _rows(core: Core, entry) -> set[str]:
    manifest = core.read_manifest(entry.slug)
    try:
        return set(manifest.all_files())
    finally:
        manifest.close()


def test_the_scanner_counts_an_unopenable_file_as_unreadable_not_binary(rig):
    """The defect, at its source: the sniff's OSError was answered as binary."""
    core, entry, project, denied = rig
    accounting = ScanAccounting()

    scanned = scan_project(str(project), accounting=accounting)

    assert "d.py" not in {f.path for f in scanned}
    assert accounting.skipped["unreadable"] == 1, dict(accounting.skipped)
    assert "binary" not in accounting.skipped, dict(accounting.skipped)
    assert accounting.could_not_read == {"d.py"}


def test_a_pass_keeps_the_row_of_a_file_it_could_not_read(rig):
    """The file still exists, so the index must not silently forget it."""
    core, entry, project, denied = rig
    before = _rows(core, entry)
    assert before == {"a.py", "d.py"}

    result = core.run_index(entry.slug, str(project), vectors="off")

    assert result["state"] == "idle", result
    assert result["result"]["files_deleted"] == 0, result
    assert result["result"]["files_indexed"] == 0, result
    assert _rows(core, entry) == before


def test_the_state_read_names_the_file_unreadable_and_never_deleted(rig):
    """After the pass, the banner must not call a present file deleted."""
    core, entry, project, denied = rig
    assert core.run_index(entry.slug, str(project), vectors="off")["state"] == "idle"

    verdict = core.staleness(entry)

    assert [r.kind for r in verdict.reasons] == [], verdict.reasons
    assert verdict.state == staleness.FRESH
    assert [s.file for s in verdict.unreadable] == ["d.py"]
    assert verdict.unreadable_count == 1
    line = staleness.warning_line(verdict, str(project))
    assert line is not None and "could not be read" in line, line
    assert "deleted" not in line, line


def test_a_genuinely_binary_file_is_still_classified_binary(tmp_path):
    """The negative control: only the OSError answer changed."""
    project = tmp_path / "tree"
    project.mkdir()
    (project / "blob.bin").write_bytes(b"\x00\x01\x02\xff")
    accounting = ScanAccounting()

    scanned = scan_project(str(project), accounting=accounting)

    assert scanned == []
    assert accounting.skipped["binary"] == 1, dict(accounting.skipped)
    assert accounting.could_not_read == set()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
