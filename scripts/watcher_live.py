#!/usr/bin/env python3
"""Live end-to-end check: real watcher + real incremental index pass on a
registered project (vivarium-sim), touching a real file.

Run with the same env the server uses: uv run python scripts/watcher_live.py
Verifies the watch fires _run_index (real Ollama/Qdrant pass) within the
stale window. Read-only with respect to the repo: the touched file is a
scratch file added then removed.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp_code_indexer.server import _run_index, REGISTRY, WATCHER, CFG  # noqa: E402,F401

PROJECT = "/home/keum/dev/grace/vivarium"
SCRATCH = os.path.join(PROJECT, "WATCHER_LIVE_SCRATCH.md")


def main() -> None:
    entry = REGISTRY.get_by_path(PROJECT)
    if entry is None:
        print(f"FATAL: {PROJECT} not registered")
        sys.exit(1)
    print(f"registered: slug={entry.slug}")

    # Baseline: last_indexed from manifest.
    from mcp_code_indexer.manifest import Manifest
    mdir = os.path.join(CFG.index_root, entry.slug, "manifest.db")
    m = Manifest(mdir)
    before = m.get_meta("last_indexed")
    m.close()
    print(f"last_indexed before: {before}")

    WATCHER.watch(entry.slug, entry.path)
    WATCHER.start()
    time.sleep(0.5)

    # Touch a scratch file inside the watched root.
    with open(SCRATCH, "w", encoding="utf-8") as f:
        f.write("# watcher live check\nGenerated for watcher verification.\n")
    t0 = time.monotonic()

    # Wait up to 90s for a watcher-triggered pass to update last_indexed.
    deadline = time.monotonic() + 90
    after = before
    while time.monotonic() < deadline:
        time.sleep(2)
        m = Manifest(mdir)
        after = m.get_meta("last_indexed")
        m.close()
        if after != before:
            break
    dt = time.monotonic() - t0
    os.remove(SCRATCH)

    if after != before:
        print(f"OK: watcher-triggered re-index completed {dt:.1f}s after touch")
        print(f"last_indexed: {before} -> {after}")
        # Give a second pass a chance to pick up the scratch-file deletion too.
        time.sleep(CFG.watch_debounce + 15)
    else:
        print(f"FAIL: no re-index within {dt:.1f}s")
        sys.exit(1)
    WATCHER.stop()


if __name__ == "__main__":
    main()
