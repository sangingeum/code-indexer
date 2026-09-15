"""Regression: read-only inotify events must not trigger the debounced re-index."""
import time
from watchdog.events import FileOpenedEvent, FileClosedNoWriteEvent, FileModifiedEvent

from mcp_code_indexer.watcher import DebouncingHandler


def test_readonly_events_do_not_fire_reindex():
    fired = []
    h = DebouncingHandler(debounce_seconds=0.05, fire=lambda slug, path: fired.append(slug))
    h.set_roots({"/repo": ("slug1", "/repo")})

    h.on_any_event(FileOpenedEvent("/repo/pyproject.toml"))
    h.on_any_event(FileClosedNoWriteEvent("/repo/pyproject.toml"))
    time.sleep(0.2)
    assert fired == [], f"read-only events fired re-index: {fired}"


def test_real_modification_still_fires():
    fired = []
    h = DebouncingHandler(debounce_seconds=0.05, fire=lambda slug, path: fired.append(slug))
    h.set_roots({"/repo": ("slug1", "/repo")})

    h.on_any_event(FileModifiedEvent("/repo/src/main.py"))
    time.sleep(0.2)
    assert fired == ["slug1"], f"modification did not fire: {fired}"
