"""Rewrite local paths out of the committed round evidence.

The round doc and its capture files are published with the repository, so
machine-specific paths are replaced with placeholders. Only the files named
below are touched; the raw CLI captures stay local working material.
"""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = ["probe-before.txt", "timing-before.txt", "timing-after.txt",
         "verify-fan-in.txt", "pins-fail-on-before.txt"]

RULES = [
    (re.compile(r"/home/keum/dev/athena/code-indexer/src"), "<repo>/src"),
    (re.compile(r"/home/keum/dev/athena/code-indexer"), "<repo>"),
    (re.compile(r"/tmp/ci-issue22-before-0b66431/src"), "<base-worktree>/src"),
    (re.compile(r"/tmp/ci-issue22-before-0b66431"), "<base-worktree>"),
    (re.compile(r"/tmp/pytest-of-[^/\s]+/pytest-\d+"), "<tmp>"),
    (re.compile(r"/home/keum"), "<home>"),
]


def main() -> None:
    for name in FILES:
        path = HERE / name
        text = path.read_text()
        for pattern, replacement in RULES:
            text = pattern.sub(replacement, text)
        path.write_text(text)
        print(f"sanitized {name}")


if __name__ == "__main__":
    main()
