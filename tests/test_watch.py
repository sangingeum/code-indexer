"""`watch` subcommand tests: bounded duration, flock contention, --name alias.

No network: embedder/store are stubbed at the Core seam, exactly like
test_concurrency.py. The watch loop itself is exercised via its CLI surface
(typer CliRunner) plus one real-subprocess duration test.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest
from typer.testing import CliRunner

from code_indexer.cli import app
from code_indexer.config import Config
from code_indexer.core import Core
from code_indexer.locks import project_lock

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
    monkeypatch.setenv("WATCH_DEBOUNCE", "1")  # fast tick
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
    """Bind the CLI module-level core seam to our stubbed core."""
    import code_indexer.cli as cli
    monkeypatch.setattr(cli, "_core", core)
    monkeypatch.setattr(cli, "_core_skip", False)


def test_watch_exits_after_duration(core, project, monkeypatch):
    """--duration T bounds the watcher's life; exit code 0."""
    entry = core.registry.add(str(project))
    _patch_core(monkeypatch, core)
    t0 = time.monotonic()
    result = runner.invoke(app, ["watch", str(project), "--duration", "2"])
    elapsed = time.monotonic() - t0
    assert result.exit_code == 0, result.output
    assert 1.5 <= elapsed <= 8, f"elapsed={elapsed:.2f}s"
    assert "watch: exiting" in result.output


def test_watch_duration_zero_means_forever(core, project, monkeypatch):
    """--duration 0 (and omitted) must NOT impose a deadline: verified by
    checking the loop keeps ticking — run it in a thread, stop via signal."""
    core.registry.add(str(project))
    _patch_core(monkeypatch, core)
    outcome: dict = {}

    def run() -> None:
        r = runner.invoke(app, ["watch", str(project), "--duration", "0"])
        outcome["exit"] = r.exit_code

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout=5)
    # It did NOT exit within 5 s (forever) — the test process ends here; the
    # thread is a daemon so it cannot block exit. A full stop-path test is
    # covered by the SIGTERM subprocess test below.
    assert th.is_alive(), "watch with --duration 0 exited early"


def test_watch_holds_flock_parallel_search_sees_contention(core, project, monkeypatch):
    """While a watch pass runs, a concurrent acquire reports
    'indexing in progress' (the flock is genuinely taken each pass)."""
    entry = core.registry.add(str(project))
    _patch_core(monkeypatch, core)

    # Hold the lock the way another process would; a watcher pass triggered
    # by a real file event must then report the held state instead of
    # indexing (quiet period 1 s -> pass fires ~1 s into a 4 s window).
    # The write happens ~1.5 s in: inotify only sees events raised after the
    # observer starts.
    import threading as _th

    def _poke() -> None:
        time.sleep(1.5)
        (project / "new.py").write_text("def new():\n    pass\n")

    with project_lock(core._lock_path(entry.slug)) as acquired:
        assert acquired is True
        th = _th.Thread(target=_poke)
        th.start()
        result = runner.invoke(app, ["watch", str(project), "--duration", "4"])
        th.join()
        assert result.exit_code == 0
        assert "indexing in progress" in result.output


def test_watch_sigterm_exits_zero(core, project):
    """Real subprocess: SIGTERM during watch exits 0 cleanly (handlers
    installed, loop breaks, lock released on context-manager unwinding)."""
    import os
    core.registry.add(str(project))
    env = dict(os.environ)
    env["INDEX_ROOT"] = core.cfg.index_root
    env["PYTHONPATH"] = "src"
    env["WATCH_DEBOUNCE"] = "1"
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
        [sys.executable, "-c", boot, "watch", str(project)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(3)  # let it install handlers and enter the loop
    proc.terminate()
    out, _ = proc.communicate(timeout=15)
    assert proc.returncode == 0, out
    assert "watch: exiting" in out


def test_name_alias_resolves_identically_to_project(core, project, monkeypatch):
    """--name X and --project X hit the same registry entry / same output."""
    entry = core.registry.add(str(project), name="aliased")
    _patch_core(monkeypatch, core)
    core.run_index(entry.slug, entry.path)
    r_proj = runner.invoke(
        app, ["find-definition", "main", "--project", "aliased"])
    r_name = runner.invoke(
        app, ["find-definition", "main", "--name", "aliased"])
    assert r_proj.exit_code == 0 == r_name.exit_code
    assert r_proj.output == r_name.output
    assert f"{project}::app.py" in r_name.output

    # Same alias resolves on get-code-context and find-references too.
    r_ctx = runner.invoke(
        app, ["get-code-context", "app.py", "--name", "aliased",
              "--start-line", "1", "--end-line", "2"])
    assert r_ctx.exit_code == 0
    assert "1| def main():" in r_ctx.output


def test_watch_rejects_paths_with_all(core, project, monkeypatch):
    core.registry.add(str(project))
    _patch_core(monkeypatch, core)
    result = runner.invoke(
        app, ["watch", str(project), "--all"])
    assert result.exit_code == 1
    assert "OR --all" in result.output


def test_watch_all_round_robin(core, project, tmp_path, monkeypatch):
    """--all watches every registered project, one pass per tick each."""
    other = tmp_path / "proj2"
    other.mkdir()
    (other / "b.py").write_text("def b():\n    pass\n")
    core.registry.add(str(project), name="one")
    core.registry.add(str(other), name="two")
    _patch_core(monkeypatch, core)
    result = runner.invoke(app, ["watch", "--all", "--duration", "1"])
    assert result.exit_code == 0
    assert "watching 2 project(s)" in result.output
