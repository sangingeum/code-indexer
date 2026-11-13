"""A transient stat miss keeps a row for exactly ONE pass, then purges it.

The boundary the ruling pins: a name the walk LISTED and could not stat is an
unreadable path -- the row survives, the pass counts and names it
``unreadable``, and the identity ``walked == indexed + uncommitted + skipped``
still holds. When the name is gone from the next walk, the row is purged as an
ordinary deletion, with no ``index_errors`` row left behind and no reason
naming a path the tree no longer holds.

Why one pass and not zero: a stat failure is not proof of a deletion (an I/O
error, a momentary permission change), and purging on it would be ``fresh`` by
forgetting. Why not forever: the tree answered on the next walk, and holding
the row past that would keep a banner nothing can clear.
"""

from __future__ import annotations

import errno
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer import staleness  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402
from code_indexer.scanner import read_scan_accounting  # noqa: E402


def _config(index_root: str) -> Config:
    return Config(ollama_url="", qdrant_url="", embed_model="stub-model",
                  index_root=index_root, embed_batch=48, upsert_batch=256,
                  max_file_bytes=1048576, watch_debounce=3,
                  watch_sweep_interval=300)


@pytest.fixture()
def rig(tmp_path):
    """Two indexed files, and the means to make one stat fail once."""
    index_root = tmp_path / "index"
    index_root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (project / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    reg = Registry(str(index_root / "registry.db"))
    entry = reg.add(str(project), name="statmiss")
    core = Core(_config(str(index_root)))
    core.registry = reg
    assert core.run_index(entry.slug, str(project), vectors="off")["state"] == "idle"
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


def _accounting(core: Core, entry) -> dict:
    manifest = core.read_manifest(entry.slug)
    try:
        return read_scan_accounting(manifest) or {}
    finally:
        manifest.close()


def _error_count(core: Core, entry) -> int:
    manifest = core.read_manifest(entry.slug)
    try:
        return manifest.count_index_errors()
    finally:
        manifest.close()


def _stat_that_fails_for(target, real_stat):
    """``os.stat`` that raises for ``target`` and delegates for everything."""
    wanted = os.fspath(target)

    def failing(path, *args, **kwargs):
        try:
            if os.fspath(path) == wanted:
                raise OSError(errno.EIO, "transient I/O error", wanted)
        except TypeError:
            pass
        return real_stat(path, *args, **kwargs)

    return failing


def test_a_transient_stat_miss_keeps_the_row_for_exactly_one_pass(rig,
                                                                 monkeypatch):
    """Pass N keeps and names it; pass N+1 purges it cleanly."""
    core, entry, project = rig
    target = project / "a.py"
    assert _rows(core, entry) == {"a.py", "b.py"}

    # --- pass N: the name is LISTED by the walk and os.stat raises. --------
    real_stat = os.stat
    monkeypatch.setattr(os, "stat", _stat_that_fails_for(target, real_stat))
    try:
        first = core.run_index(entry.slug, str(project), vectors="off")
        # The state read happens while the miss is still live: the pass's own
        # account and the banner must describe the same tree.
        verdict = core.staleness(entry)
    finally:
        monkeypatch.setattr(os, "stat", real_stat)

    assert first["state"] == "idle", first
    assert first["result"]["files_deleted"] == 0, first
    assert _rows(core, entry) == {"a.py", "b.py"}
    acc = _accounting(core, entry)
    assert acc["skipped"].get("unreadable") == 1, acc
    assert "a.py" in (acc["examples"].get("unreadable") or []), acc
    assert acc["walked"] == (acc["indexed"] + acc["uncommitted"]
                             + acc["skipped_total"]), acc
    assert not (verdict.state == staleness.STALE
                and any(r.kind == "deleted" and r.file == "a.py"
                        for r in verdict.reasons)), verdict.reasons
    assert "a.py" in [u.file for u in verdict.unreadable], verdict.unreadable
    assert verdict.state == staleness.FRESH, verdict

    # --- pass N+1: the name is gone from the walk. -------------------------
    target.unlink()

    second = core.run_index(entry.slug, str(project), vectors="off")

    assert second["state"] == "idle", second
    assert second["result"]["files_deleted"] == 1, second
    assert _rows(core, entry) == {"b.py"}
    assert _error_count(core, entry) == 0
    after = core.staleness(entry)
    assert after.state == staleness.FRESH, after
    assert after.reasons == (), after.reasons
    assert "a.py" not in [u.file for u in after.unreadable]
    line = staleness.warning_line(after, str(project))
    assert line is None or "a.py" not in line, line


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
