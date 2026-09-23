"""Event-based watcher tests (watchdog inotify + background daemon).

No network: embedder/store are stubbed at the Core seam. Inotify tests need
a real filesystem event — /tmp is fine (tmpfs or ext4 both support
inotify). Subprocess tests are skipped if the platform can't fork/signal.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time

import pytest
from typer.testing import CliRunner

pytest.importorskip(
    "watchdog.observers", reason="watchdog not installed (uv sync --group watch)")

from code_indexer.cli import app
from code_indexer.config import Config
from code_indexer.core import Core
from code_indexer.watcher import PidFileLock, WatchEngine, read_pid

runner = CliRunner()


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
    c.embedder = StubEmbedder()
    c.store = StubStore()
    c.indexer.embedder = c.embedder
    c.indexer.store = c.store
    return c


@pytest.fixture()
def core(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("WATCH_DEBOUNCE", "1")  # fast quiet period
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


def _patch_core(monkeypatch, core):
    import code_indexer.cli as cli
    monkeypatch.setattr(cli, "_core", core)
    monkeypatch.setattr(cli, "_core_skip", False)


def _make_engine(core, project) -> WatchEngine:
    return WatchEngine(
        core, [(core.registry.get_by_path(str(project)).slug, str(project))],
        quiet=1, sweep_interval=3600, index_root=core.cfg.index_root,
        echo=lambda msg: print(msg))


def test_event_triggers_pass(core, project, monkeypatch):
    """Write a file while the observer runs -> after the quiet period an
    incremental pass runs and the file lands in the manifest."""
    entry = core.registry.add(str(project))
    engine = _make_engine(core, project)
    assert engine.start_events() is True
    observer = engine._observer
    stop = {"flag": False}
    th = None
    try:
        time.sleep(0.3)  # let the emitter settle
        (project / "new.py").write_text("def new():\n    pass\n")
        th = threading.Thread(target=engine.run, args=(None, stop), daemon=True)
        th.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            m = core.manifest_for(entry.slug)
            try:
                if "new.py" in m.all_files():
                    break
            finally:
                m.close()
            time.sleep(0.2)
        else:
            pytest.fail("event-triggered pass never indexed new.py")
    finally:
        stop["flag"] = True
        if th:
            th.join(timeout=5)
        observer.stop()


def test_debounce_coalesces_burst(core, project):
    """A burst of N writes inside the quiet period coalesces into at most
    one pass (dirty-set holds one entry; the timestamp resets per event)."""
    entry = core.registry.add(str(project))
    engine = _make_engine(core, project)
    engine.start_events()
    observer = engine._observer
    try:
        time.sleep(0.3)
        for i in range(5):
            (project / f"f{i}.py").write_text(f"def f{i}():\n    pass\n")
            time.sleep(0.1)  # all 5 land inside the 1 s quiet period
        time.sleep(0.2)
        # Everything coalesced: one dirty entry, and no pass has run yet.
        assert set(engine.dirty) == {entry.slug}
        m = core.manifest_for(entry.slug)
        try:
            assert "f0.py" not in m.all_files()
        finally:
            m.close()
        # After the quiet period elapses, exactly one pass fires and clears it.
        stop = {"flag": False}
        th = threading.Thread(target=engine.run, args=(None, stop), daemon=True)
        th.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and engine.dirty:
            time.sleep(0.1)
        assert not engine.dirty, "coalesced burst never fired"
    finally:
        stop["flag"] = True
        th.join(timeout=5)
        observer.stop()


def test_events_under_index_root_and_git_are_filtered(core, project, tmp_path):
    """Self-writes (index root) and .git paths never dirty a project."""
    entry = core.registry.add(str(project))
    engine = _make_engine(core, project)
    engine.start_events()
    observer = engine._observer
    try:
        time.sleep(0.3)
        git = project / ".git"
        git.mkdir()
        (git / "config").write_text("x")
        (project / "editor.py~").write_text("x")
        (project / "notes.swp").write_text("x")
        with open(core.cfg.index_root + "/stray.bin", "w") as stray:
            stray.write("x")  # our writes
        time.sleep(1.0)
        assert engine.dirty == {}, f"unexpected dirty: {engine.dirty}"
    finally:
        observer.stop()


def test_moved_directory_events_dirty_project(core, project):
    """Deleting a watched dir wholesale still dirties the project."""
    entry = core.registry.add(str(project))
    subdir = project / "pkg"
    subdir.mkdir()
    (subdir / "mod.py").write_text("def mod():\n    pass\n")
    core.run_index(entry.slug, str(project))  # get mod.py into the manifest
    engine = _make_engine(core, project)
    engine.start_events()
    observer = engine._observer
    stop = {"flag": False}
    th = None
    try:
        time.sleep(0.3)
        import shutil
        shutil.rmtree(subdir)  # moved/deleted dir
        th = threading.Thread(target=engine.run, args=(None, stop), daemon=True)
        th.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            m = core.manifest_for(entry.slug)
            try:
                if "pkg/mod.py" not in m.all_files():
                    break
            finally:
                m.close()
            time.sleep(0.2)
        else:
            pytest.fail("deleted-dir event never purged pkg/mod.py")
    finally:
        stop["flag"] = True
        if th:
            th.join(timeout=5)
        observer.stop()


def test_background_daemonizes_and_writes_pidfile(core, project, monkeypatch, tmp_path):
    """--background double-forks; the parent returns after the daemon's
    handshake; the pidfile holds the daemon pid and is flock-held."""
    entry = core.registry.add(str(project))
    _patch_core(monkeypatch, core)
    pidfile = tmp_path / "watch.pid"
    log = tmp_path / "watch.log"
    import code_indexer.cli as cli

    orig = cli._watch_background

    def redirected(core_arg, targets, duration, pidfile_path, log_path):
        orig(core_arg, targets, duration, str(pidfile), str(log))

    monkeypatch.setattr(
        cli, "_watch_background",
        lambda c, t, d, p, l: redirected(c, t, d, p, l))
    # Keep the daemon short-lived: duration bounds its life.
    result = runner.invoke(
        app, ["watch", str(project), "--background", "--duration", "3"])
    # NOTE: the daemon's Core() re-loads config from env; INDEX_ROOT is set
    # by the fixture env so the daemon shares the same state root.
    assert result.exit_code == 0, result.output
    assert "watch daemon started" in result.output
    daemon_pid = read_pid(str(pidfile))
    assert daemon_pid, f"no pid in {pidfile}"
    # The pidfile is flock-held while the daemon lives.
    lock = PidFileLock(str(pidfile))
    try:
        assert lock.acquire() is False, "pidfile not locked by live daemon"
    finally:
        lock.release(unlink=False)
    # Wait out --duration 3 and verify clean exit: pidfile unlinked.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not os.path.exists(pidfile):
            break
        time.sleep(0.3)
    else:
        pytest.fail("daemon did not release/remove its pidfile after --duration")
    # Log file written.
    assert log.exists() and "watch daemon" in log.read_text()


def test_pidfile_refuses_second_watcher(tmp_path):
    """A live flock on the pidfile makes a second watcher refuse to start."""
    pidfile = str(tmp_path / "watch.pid")
    holder = PidFileLock(pidfile)
    assert holder.acquire() is True
    second = PidFileLock(pidfile)
    try:
        assert second.acquire() is False
    finally:
        holder.release(unlink=True)
        second.release(unlink=False)
    assert not os.path.exists(pidfile), "released pidfile left residue"


def test_pidfile_read_pid_garbage(tmp_path):
    pidfile = tmp_path / "watch.pid"
    pidfile.write_text("not-a-pid\n")
    assert read_pid(str(pidfile)) is None
    assert read_pid(str(tmp_path / "missing.pid")) is None


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="no SIGTERM")
def test_background_sigterm_cleans_pidfile_and_lock(core, project):
    """SIGTERM to a --background watcher exits 0, removes the pidfile, and
    leaves no live flock on it."""
    entry = core.registry.add(str(project))
    env = dict(os.environ)
    env["INDEX_ROOT"] = core.cfg.index_root
    env["PYTHONPATH"] = "src"
    env["WATCH_DEBOUNCE"] = "1"
    pidfile = os.path.join(core.cfg.index_root, "watch.pid")
    log = os.path.join(core.cfg.index_root, "watch.log")
    boot = (
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
    proc = subprocess.Popen(
        [sys.executable, "-c", boot, "watch", str(project), "--background",
         "--duration", "30"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        out, _ = proc.communicate(timeout=30)
        assert proc.returncode == 0, out
        assert "watch daemon started" in out
        deadline = time.monotonic() + 15
        daemon_pid = None
        while time.monotonic() < deadline:
            daemon_pid = read_pid(pidfile)
            if daemon_pid:
                break
            time.sleep(0.2)
        assert daemon_pid, "daemon never wrote its pidfile"
        assert os.path.exists(log), "daemon log missing"
        os.kill(daemon_pid, signal.SIGTERM)
        # Clean exit: pidfile removed, flock free.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and os.path.exists(pidfile):
            time.sleep(0.2)
        assert not os.path.exists(pidfile), "pidfile residue after SIGTERM"
        lock = PidFileLock(pidfile)  # recreates; lock must succeed
        try:
            assert lock.acquire() is True, "flock still held after SIGTERM"
        finally:
            lock.release(unlink=True)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)


def test_watch_background_honors_duration_exit_zero(core, project, monkeypatch):
    """--background --duration T: parent exits 0, daemon exits 0 after T."""
    entry = core.registry.add(str(project))
    _patch_core(monkeypatch, core)
    result = runner.invoke(
        app, ["watch", str(project), "--background", "--duration", "2"])
    assert result.exit_code == 0, result.output
    assert "watch daemon started" in result.output
    pidfile = os.path.join(core.cfg.index_root, "watch.pid")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and os.path.exists(pidfile):
        time.sleep(0.3)
    assert not os.path.exists(pidfile), "daemon did not exit after --duration"
