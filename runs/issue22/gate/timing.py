"""Gate probe: time the four graph/overview probes for base and tip.

Three runs each, cold subprocess, output discarded, median reported against
the round's thresholds (find-callers < 3 s, overview < 2 s).

Environment:
  CI_TIP_SRC   source tree under test   (default: this checkout's src/)
  CI_BASE_SRC  source tree for "before" (default: /tmp/ci-base-22/src)
  CI_PYTHON    interpreter to run       (default: sys.executable)
"""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
TIP_DEFAULT = str(HERE.parents[2].parent / "src")
PY = os.environ.get("CI_PYTHON", sys.executable)
RUNNER = str(HERE.parent / "cli_runner.py")
TREES = {
    "base": os.environ.get("CI_BASE_SRC", "/tmp/ci-base-22/src"),
    "tip ": TIP_DEFAULT,
}
PROBES = {
    "find-callers pairA (vivarium-sim)": [
        "find-callers", "pairA", "--project", "vivarium-sim", "--json"],
    "find-callers the (b1b7f48b)": [
        "find-callers", "the", "--project", "b1b7f48b", "--json"],
    "overview (vivarium-sim)": [
        "overview", "--project", "vivarium-sim", "--json"],
    "overview (b1b7f48b)": [
        "overview", "--project", "b1b7f48b", "--json"],
}
THRESHOLD = {"find-callers": 3.0, "overview": 2.0}


def run(src: str, args: list[str]) -> tuple[float, int]:
    env = dict(os.environ, PYTHONPATH=src)
    cmd = [PY, RUNNER, *args, "--skip-stale-check"]
    start = time.perf_counter()
    res = subprocess.run(cmd, env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    return time.perf_counter() - start, res.returncode


def main() -> None:
    print(f"python={PY}")
    verdicts = []
    for name, args in PROBES.items():
        for label, src in TREES.items():
            times, codes = [], []
            for _ in range(3):
                t, rc = run(src, args)
                times.append(t)
                codes.append(rc)
            med = statistics.median(times)
            limit = THRESHOLD[args[0]]
            ok = med < limit and set(codes) == {0}
            verdicts.append((name, label, med, limit, ok))
            print(f"{name:38s} {label} runs="
                  f"[{', '.join(f'{t:.3f}' for t in times)}]"
                  f" median={med:.3f}s exit={codes} limit={limit}s"
                  f" {'PASS' if ok else 'FAIL'}")
    print("all_within_threshold:", all(v[4] for v in verdicts))


if __name__ == "__main__":
    main()
