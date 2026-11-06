"""Staleness is DETECTED, never repaired: the read contract's verdicts.

A read answers from the index as of ``meta.last_indexed`` and reports
``fresh``, ``stale`` (with reasons) or ``unknown``. Nothing here runs an
index pass: ``Core.staleness`` is the read path, and the only writer a read
can trigger is the explicit ``Core.refresh`` (``--refresh``), which fails
hard when another process holds the per-project flock.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.errors import LockedError  # noqa: E402
from code_indexer.manifest import ManifestFile  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402
from code_indexer.scanner import scan_project  # noqa: E402
from code_indexer import staleness  # noqa: E402


@pytest.fixture()
def rig():
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=tempfile.mkdtemp(prefix="ci-stale-"),
                 stale_ttl=60, embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=3,
                 watch_sweep_interval=300)
    core = Core(cfg)
    project = tempfile.mkdtemp(prefix="ci-stale-proj-")
    with open(os.path.join(project, "a.py"), "w") as f:
        f.write("def a():\n    return 1\n")
    with open(os.path.join(project, "b.py"), "w") as f:
        f.write("def b():\n    return 2\n")
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(project, name="stale")
    yield core, entry, project
    reg.close()
    shutil.rmtree(cfg.index_root, ignore_errors=True)
    shutil.rmtree(project, ignore_errors=True)


def record_pass(core: Core, entry, project: str) -> None:
    """Write the manifest rows a completed pass would have written."""
    m = core.manifest_for(entry.slug)
    try:
        m.upsert_files([
            ManifestFile(f.path, f.content_hash, f.size, 1, "ok",
                         f.mtime_ns, f.inode)
            for f in scan_project(project)
        ])
        m.set_meta("last_indexed", "1762500000")
        m.set_meta("index_revision", "7")
    finally:
        m.close()


def run_pass_spy(core: Core) -> list:
    calls: list = []
    core.run_index = lambda *a, **kw: calls.append(a) or {
        "state": "idle", "result": {}}
    return calls


def test_fresh_when_the_tree_matches_the_index(rig):
    core, entry, project = rig
    record_pass(core, entry, project)
    verdict = core.staleness(entry)
    assert verdict.state == staleness.FRESH
    assert verdict.stale is False and verdict.known is True
    assert verdict.reasons == ()
    assert verdict.reason_count == 0
    assert verdict.last_indexed == "2025-11-07T07:20:00Z"
    assert verdict.index_revision == "7"


def test_modified_file_is_one_stale_reason(rig):
    core, entry, project = rig
    record_pass(core, entry, project)
    with open(os.path.join(project, "a.py"), "w") as f:
        f.write("def a():\n    return 99\n")
    verdict = core.staleness(entry)
    assert verdict.state == staleness.STALE
    assert verdict.stale is True and verdict.known is True
    assert [(r.kind, r.file) for r in verdict.reasons] == [("modified", "a.py")]
    assert verdict.reason_count == 1


def test_touching_a_file_without_changing_content_is_not_stale(rig):
    core, entry, project = rig
    record_pass(core, entry, project)
    os.utime(os.path.join(project, "a.py"), None)
    assert core.staleness(entry).state == staleness.FRESH


def test_added_and_deleted_files_are_reasons(rig):
    core, entry, project = rig
    record_pass(core, entry, project)
    with open(os.path.join(project, "c.py"), "w") as f:
        f.write("def c():\n    return 3\n")
    os.remove(os.path.join(project, "b.py"))
    verdict = core.staleness(entry)
    assert verdict.state == staleness.STALE
    kinds = [(r.kind, r.file) for r in verdict.reasons]
    assert kinds == [("added", "c.py"), ("deleted", "b.py")]


def test_reasons_are_capped_but_the_count_is_total(rig):
    core, entry, project = rig
    record_pass(core, entry, project)
    for i in range(8):
        with open(os.path.join(project, f"new{i}.py"), "w") as f:
            f.write(f"def n{i}():\n    return {i}\n")
    verdict = core.staleness(entry)
    block = staleness.block(verdict)
    assert verdict.reason_count == 8
    assert len(block["stale_reasons"]) == staleness.REASON_CAP
    assert block["stale_reason_count"] == 8
    assert block["stale"] is True and block["stale_known"] is True


def test_absent_project_path_is_unknown_not_fresh(rig):
    """An ``unknown`` verdict still reports what the manifest holds.

    The tree is gone, so the check cannot run — but an answer that describes
    the index still owes the reader the pass it describes.
    """
    core, entry, project = rig
    record_pass(core, entry, project)
    shutil.rmtree(project)
    verdict = core.staleness(entry)
    assert verdict.state == staleness.UNKNOWN
    assert verdict.known is False and verdict.stale is False
    assert "absent" in (verdict.detail or "")
    block = staleness.block(verdict)
    assert block["stale_known"] is False
    assert block["last_indexed"] == "2025-11-07T07:20:00Z"
    assert block["index_revision"] == "7"


def test_no_completed_pass_is_unknown(rig):
    core, entry, project = rig
    verdict = core.staleness(entry)
    assert verdict.state == staleness.UNKNOWN
    assert verdict.known is False


def test_a_read_never_runs_an_index_pass(rig):
    core, entry, project = rig
    record_pass(core, entry, project)
    calls = run_pass_spy(core)
    core.staleness(entry)
    assert calls == [], "staleness detection triggered an index pass"


def test_refresh_runs_one_explicit_pass(rig):
    core, entry, project = rig
    calls = run_pass_spy(core)
    core.refresh(entry)
    assert len(calls) == 1


def test_skip_stale_check_is_inert_for_refresh(rig):
    """The flag is accepted but cannot veto an explicit --refresh."""
    core, entry, project = rig
    core.skip_stale_check = True
    calls = run_pass_spy(core)
    core.refresh(entry)
    assert len(calls) == 1


def test_refresh_fails_hard_when_the_flock_is_held(rig):
    """A held per-project flock is LockedError (exit 5), never a stale answer."""
    core, entry, project = rig
    lock_path = core._lock_path(entry.slug)
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover — child process
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.write(write_fd, b"1")
            time.sleep(10)
        finally:
            os._exit(0)
    os.close(write_fd)
    try:
        os.read(read_fd, 1)  # the child holds the lock now
        with pytest.raises(LockedError, match="indexing in progress"):
            core.refresh(entry)
    finally:
        os.close(read_fd)
        os.kill(pid, 9)
        os.waitpid(pid, 0)
