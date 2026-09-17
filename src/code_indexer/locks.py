"""Per-project lock files via fcntl.flock (design §6 step 1 / §7, flock ruling).

Mechanism: open the lock file once per project (creating if needed) and take
an exclusive non-blocking flock. The kernel releases the lock automatically
when the holding process dies, so there is NO stale-lock stealing path and no
lock age heuristics — a crashed indexer cannot wedge the project.

FOOTGUN — unlink while held: never ``os.unlink()`` a flock'd lock file while
any process may hold it. With flock the lock lives on the inode, not the
path; unlinking creates a new file at the path on the next open, and two
processes can then hold "the lock" on two different inodes simultaneously.
The lock file is therefore deliberately permanent — we open, lock, and close
without ever unlinking. Its content (pid + timestamp) is diagnostic only.

If the lock is held, callers report "indexing in progress".
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import time
from collections.abc import Iterator


@contextlib.contextmanager
def project_lock(lock_path: str) -> Iterator[bool]:
    """Yield True if the lock was acquired, False if already held elsewhere.

    BlockingIOError from flock(LOCK_EX|LOCK_NB) means another live process
    holds the lock — we do NOT wait and do NOT steal. The lock file is
    permanent; see the module docstring for the unlink-while-held footgun.
    """
    # O_RDWR so fcntl locks work on all filesystems; file is permanent.
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        # Diagnostic-only content (never used for staleness or stealing).
        with contextlib.suppress(OSError):
            os.ftruncate(fd, 0)
            os.write(
                fd,
                f"{os.getpid()} {time.strftime('%Y-%m-%dT%H:%M:%S%z')}".encode(),
            )
        yield True
    finally:
        # Closing the fd releases this process's flock (kernel-managed).
        os.close(fd)
