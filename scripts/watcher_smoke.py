#!/usr/bin/env python3
"""Manual verification: watcher-triggered re-index on file touch.

Runs the real FileWatcher against a scratch project with a stub runner that
logs the "index pass", simulating what server._watch_runner does. Verifies:
  1. touching a watched file triggers a debounced re-index pass
  2. .git churn alone does NOT trigger one
  3. unwatch stops further passes

Usage: uv run python scripts/watcher_smoke.py
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from pathlib import Path

from mcp_code_indexer.watcher import FileWatcher

PASSES: list[tuple[str, str]] = []
DONE = threading.Event()


def runner(slug: str, path: str) -> None:
    # In the server this is _run_index(slug, path, force=False).
    print(f"[pass] slug={slug} path={path} at {time.strftime('%H:%M:%S')}")
    PASSES.append((slug, path))
    DONE.set()


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="watcher-smoke-"))
    proj = tmp / "vivarium-sim"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "sim.py").write_text("step = 0\n")

    w = FileWatcher(runner, debounce_seconds=2.0)
    w.watch("vivarium_sim", str(proj))
    w.start()
    try:
        # 1. touch a source file -> one pass after the 2s quiet period.
        t0 = time.monotonic()
        (proj / "src" / "sim.py").write_text("step = 1\n")
        assert DONE.wait(timeout=10), "no re-index pass after file touch"
        dt = time.monotonic() - t0
        print(f"PASS 1: touch -> index pass fired after {dt:.1f}s (debounce 2s)")
        assert len(PASSES) == 1

        # 2. .git churn must not trigger anything.
        (proj / ".git").mkdir()
        (proj / ".git" / "index").write_text("churn\n" * 100)
        PASSES.clear()
        DONE.clear()
        time.sleep(4)  # > debounce window
        assert not PASSES, ".git churn wrongly triggered a pass"
        print("PASS 2: .git churn ignored")

        # 3. unwatch stops passes.
        w.unwatch("vivarium_sim")
        (proj / "src" / "sim.py").write_text("step = 2\n")
        time.sleep(4)
        assert not PASSES, "pass fired after unwatch"
        print("PASS 3: unwatch stops passes")
        print("ALL OK")
    finally:
        w.stop()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
