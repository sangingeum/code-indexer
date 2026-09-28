"""`unwatch` subcommand + `watcher.stop_watcher`: the reverse of `watch`.

No network: embedder/store are stubbed at the Core seam, exactly like
test_watch.py. The stop path is exercised against real watcher subprocesses so
the SIGTERM genuinely travels between processes, and against small flock
holders for the stale/residue cases.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

from code_indexer.cli import app
from code_indexer.config import Config
from code_indexer.core import Core
from code_indexer.watcher import (
    pidfile_is_free,
    read_pid,
    stop_watcher,
)

runner = CliRunner()

# A CLI process with the Ollama/Qdrant seams stubbed. Deliberately contains no
# literal "watch" token before the argv subcommand, so /proc-argv introspection
# (watcher._holder_roots) still resolves the watched roots.
_BOOT = (
    "import code_indexer.core as cm\n"
    "class S:\n"
    "    def __init__(self, *a, **k): pass\n"
    "    def dimension(self): return 4\n"
    "    def embed(self, t): return [[0.0]] * len(t)\n"
    "class Q:\n"
    "    def __init__(self, *a, **k): pass\n"
    "    def collection_exists(self, n): return True\n"
    "    def create_collection(self, n, d): pass\n"
    "    def upsert_points(self, n, p): pass\n"
    "    def purge_file_points(self, *a, **k): pass\n"
    "cm.Embedder, cm.Store = S, Q\n"
    "from code_indexer.cli import app\n"
    "app()\n"
)

# A child that holds the pidfile flock like a live watcher and dies on SIGTERM.
_HOLD_LIVE = (
    "import fcntl, os, sys, time\n"
    "pidfile, marker = sys.argv[1], sys.argv[2]\n"
    "fd = os.open(pidfile, os.O_CREAT | os.O_RDWR, 0o644)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "os.ftruncate(fd, 0)\n"
    "os.write(fd, ('%d now\\n' % os.getpid()).encode())\n"
    "open(marker, 'w').write('locked')\n"
    "time.sleep(60)\n"
)

# The same, but its pidfile carries an unparsable pid.
_HOLD_GARBAGE = (
    "import fcntl, os, sys, time\n"
    "pidfile, marker = sys.argv[1], sys.argv[2]\n"
    "fd = os.open(pidfile, os.O_CREAT | os.O_RDWR, 0o644)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "os.ftruncate(fd, 0)\n"
    "os.write(fd, b'not-a-pid\\n')\n"
    "open(marker, 'w').write('locked')\n"
    "time.sleep(60)\n"
)


class _Holder:
    """A live child holding the pidfile flock (test stand-in for a watcher)."""

    def __init__(self, script: str, pidfile: str, marker: str) -> None:
        self.pidfile = pidfile
        self.marker = marker
        self.proc = subprocess.Popen(
            [sys.executable, "-c", script, pidfile, marker])
        assert _wait_for(lambda: os.path.exists(marker)), \
            "holder never acquired the pidfile flock"

    def cleanup(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)


class StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class StubStore:
    def collection_exists(self, name):
        return True

    def create_collection(self, name, dim):
        pass

    def upsert_points(self, name, points):
        pass

    def purge_file_points(self, name, project, path, min_chunk_index=0):
        pass


def _stub_core(cfg: Config) -> Core:
    c = Core(cfg)
    c.embedder = StubEmbedder()  # type: ignore[assignment]
    c.store = StubStore()  # type: ignore[assignment]
    c.indexer.embedder = c.embedder
    c.indexer.store = c.store
    return c


@pytest.fixture()
def core(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("WATCH_DEBOUNCE", "1")
    cfg = Config(
        ollama_url="http://stub", qdrant_url="http://stub", embed_model="stub",
        index_root=str(tmp_path / "state"), stale_ttl=0, embed_batch=48,
        upsert_batch=256, max_file_bytes=1048576, watch_debounce=1,
        watch_sweep_interval=3600)
    return _stub_core(cfg)


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text("def main():\n    pass\n")
    return root


def _patch_core(monkeypatch, core) -> None:
    """Bind the CLI module-level core seam to our stubbed core."""
    import code_indexer.cli as cli
    monkeypatch.setattr(cli, "_core", core)
    monkeypatch.setattr(cli, "_core_skip", False)


def _boot_env(core) -> dict:
    env = dict(os.environ)
    env["INDEX_ROOT"] = core.cfg.index_root
    env["WATCH_DEBOUNCE"] = "1"
    env["WATCH_SWEEP_INTERVAL"] = "3600"
    env["PYTHONPATH"] = "src"
    return env


def _wait_for(predicate, timeout: float = 25.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------------------------------------------------------------------------
# CLI argument handling / clean no-op (no watcher involved)
# ---------------------------------------------------------------------------

def test_unwatch_without_watcher_is_clean_noop(core, project, monkeypatch):
    """No live watcher: a clear message and exit 0 (idempotent no-op)."""
    core.registry.add(str(project))
    _patch_core(monkeypatch, core)
    result = runner.invoke(app, ["unwatch", "--all"])
    assert result.exit_code == 0, result.output
    assert "no watcher running" in result.output
    assert "watch.pid" in result.output


def test_unwatch_rejects_paths_with_all(core, project, monkeypatch):
    core.registry.add(str(project))
    _patch_core(monkeypatch, core)
    result = runner.invoke(app, ["unwatch", str(project), "--all"])
    assert result.exit_code == 1
    assert "OR --all" in result.output


def test_unwatch_requires_a_target(core, monkeypatch):
    _patch_core(monkeypatch, core)
    result = runner.invoke(app, ["unwatch"])
    assert result.exit_code == 1
    assert "at least one project path" in result.output


def test_unwatch_all_without_projects_errors(core, monkeypatch):
    _patch_core(monkeypatch, core)
    result = runner.invoke(app, ["unwatch", "--all"])
    assert result.exit_code == 1
    assert "no projects registered" in result.output


def test_unwatch_unknown_project_errors(core, tmp_path, monkeypatch):
    _patch_core(monkeypatch, core)
    result = runner.invoke(app, ["unwatch", str(tmp_path / "nope")])
    assert result.exit_code == 1
    assert "not registered" in result.output


# ---------------------------------------------------------------------------
# stop_watcher / pidfile_is_free (flock liveness, never pid existence)
# ---------------------------------------------------------------------------

def test_pidfile_is_free_missing_and_held(tmp_path):
    pidfile = str(tmp_path / "watch.pid")
    assert pidfile_is_free(pidfile) is True  # absent counts as free

    from code_indexer.watcher import PidFileLock
    lock = PidFileLock(pidfile)
    try:
        assert lock.acquire() is True
        assert pidfile_is_free(pidfile) is False
    finally:
        lock.release(unlink=True)
    assert pidfile_is_free(pidfile) is True


def test_stop_watcher_absent_clears_stale_pidfile(tmp_path):
    """A dead pid leaves residue: reported absent, residue removed, exit 0."""
    pidfile = tmp_path / "watch.pid"
    pidfile.write_text("999999 2026-01-01T00:00:00+0900\n")
    result = stop_watcher(str(pidfile), timeout=1.0)
    assert result["state"] == "absent"
    assert result["pid"] is None
    assert not pidfile.exists(), "stale pidfile residue not cleaned up"


def test_stop_watcher_signals_live_holder(tmp_path):
    """A live flock holder is SIGTERMed and reported stopped."""
    pidfile = str(tmp_path / "watch.pid")
    marker = str(tmp_path / "marker")
    holder = _Holder(_HOLD_LIVE, pidfile, marker)
    try:
        assert read_pid(pidfile) == holder.proc.pid
        result = stop_watcher(pidfile, timeout=10.0)
        assert result["state"] == "stopped"
        assert result["pid"] == holder.proc.pid
        assert holder.proc.wait(timeout=10) is not None, "holder survived SIGTERM"
        assert pidfile_is_free(pidfile) is True
        assert not os.path.exists(pidfile)
    finally:
        holder.cleanup()


def test_stop_watcher_unresolvable_when_pid_unreadable(tmp_path):
    """A live holder with an unparsable pid cannot be signalled: exit 1 path."""
    pidfile = str(tmp_path / "watch.pid")
    marker = str(tmp_path / "marker")
    holder = _Holder(_HOLD_GARBAGE, pidfile, marker)
    try:
        result = stop_watcher(pidfile, timeout=1.0)
        assert result["state"] == "unresolvable"
        assert _pid_alive(holder.proc.pid), "holder should not have been killed"
        assert not pidfile_is_free(pidfile)
    finally:
        holder.cleanup()


def test_unwatch_unreadable_pid_exits_one(core, project, monkeypatch, tmp_path):
    """The CLI surfaces the unresolvable case with a clear error and exit 1."""
    core.registry.add(str(project))
    _patch_core(monkeypatch, core)
    # Point the index root at tmp_path so the pidfile unwatch looks for is the
    # one this holder took.
    core.cfg.index_root = str(tmp_path)
    pidfile = str(tmp_path / "watch.pid")
    marker = str(tmp_path / "marker")
    holder = _Holder(_HOLD_GARBAGE, pidfile, marker)
    try:
        result = runner.invoke(app, ["unwatch", "--all"])
        assert result.exit_code == 1, result.output
        assert "unreadable" in result.output
    finally:
        holder.cleanup()


# ---------------------------------------------------------------------------
# End to end: a real watcher subprocess is stopped by `unwatch`
# ---------------------------------------------------------------------------

def test_unwatch_stops_foreground_watcher(core, project, monkeypatch):
    """A foreground watcher takes the same pidfile and is stopped by unwatch."""
    core.registry.add(str(project))
    _patch_core(monkeypatch, core)
    pidfile = os.path.join(core.cfg.index_root, "watch.pid")
    proc = subprocess.Popen(
        [sys.executable, "-c", _BOOT, "watch", str(project),
         "--duration", "60"],
        env=_boot_env(core), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True)
    try:
        assert _wait_for(lambda: read_pid(pidfile) is not None), \
            "foreground watcher never wrote its pidfile"
        assert read_pid(pidfile) == proc.pid, "pidfile is not the foreground pid"

        result = runner.invoke(app, ["unwatch", str(project)])
        assert result.exit_code == 0, result.output
        assert "stopped watcher" in result.output
        assert f"pid={proc.pid}" in result.output
        out, _ = proc.communicate(timeout=25)
        assert proc.returncode == 0, out
        assert "watch: exiting" in out
        assert not os.path.exists(pidfile)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)


def test_unwatch_stops_background_watcher(core, project, monkeypatch):
    """A --background daemon is stopped by unwatch (pid from the pidfile)."""
    core.registry.add(str(project))
    _patch_core(monkeypatch, core)
    pidfile = os.path.join(core.cfg.index_root, "watch.pid")
    started = subprocess.run(
        [sys.executable, "-c", _BOOT, "watch", str(project), "--background",
         "--duration", "60"],
        env=_boot_env(core), capture_output=True, text=True, timeout=40)
    assert started.returncode == 0, started.stdout + started.stderr
    assert "watch daemon started" in started.stdout + started.stderr
    assert _wait_for(lambda: read_pid(pidfile) is not None)
    daemon_pid = read_pid(pidfile)
    assert daemon_pid is not None
    try:
        result = runner.invoke(app, ["unwatch", "--all"])
        assert result.exit_code == 0, result.output
        assert "stopped watcher" in result.output
        assert f"pid={daemon_pid}" in result.output
        assert _wait_for(lambda: not os.path.exists(pidfile)), \
            "daemon did not release its pidfile"
        assert pidfile_is_free(pidfile) is True
        assert _wait_for(lambda: not _pid_alive(daemon_pid), timeout=10.0), \
            "daemon process still alive after unwatch"
    finally:
        if _pid_alive(daemon_pid):
            try:
                os.kill(daemon_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass