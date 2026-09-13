"""Watcher unit tests: debounce, ignore filtering, lifecycle. No real inotify."""

import threading
import time

from mcp_code_indexer.scanner import SKIP_DIRS
from mcp_code_indexer.watcher import (
    DebouncingHandler,
    FileWatcher,
    is_ignored_path,
)


def test_is_ignored_path_covers_churn_dirs():
    assert is_ignored_path(".git/objects/ab/cdef")
    assert is_ignored_path("node_modules/dep.js")
    assert is_ignored_path(".venv/lib/python3.11/site.py")
    assert not is_ignored_path("src/mcp_code_indexer/server.py")
    # Every scanner skip-list dir is filtered.
    for d in SKIP_DIRS:
        assert is_ignored_path(f"{d}/anything.txt")


def test_debounce_coalesces_events(monkeypatch):
    """Burst of events on one project -> exactly one fire after quiet period."""
    fired: list[tuple[str, str]] = []
    done = threading.Event()

    def fire(slug: str, path: str) -> None:
        fired.append((slug, path))
        done.set()

    h = DebouncingHandler(0.2, fire)
    h.set_roots({"/tmp/proj": ("slug1", "/tmp/proj")})
    for i in range(10):
        h.on_any_event(type("E", (), {"src_path": f"/tmp/proj/src/f{i}.py",
                                      "dest_path": None})())
        time.sleep(0.02)  # each event restarts the quiet period
    assert not fired  # nothing fired while events keep arriving
    assert done.wait(timeout=2.0)
    assert fired == [("slug1", "/tmp/proj")]


def test_debounce_ignores_git_churn():
    fired: list[tuple[str, str]] = []
    h = DebouncingHandler(0.1, lambda s, p: fired.append((s, p)))
    h.set_roots({"/tmp/proj": ("slug1", "/tmp/proj")})
    for p in ("/tmp/proj/.git/index", "/tmp/proj/.git/objects/ab/cd",
              "/tmp/proj/node_modules/x.js", "/tmp/proj/.venv/bin/python"):
        h.on_any_event(type("E", (), {"src_path": p, "dest_path": None})())
    time.sleep(0.4)
    assert fired == []


def test_debounce_two_projects_fire_separately():
    fired: list[tuple[str, str]] = []
    h = DebouncingHandler(0.1, lambda s, p: fired.append((s, p)))
    h.set_roots({"/tmp/a": ("slug_a", "/tmp/a"),
                 "/tmp/b": ("slug_b", "/tmp/b")})
    h.on_any_event(type("E", (), {"src_path": "/tmp/a/x.py", "dest_path": None})())
    h.on_any_event(type("E", (), {"src_path": "/tmp/b/y.py", "dest_path": None})())
    time.sleep(0.5)
    assert sorted(fired) == [("slug_a", "/tmp/a"), ("slug_b", "/tmp/b")]


def test_longest_prefix_root_match():
    fired: list[tuple[str, str]] = []
    h = DebouncingHandler(0.1, lambda s, p: fired.append((s, p)))
    h.set_roots({"/tmp/ws": ("outer", "/tmp/ws"),
                 "/tmp/ws/inner": ("inner", "/tmp/ws/inner")})
    h.on_any_event(type("E", (), {"src_path": "/tmp/ws/inner/z.py",
                                  "dest_path": None})())
    time.sleep(0.4)
    assert fired == [("inner", "/tmp/ws/inner")]


def test_filewatcher_end_to_end(tmp_path):
    """Real watchdog observer: touching a file fires the runner once."""
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "a.py").write_text("x = 1\n")

    fired: list[tuple[str, str]] = []
    done = threading.Event()

    def runner(slug: str, path: str) -> None:
        fired.append((slug, path))
        done.set()

    w = FileWatcher(runner, debounce_seconds=0.3)
    w.watch("slug_e2e", str(proj))
    w.start()
    try:
        time.sleep(0.3)  # let inotify settle
        (proj / "src" / "a.py").write_text("x = 2\n")
        (proj / "src" / "b.py").write_text("y = 2\n")
        (proj / ".git").mkdir(exist_ok=True)
        (proj / ".git" / "index").write_text("churn\n")
        assert done.wait(timeout=5.0), "watcher never fired"
        assert fired and fired[0][0] == "slug_e2e"
        # .git churn must not schedule a second fire
        time.sleep(0.8)
        assert len(fired) == 1
    finally:
        w.stop()


def test_unwatch_cancels_pending():
    fired: list[tuple[str, str]] = []
    w = FileWatcher(lambda s, p: fired.append((s, p)), debounce_seconds=0.2)
    w.watch("slug_u", "/tmp/whatever")
    # simulate an event then unwatch before the quiet period ends
    w._handler.on_any_event(
        type("E", (), {"src_path": "/tmp/whatever/a.py", "dest_path": None})())
    w.unwatch("slug_u")
    time.sleep(0.5)
    assert fired == []
