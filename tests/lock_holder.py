"""A per-project flock held by a CHILD PROCESS, like an index pass in flight.

Tests need the lock held by something other than themselves. They must not
``os.fork()`` for it: the test process is multi-threaded, and fork() in a
multi-threaded process violates its precondition (a lock another thread holds
stays locked forever in the child). A fresh subprocess has the same effect and
no precondition to violate.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

_HOLDER = (
    "import fcntl, os, sys, time\n"
    "lock_path, marker = sys.argv[1], sys.argv[2]\n"
    "fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "open(marker, 'w').write('locked')\n"
    "time.sleep(60)\n"
)


class HeldLock:
    """The holder subprocess plus the marker a caller cleans up."""

    def __init__(self, lock_path: str) -> None:
        self.lock_path = lock_path
        self.marker = lock_path + ".holder"
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _HOLDER, lock_path, self.marker])

    def wait_ready(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if os.path.exists(self.marker):
                return True
            time.sleep(0.05)
        return False

    def release(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=10)
        try:
            os.unlink(self.marker)
        except OSError:
            pass


def hold_project_lock(lock_path: str) -> HeldLock:
    """Start the holder; raises when the flock was never taken."""
    holder = HeldLock(lock_path)
    if not holder.wait_ready():
        holder.release()
        raise RuntimeError(f"lock holder never acquired {lock_path}")
    return holder
