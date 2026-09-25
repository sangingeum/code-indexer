"""Event-based watcher: inotify (watchdog Observer) + daemonization.

Design ruling for the ``watch`` subcommand:

- **watchdog Observer over raw inotify.** The Observer API gives recursive
  root watches with automatic watch-tree maintenance (new subdirs get
  watched as they appear; moved/deleted dirs have their watches reaped).
  Raw inotify would mean hand-rolling watch-descriptor bookkeeping for zero
  benefit in this Linux-only household. watchdog lives in the opt-in
  ``watch`` dependency-group, NOT in runtime deps: the MCP server and every
  one-shot CLI command never import it — only ``code-indexer watch`` does
  (lazy import with a fallback to quiet-interval polling).

- **Quiet period = the old poll tick.``watch_debounce`` (default 3 s) is
  reinterpreted as the *debounce/quiet period*: an event burst must stay
  quiet that long before a pass fires. Env alias ``WATCH_QUIET_PERIOD`` is
  accepted and wins over ``WATCH_DEBOUNCE``. An event burst that doesn't
  change any content costs at most one hash scan — zero embedding, zero
  Qdrant traffic (the incremental diff in the indexer guarantees it).

- **Self-heal sweep.** A full staleness pass (``run_index force=False``) for
  every watched project runs every ``WATCH_SWEEP_INTERVAL`` seconds
  (default 300 s, documented) even with zero events, healing anything
  missed — e.g. the kernel dropping events under memory pressure (watchdog
  does not surface the raw ``IN_Q_OVERFLOW`` flag; the sweep is the
  self-heal mechanism).

- **Self-write suppression.** The indexer never writes inside watched
  project roots — manifests, registry, and locks live under ``index_root``.
  Events are filtered: anything under ``index_root``, ``.git`` paths, and
  common temp/editor-suffix files (``~``, ``.swp``, ``.tmp``, …) are
  ignored. No suppression *window* is used: a real user edit made while a
  pass runs is never dropped, it simply schedules the next quiet pass.

- **Watch-descriptor exhaustion.** Scheduling the recursive root watch is
  wrapped in ``except OSError``: if inotify watch limits are hit, the
  watcher logs the condition and degrades to quiet-interval polling mode
  (hash scan per project per quiet tick), which cannot lose changes.

- **Daemonization (``--background``).** Double-fork + setsid + stdio
  redirected to ``~/.code-indexer/watch.log``. Exactly one watcher per
  index root: a flock-guarded PID file (``watch.pid``) — the second
  watcher's ``flock(LOCK_EX|LOCK_NB)`` on the pidfile fails and startup is
  refused. The lock lives on the inode, so a SIGKILLed watcher leaves no
  *live* lock; the pidfile is unlinked only after the fd is closed and the
  flock released (never while held).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
import time
from collections.abc import Callable
from typing import Any

SWEEP_INTERVAL_DEFAULT = 300

# Editor/backup/temp artifacts that never deserve an index pass.
_TEMP_SUFFIXES: tuple[str, ...] = (".tmp", ".swp", ".swx", ".orig", ".rej", ".part", "~")
_TEMP_NAMES: frozenset[str] = frozenset({"4913"})  # vim's test file


class PidFileLock:
    """Flock-guarded PID file for the watcher (one live watcher per root).

    The flock — not pid existence — is the liveness test: ``acquire`` fails
    with False when another live watcher holds the pidfile. On success the
    daemon pid + timestamp are written. ``release`` closes the fd (which
    releases the kernel flock) and only then unlinks the path, so the lock
    is never unlinked while held.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        with contextlib.suppress(OSError):
            os.ftruncate(fd, 0)
            os.write(
                fd,
                f"{os.getpid()} {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n".encode(),
            )
        self._fd = fd
        return True

    def release(self, unlink: bool = True) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        os.close(fd)  # kernel releases the flock here, before any unlink
        if unlink:
            with contextlib.suppress(OSError):
                os.unlink(self.path)


def read_pid(pidfile_path: str) -> int | None:
    """First token of the pidfile as an int, or None if absent/invalid."""
    try:
        with open(pidfile_path, encoding="ascii") as fh:
            return int(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


# Daemon-side exit code meaning "refused: another live watcher holds the
# pidfile". Distinct from generic failure so the parent can report the
# already-running case as a normal idempotent outcome instead of
# "failed to start".
BUSY_EXIT = 3


class AlreadyRunning(Exception):
    """A live watcher already holds the pidfile (parent-side, pre-check).

    Carries the holder's pid when it could be read, plus the holder's
    watched roots (absolute paths) when the holder is alive and inspectable.
    """

    def __init__(
        self,
        pidfile_path: str,
        holder_pid: int | None,
        holder_roots: list[str] | None = None,
    ) -> None:
        self.pidfile_path = pidfile_path
        self.holder_pid = holder_pid
        self.holder_roots = holder_roots
        super().__init__(
            f"watch: already running (pid={holder_pid}, pidfile={pidfile_path})")


def probe_watcher_holder(pidfile_path: str) -> tuple[bool, int | None,
                                                     list[str] | None]:
    """Best-effort probe of the watcher holding ``pidfile_path``.

    Returns ``(alive, pid, roots)``. ``alive`` is False when the pidfile is
    absent or its pid does not exist (stale residue). ``roots`` lists the
    absolute paths the holder watches, recovered from /proc when the holder
    is on this machine, else None when unknown. Parent-side heuristic only —
    the daemon-side flock remains the authoritative single-instance gate.
    """
    pid = read_pid(pidfile_path)
    if pid is None or pid <= 0:
        return (False, None, None)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return (False, pid, None)
    except PermissionError:
        pass  # exists, owned by someone else — treat as live
    return (True, pid, _holder_roots(pid))


def _holder_roots(pid: int) -> list[str] | None:
    """Watched roots of a running watcher, from /proc; None if unknown."""
    try:
        with open(f"/proc/{pid}/cmdline", encoding="utf-8", errors="replace") as fh:
            argv = [a for a in fh.read().split("\0") if a]
    except OSError:
        return None
    # Holder's cwd: relative roots (e.g. `watch .`) resolve against it, not
    # against the probing process's cwd.
    holder_cwd: str | None = None
    try:
        holder_cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        pass
    # Find the `watch` subcommand; everything after it up to a --flag
    # (e.g. --background) is a positional target path.
    try:
        idx = argv.index("watch")
    except ValueError:
        return None
    roots: list[str] = []
    for arg in argv[idx + 1:]:
        if arg.startswith("-"):
            break
        if holder_cwd and not os.path.isabs(arg):
            arg = os.path.join(holder_cwd, arg)
        roots.append(os.path.abspath(arg))
    return roots


def _redirect_stdio(log_path: str) -> None:
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    fd = os.open(log_path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
    os.dup2(fd, 0)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    if fd > 2:
        os.close(fd)
    # Rebind the Python-level streams too: a captured/replaced sys.stdout
    # (e.g. under a test runner) would otherwise swallow every print.
    sys.stdout = open(1, "w", encoding="utf-8", closefd=False)
    sys.stderr = open(2, "w", encoding="utf-8", closefd=False)
    sys.stdout.reconfigure(line_buffering=True)


def spawn_background(
    pidfile_path: str,
    log_path: str,
    setup: Callable[[], int],
    main: Callable[[], None],
) -> int:
    """Daemonize (double-fork + setsid) and run ``main`` in the daemon.

    Parent side: waits for the daemon's setup handshake and returns the
    daemon's pid (read back from the pidfile) or raises RuntimeError on
    failure. Daemon side: redirects stdio to ``log_path``, runs ``setup``
    (returns 0 == ready, nonzero == failed, message already logged), then
    runs ``main`` and exits 0. ``main`` must keep the pidfile lock for its
    whole life.
    """
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid:
        # Parent: reap the intermediate child, read the handshake.
        os.close(write_fd)
        os.waitpid(pid, 0)
        handshake = b""
        with contextlib.suppress(OSError):
            handshake = os.read(read_fd, 8)
        os.close(read_fd)
        if handshake == b"ok":
            daemon_pid = read_pid(pidfile_path)
            if daemon_pid:
                return daemon_pid
        # Non-ok handshake: distinguish "a live watcher already holds the
        # pidfile" (including a lock lost between the parent-side check and
        # the daemon's flock acquire) from a genuine startup failure. The
        # flock itself is the truth — probe it via the pidfile holder.
        alive, holder_pid, _roots = probe_watcher_holder(pidfile_path)
        if alive:
            raise AlreadyRunning(pidfile_path, holder_pid)
        raise RuntimeError(
            f"watch --background failed to start; see {log_path}")
    # First child: new session, fork again so the daemon can never
    # re-acquire a controlling terminal.
    os.setsid()
    if os.fork():
        os._exit(0)
    # Grandchild: this is the daemon.
    os.close(read_fd)
    _redirect_stdio(log_path)
    code = setup()
    with contextlib.suppress(OSError):
        os.write(write_fd, b"ok" if code == 0 else b"fail")
    os.close(write_fd)
    if code != 0:
        os._exit(code)
    main()
    os._exit(0)


class WatchEngine:
    """inotify event source + quiet-period pass loop for one watcher process.

    Round-robin order of ``targets`` is preserved. Events only *schedule*:
    a project fires at most one pass per quiet period, and bursts coalesce
    (last event wins; the timer resets on every event).
    """

    def __init__(
        self,
        core: Any,
        targets: list[tuple[str, str]],
        *,
        quiet: float,
        sweep_interval: float,
        index_root: str,
        echo: Callable[..., None],
    ) -> None:
        self.core = core
        self.targets = targets
        self.quiet = max(1.0, quiet)
        self.sweep_interval = max(1.0, sweep_interval)
        self.index_root = os.path.abspath(index_root)
        self.echo = echo
        # slug -> monotonic time of the most recent qualifying event.
        self.dirty: dict[str, float] = {}
        self._last_pass: dict[str, float] = {}
        self._observer: Any = None
        self.events_active = False

    # -- event plumbing ---------------------------------------------------

    def start_events(self) -> bool:
        """Install the inotify observer. True = event mode, False = the
        polling fallback (watchdog missing or watch-descriptor exhaustion)."""
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            self.echo(
                "watch: watchdog not installed — polling fallback "
                "(install with: uv sync --group watch)")
            return False

        engine = self

        class _Handler(FileSystemEventHandler):  # type: ignore[misc]
            @staticmethod
            def _filter(path: str | None) -> str | None:
                if not path:
                    return None
                path = os.path.abspath(path)
                parts = path.replace(os.sep, "/").split("/")
                if ".git" in parts:
                    return None
                name = os.path.basename(path)
                if name.startswith(".") or name in _TEMP_NAMES \
                        or name.endswith(_TEMP_SUFFIXES):
                    return None
                if path.startswith(engine.index_root + os.sep):
                    return None  # our own writes (manifest/registry/locks)
                return path

            # Only content-change event types dirty a project. Read-only
            # events (opened/closed_no_write — raised by the indexer's own
            # hash scan!) must not re-schedule a pass, or a pass would
            # perpetually re-dirty its project.
            _PASSING_TYPES = frozenset({
                "modified", "created", "deleted", "moved", "closed",
            })

            def on_any_event(self, event: Any) -> None:
                if event.event_type not in self._PASSING_TYPES:
                    return
                src = self._filter(getattr(event, "dest_path", None)
                                   or getattr(event, "src_path", None))
                if src is None:
                    return
                if event.is_directory and event.event_type not in (
                        "deleted", "moved"):
                    # A created dir has no indexable content yet; its files
                    # raise their own events. Deleted/moved dirs change file
                    # sets wholesale and must dirty the project.
                    return
                slug = engine._slug_for(src)
                if slug:
                    engine.dirty[slug] = time.monotonic()

        handler = _Handler()
        observer = Observer()
        try:
            for _slug, root in self.targets:
                observer.schedule(handler, os.path.abspath(root),
                                  recursive=True)
        except OSError as exc:
            self.echo(
                f"watch: inotify schedule failed ({exc}) — polling fallback")
            with contextlib.suppress(Exception):
                observer.stop()
            return False
        with contextlib.suppress(AttributeError):
            observer.daemon_threads = True  # type: ignore[attr-defined]
        observer.start()
        self._observer = observer
        self.events_active = True
        return True

    def _slug_for(self, path: str) -> str | None:
        best: tuple[int, str] | None = None
        for slug, root in self.targets:
            root_abs = os.path.abspath(root)
            if path == root_abs or path.startswith(root_abs + os.sep):
                if best is None or len(root_abs) > best[0]:
                    best = (len(root_abs), slug)
        return best[1] if best else None

    # -- passes -----------------------------------------------------------

    def _run_pass(self, slug: str, path: str, *, sweep: bool) -> None:
        result = self.core.watch_pass(slug, path)
        if result["state"] == "idle":
            r = result["result"]
            if r["files_indexed"] or r["files_deleted"] or r["chunks_embedded"]:
                self.echo(f"{slug}: pass {json.dumps(r, ensure_ascii=False)}")
        elif result["state"] == "indexing":
            self.echo(f"{slug}: {result['detail']}")
        elif result["state"] == "error":
            self.echo(f"{slug}: indexing error: {result['error']}", err=True)
        self._last_pass[slug] = time.monotonic()
        if sweep:
            self.echo(f"{slug}: staleness sweep (periodic, every "
                      f"{int(self.sweep_interval)}s) ran")

    # -- main loop --------------------------------------------------------

    def run(self, duration: float | None, stop: dict[str, bool]) -> None:
        deadline = (
            time.monotonic() + duration if duration and duration > 0 else None)
        next_sweep = time.monotonic() + self.sweep_interval
        try:
            while not stop["flag"]:
                now = time.monotonic()
                for slug, path in self.targets:
                    if stop["flag"]:
                        break
                    fire = False
                    sweep = False
                    if self.events_active:
                        t = self.dirty.get(slug)
                        if t is not None and now - t >= self.quiet:
                            self.dirty.pop(slug, None)
                            fire = True
                    else:
                        # Polling fallback / degraded mode: one hash scan per
                        # project per quiet period.
                        if now - self._last_pass.get(slug, 0.0) >= self.quiet:
                            fire = True
                    if now >= next_sweep:
                        fire = True
                        sweep = True
                    if fire:
                        self._run_pass(slug, path, sweep=sweep)
                if time.monotonic() >= next_sweep:
                    next_sweep = time.monotonic() + self.sweep_interval
                if deadline is not None and time.monotonic() >= deadline:
                    break
                # Short slices so SIGINT/SIGTERM land promptly mid-nap.
                time.sleep(0.1)
        finally:
            if self._observer is not None:
                with contextlib.suppress(Exception):
                    self._observer.stop()
                    self._observer.join(timeout=2)
                self._observer = None
