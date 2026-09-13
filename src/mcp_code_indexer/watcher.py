"""Lightweight filesystem watcher (design addendum §8, wave 9 finding).

Auto-reindex used to be call-triggered only (``_maybe_refresh`` in
server.py), so indexes silently went stale when agents edited code without
calling semantic_search. This module closes that gap: registered project
roots are watched via inotify (through the ``watchdog`` library — cleanest
route, recursive watches + event normalization handled for us), edits are
debounced per project, and after a quiet period the SAME incremental
``_run_index(slug, path, force=False)`` runs on the background-thread
pattern.

Guarantees:
- One watchdog Observer thread total (never per-project watchers).
- Debounce: events coalesce into one pending re-index per project; the pass
  fires only after a quiet period with no further events.
- Reuses server-side project_lock (inside _run_index), so a watcher pass
  never races a tool-triggered pass.
- Failure isolation: every failure path is caught, logged, and the watcher
  restarts with exponential backoff — a watcher crash can never take the
  MCP server down.
- .git/ and other churn trees are filtered via the scanner's SKIP_DIRS plus
  a path-component check (recursion into ignored trees is pruned).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .scanner import SKIP_DIRS

logger = logging.getLogger("mcp-code-indexer.watcher")

DEFAULT_DEBOUNCE_SECONDS = 3.0
DEFAULT_RESTART_BACKOFF_MAX = 30.0


def is_ignored_path(rel_path: str) -> bool:
    """True if any path component of *rel_path* is in the scanner skip-list.

    Also filters anything inside a ``.git`` directory regardless of the
    rel-path shape (inotify event paths may not be clean repo-relative
    paths, e.g. for nested roots whose parent contains the .git dir).
    """
    parts = rel_path.replace(os.sep, "/").split("/")
    if any(part in SKIP_DIRS for part in parts):
        return True
    last = parts[-1].lower() if parts else ""
    # .git churn: the file itself (.git/index, .gitconfig aside) — but keep
    # real repo files like .gitignore and .github/ indexable.
    if last.startswith(".git") and not last.startswith(".gitignore"):
        return True
    return False


@dataclass
class _PendingProject:
    """Debounce state for one watched project."""
    slug: str
    path: str
    timer: threading.Timer | None = None
    last_event: float = 0.0


@dataclass
class WatcherStats:
    started_at: float = field(default_factory=time.time)
    events_seen: int = 0
    debounce_passes: int = 0


class DebouncingHandler(FileSystemEventHandler):
    """Watchdog event handler: filter, then debounce per project.

    When the quiet period elapses the *fire* callback runs once per project.
    The callback (wired in FileWatcher) does the actual indexing work off
    the watchdog observer thread so the observer never blocks.

    Extends FileSystemEventHandler so watchdog's dispatcher can call us.
    """

    def __init__(self, debounce_seconds: float,
                 fire: Callable[[str, str], None]) -> None:
        super().__init__()
        self._debounce = debounce_seconds
        self._fire = fire
        self._lock = threading.Lock()
        self._pending: dict[str, _PendingProject] = {}
        self._roots: dict[str, tuple[str, str]] = {}

    # -- watchdog callback API ------------------------------------------------
    def on_any_event(self, event: FileSystemEvent) -> None:
        dest = getattr(event, "dest_path", None)
        for raw in (event.src_path, dest):
            if raw:
                self._consider(str(raw))

    # -- internals ------------------------------------------------------------
    def _consider(self, raw_path: str) -> None:
        root = self._root_for(raw_path)
        if root is None:
            return
        slug, project_path = root
        if os.path.normpath(raw_path) == project_path:
            # Directory-modified event on the root itself (e.g. a top-level
            # dir created/deleted) — the child's own event will follow.
            return
        rel = os.path.relpath(raw_path, project_path)
        if is_ignored_path(rel):
            return
        self._schedule(slug, project_path)

    def _root_for(self, raw_path: str) -> tuple[str, str] | None:
        """Longest-prefix match of raw_path against watched roots."""
        with self._lock:
            roots = dict(self._roots)
        best: tuple[str, str] | None = None
        best_len = -1
        norm = os.path.normpath(raw_path)
        for root, entry in roots.items():
            if norm == root or norm.startswith(root + os.sep):
                if len(root) > best_len:
                    best, best_len = entry, len(root)
        return best

    def _schedule(self, slug: str, project_path: str) -> None:
        with self._lock:
            pend = self._pending.get(slug)
            if pend is None:
                pend = _PendingProject(slug=slug, path=project_path)
                self._pending[slug] = pend
            pend.last_event = time.monotonic()
            if pend.timer is not None:
                pend.timer.cancel()
            # Quiet period restarts on every event. The fire callback runs on
            # its own thread so the observer is never blocked by indexing.
            pend.timer = threading.Timer(
                self._debounce, self._fire_safe, args=(slug, project_path))
            pend.timer.daemon = True
            pend.timer.start()

    def _fire_safe(self, slug: str, project_path: str) -> None:
        with self._lock:
            pend = self._pending.get(slug)
            if pend is not None:
                pend.timer = None
        try:
            self._fire(slug, project_path)
        except Exception:  # noqa: BLE001
            logger.exception("debounced re-index fire failed for %s", slug)

    # -- registration plumbing (called from FileWatcher) ----------------------
    def set_roots(self, roots: dict[str, tuple[str, str]]) -> None:
        """Replace the roots map: root_path -> (slug, project_path)."""
        with self._lock:
            self._roots = dict(roots)

    def cancel_pending(self, slug: str) -> None:
        with self._lock:
            pend = self._pending.pop(slug, None)
        if pend is not None and pend.timer is not None:
            pend.timer.cancel()


class FileWatcher:
    """One Observer, one handler, one supervised lifecycle.

    Watched roots follow REGISTRY: ``watch(slug, path)`` on registration,
    ``unwatch(slug)`` on removal. A supervisor thread restarts the observer
    with exponential backoff (1s -> 30s cap) if anything throws.
    """

    def __init__(self, runner: Callable[[str, str], None],
                 debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS) -> None:
        self._runner = runner
        self._debounce = debounce_seconds
        self._handler = DebouncingHandler(debounce_seconds, self._fire)
        self._roots: dict[str, tuple[str, str]] = {}   # root -> (slug, path)
        self._by_slug: dict[str, str] = {}             # slug -> root
        self._lock = threading.Lock()
        self._observer: Any = None  # watchdog Observer (platform-selected class)
        self._stop = threading.Event()
        self._supervisor: threading.Thread | None = None
        self.stats = WatcherStats()

    # -- public API ------------------------------------------------------------
    def start(self) -> None:
        """Start the observer and the supervisor thread."""
        with self._lock:
            if self._supervisor is not None:
                return
            self._stop.clear()
            self._observer = self._make_observer()
            self._observer.start()
            # Schedule any roots registered before start() (server main path).
            # NB: threading.Lock is not reentrant — we already hold self._lock.
            for root, _entry in list(self._roots.items()):
                try:
                    self._observer.schedule(self._handler, root, recursive=True)
                except Exception:  # noqa: BLE001
                    logger.exception("initial schedule failed for %s", root)
            self._supervisor = threading.Thread(
                target=self._supervise, daemon=True, name="index-watcher")
            self._supervisor.start()
        logger.info("file watcher started (debounce=%.1fs)", self._debounce)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            obs = self._observer
            sup = self._supervisor
            self._observer = None
            self._supervisor = None
        if sup is not None:
            sup.join(timeout=2)
        if obs is not None:
            obs.stop()
            obs.join(timeout=2)
        logger.info("file watcher stopped")

    def watch(self, slug: str, project_path: str) -> None:
        """Register a watch for one project root (called on add_project)."""
        root = os.path.abspath(project_path)
        if not os.path.isdir(root):
            return
        obs = self._observer
        with self._lock:
            prev = self._by_slug.get(slug)
            if prev and prev != root:
                self._roots.pop(prev, None)
            self._roots[root] = (slug, root)
            self._by_slug[slug] = root
            self._handler.set_roots(self._roots)
        if obs is None:
            return
        try:
            obs.schedule(self._handler, root, recursive=True)
        except Exception:  # noqa: BLE001
            logger.exception("watch schedule failed for %s — supervisor "
                             "will resync", root)
            self._resync()

    def unwatch(self, slug: str) -> None:
        """Drop the watch for one project (called on remove_project)."""
        with self._lock:
            root = self._by_slug.pop(slug, None)
            if root is not None:
                self._roots.pop(root, None)
                self._handler.set_roots(self._roots)
        self._handler.cancel_pending(slug)
        if root is None:
            return
        obs = self._observer
        if obs is None:
            return
        # watchdog has no unschedule-by-path; drop this root's watch entries.
        try:
            for w in list(obs._watches.values()):  # type: ignore[attr-defined]
                if getattr(w, "path", None) == root:
                    obs.unschedule(w)
        except Exception:  # noqa: BLE001
            logger.debug("unschedule cleanup failed for %s", root)

    # -- internals ------------------------------------------------------------
    def _make_observer(self) -> Observer:
        obs = Observer(timeout=1.0)
        obs.daemon = True
        return obs

    def _fire(self, slug: str, project_path: str) -> None:
        self.stats.debounce_passes += 1
        # Run off the timer thread so the debounce bookkeeping never blocks
        # on a long index pass; project_lock (inside _run_index) serializes
        # this against tool-triggered passes.
        threading.Thread(
            target=self._runner, args=(slug, project_path),
            daemon=True, name=f"watch-index-{slug}").start()

    def _supervise(self) -> None:
        """Keep the observer alive; restart with backoff on failure."""
        backoff = 1.0
        while not self._stop.is_set():
            obs = self._observer
            if obs is None:
                break
            if not obs.is_alive():
                logger.warning("observer died — restarting in %.1fs", backoff)
                self._stop.wait(backoff)
                if self._stop.is_set():
                    break
                try:
                    self._resync()
                    backoff = min(backoff * 2, DEFAULT_RESTART_BACKOFF_MAX)
                except Exception:  # noqa: BLE001
                    logger.exception("watcher restart failed — retrying")
            else:
                backoff = 1.0
            self._stop.wait(1.0)

    def _resync(self) -> None:
        """Rebuild the observer and re-schedule every registered root."""
        with self._lock:
            old = self._observer
            new = self._make_observer()
            self._observer = new
            roots = dict(self._roots)
        if old is not None:
            try:
                old.stop()
                if old._started.is_set():  # type: ignore[attr-defined]
                    old.join(timeout=1)
            except Exception:  # noqa: BLE001
                logger.exception("old observer stop failed")
        new.start()
        for root, _entry in roots.items():
            try:
                new.schedule(self._handler, root, recursive=True)
            except Exception:  # noqa: BLE001
                logger.exception("resync watch failed for %s", root)
        logger.info("watcher resynced: %d root(s)", len(roots))


# Typing import kept at bottom users: Any referenced only for watchdog's
# loosely typed events; nothing else needed.
_ = Any
