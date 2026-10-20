"""Bar 10 probe: cold-subprocess timing, base vs tip, median of three.

Each run is a fresh interpreter through run_cli.py with CI_SRC naming the
tree, `--skip-stale-check`, stdout discarded (no reader-side cost), and the
wall clock measured around the subprocess. Thresholds are the round's:
find-callers under 3 s, overview under 2 s.
"""
import os
import statistics
import subprocess
import sys
import time

TIP = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
BASE = os.environ.get("CI22_BASE", "")
if not BASE:
    raise SystemExit("set CI22_BASE to a worktree at 58473d9")
RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_cli.py")
PY = os.path.join(TIP, ".venv", "bin", "python")

CASES = [
    ("find-callers __init__ (code-indexer)", 3.0,
     ["find-callers", "__init__", "--project", "code-indexer",
      "--max-nodes", "400"]),
    ("find-callers Compile (vivarium-sim)", 3.0,
     ["find-callers", "Compile", "--project", "vivarium-sim"]),
    ("find-callers pairA (vivarium-sim)", 3.0,
     ["find-callers", "pairA", "--project", "vivarium-sim"]),
    ("overview (vivarium-sim)", 2.0, ["overview", "--project", "vivarium-sim"]),
    ("overview (b1b7f48b)", 2.0, ["overview", "--project", "b1b7f48b"]),
]


def measure(tree: str, args: list[str]) -> list[float]:
    times = []
    env = dict(os.environ, CI_SRC=os.path.join(tree, "src"),
               CI_TREE="base" if tree == BASE else "tip")
    for _ in range(3):
        start = time.perf_counter()
        subprocess.run([PY, RUNNER, *args, "--skip-stale-check", "--json"],
                       env=env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
        times.append(time.perf_counter() - start)
    return times


def main() -> int:
    failures = 0
    print("tip  = this checkout")
    print("base = a worktree at 58473d9 (CI22_BASE)")
    for label, limit, args in CASES:
        base = measure(BASE, args)
        tip = measure(TIP, args)
        bmed, tmed = statistics.median(base), statistics.median(tip)
        ok = tmed < limit
        failures += 0 if ok else 1
        print(f"{label}:")
        print(f"  base runs={[round(t, 3) for t in base]} median={bmed:.3f}s")
        print(f"  tip  runs={[round(t, 3) for t in tip]} median={tmed:.3f}s"
              f"  limit={limit:.1f}s within={ok}"
              f"  delta={tmed - bmed:+.3f}s")
    print(f"all_within_threshold: {failures == 0}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
