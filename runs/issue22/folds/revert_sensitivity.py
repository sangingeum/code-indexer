"""Revert-sensitivity evidence for the two fold pins (F9, F10).

For each pin: flip one expectation to the value the pre-fold reasoning would
have produced, run only that test, and require a failure. Then restore the
file byte-exact from the committed blob with `git restore --source=HEAD
--staged --worktree`, and verify sha256 against a copy taken before the first
flip.

Run from anywhere: it locates the repo from its own path.
"""
from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
TEST_REL = "tests/test_graph.py"
TEST = REPO / TEST_REL
COPY = pathlib.Path("/tmp/folds-test_graph.py.committed")

FLIPS = (
    (
        'assert payload["omitted"] == 1, payload',
        'assert payload["omitted"] == 0, payload',
        "test_a_capped_root_list_counts_the_edges_it_also_refused",
        "F9: expectation flipped to the pre-fold claim (omitted: 0)",
    ),
    (
        "    assert len(rows) == 25, len(rows)",
        "    assert len(rows) == 40, len(rows)",
        "test_find_symbol_browse_mode_keeps_its_default_cap",
        "F10: expectation flipped to an uncapped browse read (40)",
    ),
)


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    shutil.copyfile(TEST, COPY)
    committed_sha = sha256(COPY)
    print(f"committed {TEST_REL}: sha256 {committed_sha}")
    failures = 0
    for old, new, test_id, label in FLIPS:
        text = TEST.read_text()
        if text.count(old) != 1:
            print(f"ABORT: anchor not unique for {test_id}: {old!r}")
            return 2
        TEST.write_text(text.replace(old, new))
        print(f"\n== {label} ==")
        proc = subprocess.run(
            ["uv", "run", "pytest", f"tests/test_graph.py::{test_id}",
             "-q", "-W", "error"],
            cwd=REPO, capture_output=True, text=True,
        )
        print(proc.stdout.strip())
        print(proc.stderr.strip())
        print(f"exit={proc.returncode} (failure required)")
        if proc.returncode == 0:
            failures += 1
        subprocess.run(["git", "restore", "--source=HEAD", "--staged",
                        "--worktree", TEST_REL], cwd=REPO, check=True)
        restored_sha = sha256(TEST)
        print(f"restored: sha256 {restored_sha} "
              f"{'MATCH' if restored_sha == committed_sha else 'MISMATCH'}")
        if restored_sha != committed_sha:
            failures += 1
    print("\n" + ("all flips failed as required; tree restored byte-exact"
                  if failures == 0 else f"{failures} problem(s)"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
