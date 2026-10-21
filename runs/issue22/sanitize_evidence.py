"""Rewrite local paths out of the committed round evidence.

The round doc and its capture files are published with the repository, so
machine-specific paths are replaced with placeholders. Only the files named
below are touched; the raw CLI captures stay local working material.
"""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[2]
HOME = Path.home()
FILES = ["probe-before.txt", "timing-before.txt", "timing-after.txt",
         "verify-fan-in.txt", "pins-fail-on-before.txt"]

# The prefixes are derived from the running checkout, never written down: this
# file is published with the repository, so a literal path here would leak the
# machine layout the script exists to rewrite.
#
# The account home carries a boundary: the next character must not be a path or
# account-name character, so a neighbouring account (`/home/name2`) is left
# alone. It is expressed as a negative lookahead because the alternative — a
# list of characters that may follow — misses the places paths actually end in
# prose (`,`, `)`, `:`, `;`, a closing full stop), and a miss here is
# under-redaction, the one direction this helper must not fail in.
RULES = [
    (re.compile(re.escape(str(REPO_ROOT / "src"))), "<repo>/src"),
    (re.compile(re.escape(str(REPO_ROOT))), "<repo>"),
    (re.compile(r"/tmp/pytest-of-[^/\s]+/pytest-\d+"), "<tmp>"),
    (re.compile(r"/tmp/[^/\s]+/src"), "<base-worktree>/src"),
    (re.compile(r"/tmp/[^/\s]+"), "<base-worktree>"),
    (re.compile(re.escape(str(HOME)) + r"(?![A-Za-z0-9._-])"), "<home>"),
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
