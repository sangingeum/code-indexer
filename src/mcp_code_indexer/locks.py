"""Per-project O_EXCL lock files (design §6 step 1 / §7).

Lock file content: pid + ISO timestamp; stolen if older than 30 min
(crashed-process cleanup). If held, callers report `state: indexing`.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import time

logger = logging.getLogger("mcp-code-indexer.locks")

LOCK_STALE_SECONDS = 30 * 60


@contextlib.contextmanager
def project_lock(lock_path: str):
    """Yield True if the lock was acquired, False if already held."""
    fd = None
    try:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {time.strftime('%Y-%m-%dT%H:%M:%S%z')}".encode())
            acquired = True
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            # Stale-lock recovery: steal if older than 30 minutes.
            try:
                age = time.time() - os.stat(lock_path).st_mtime
                if age > LOCK_STALE_SECONDS:
                    logger.warning("stealing stale lock %s (age %.0fs)", lock_path, age)
                    os.unlink(lock_path)
                    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, f"{os.getpid()} {time.strftime('%Y-%m-%dT%H:%M:%S%z')}".encode())
                    acquired = True
                else:
                    acquired = False
            except FileNotFoundError:
                acquired = False  # raced with a releaser; let caller retry
        yield acquired
    finally:
        if fd is not None:
            os.close(fd)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(lock_path)