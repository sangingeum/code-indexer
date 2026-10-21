"""Strip machine-specific paths from a captured run so it can be published.

    sanitize_capture.py RAW OUT

Drops pytest's `tmp_path = ...` fixture lines and rewrites this host's own
absolute prefixes — the account home and this checkout — to placeholders.
Nothing else changes: assertions, counts and failure names stay byte-identical,
which is what the capture is cited for.

The prefixes are derived at run time, never written down: the repository root
from this file's own location and the account home from `Path.home()`. A path
that belongs to neither is not this host's to relabel — a foreign machine's
path passes through **unchanged**. Rewriting it to `<repo>` would assert that
another machine's checkout is this repository, which is the opposite of what
sanitising a capture is for.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
HOME = Path.home()

# Repository first: this checkout usually lives under the account home, and its
# paths deserve the more specific placeholder. Only these two prefixes are
# rewritten; anything else is a foreign path.
RULES = [
    (re.compile(r"/tmp/pytest-of-[^/\s'\"]+"), "<pytest-tmp>"),
    (re.compile(r"/tmp/ci22-[a-z]+"), "<base-worktree>"),
    (re.compile(re.escape(str(REPO_ROOT))), "<repo>"),
    (re.compile(re.escape(str(HOME)) + r"(?=/|\s|[\"']|$)"), "<home>"),
]


def sanitize_capture(text: str) -> str:
    """Return the capture text with this host's paths placeholdered."""
    for pattern, replacement in RULES:
        text = pattern.sub(replacement, text)
    return text


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: sanitize_capture.py RAW OUT", file=sys.stderr)
        return 2
    raw, out = (Path(arg) for arg in args)
    kept = [
        sanitize_capture(line)
        for line in raw.read_text(encoding="utf-8", errors="replace").splitlines(True)
        if not line.startswith("tmp_path = ")
    ]
    out.write_text("".join(kept), encoding="utf-8")
    print(f"sanitized {raw} -> {out} ({len(kept)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
