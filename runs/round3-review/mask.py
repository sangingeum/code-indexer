"""Mask host-specific paths in this review's captures before they are committed.

Captures are quoted evidence and keep their payload, but a machine path is
never publishable, so the repository root and the home directory are replaced
with their published spellings. Idempotent: re-running it is a no-op.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path.cwd()
HOME = Path.home()
REVIEW = ROOT / "runs" / "round3-review"

REPLACEMENTS = [
    (str(ROOT), "<project-root>"),
    (str(HOME), "<home>"),
]

masked = 0
for path in sorted(REVIEW.iterdir()):
    if path.suffix not in {".txt", ".json", ".log", ".out"}:
        continue
    text = path.read_text(encoding="utf-8")
    new = text
    for old, replacement in REPLACEMENTS:
        new = new.replace(old, replacement)
    if new != text:
        path.write_text(new, encoding="utf-8")
        masked += 1
print(f"masked {masked} capture file(s)")
