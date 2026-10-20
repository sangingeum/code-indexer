"""Wall-clock before/after measurements for the store-cap round (issue #22).

Usage: python timing.py <src-dir> <label>

Runs each command three times against the live indexes, reports the median,
and saves the last run's output so the numbers can be compared, not just the
timings. `src-dir` is put first on PYTHONPATH, so a base worktree measures
the "before" build without touching the checkout.
"""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

VENV_PY = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"
OUT_DIR = Path(__file__).resolve().parent
RUNNER = OUT_DIR / "run_cli.py"

COMMANDS = [
    ("find-callers pairA (vivarium-sim)",
     ["find-callers", "pairA", "--project", "vivarium-sim", "--json"]),
    ("find-callers the (b1b7f48b)",
     ["find-callers", "the", "--project", "b1b7f48b", "--json"]),
    ("overview (vivarium-sim)",
     ["overview", "--project", "vivarium-sim"]),
    ("overview (b1b7f48b)",
     ["overview", "--project", "b1b7f48b"]),
]


def run(src: str, args: list[str]) -> tuple[float, str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = src
    t0 = time.perf_counter()
    proc = subprocess.run(
        [str(VENV_PY), str(RUNNER), *args, "--skip-stale-check"],
        cwd="/tmp", env=env, capture_output=True, text=True, timeout=600)
    elapsed = time.perf_counter() - t0
    if proc.returncode != 0:
        raise SystemExit(f"command failed ({proc.returncode}): {args}\n"
                         f"{proc.stderr[:2000]}")
    return elapsed, proc.stdout, proc.stderr


def main() -> None:
    src = sys.argv[1]
    label = sys.argv[2]
    print(f"# {label}: PYTHONPATH={src}")
    for name, args in COMMANDS:
        times: list[float] = []
        stdout = ""
        for _ in range(3):
            elapsed, stdout, _err = run(src, args)
            times.append(elapsed)
        median = statistics.median(times)
        print(f"{name}: runs={[round(t, 3) for t in times]} "
              f"median={median:.3f}s")
        (OUT_DIR / f"{label}-{name.split()[0]}-"
         f"{name.split('(')[1].rstrip(')')}.txt").write_text(stdout)


if __name__ == "__main__":
    main()
