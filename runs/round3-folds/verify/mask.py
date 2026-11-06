"""Mask host paths in this directory's captures, in place. Idempotent."""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = str(Path.home())
ROOT = str(HERE.parents[2])
PAIRS = [(ROOT, "<project>"), (HOME, "<home>")]

for path in sorted(HERE.iterdir()):
    if path.is_dir() or path.suffix not in {".txt", ".json", ".md", ".diff", ".log"}:
        continue
    if path.name == "mask.py":
        continue
    text = path.read_text(encoding="utf-8")
    out = text
    for needle, replacement in PAIRS:
        out = out.replace(needle, replacement)
    if out != text:
        path.write_text(out, encoding="utf-8")
        print(f"masked {path.name}")
    else:
        print(f"clean {path.name}")
