"""Strip machine-specific paths from a captured run so it can be published.

    sanitize_capture.py RAW OUT

Drops pytest's `tmp_path = ...` fixture lines and rewrites absolute temporary
roots to placeholders. Nothing else changes: assertions, counts and failure
names stay byte-identical, which is what the capture is cited for.
"""
import re
import sys

raw, out = sys.argv[1], sys.argv[2]
kept = []
for line in open(raw, encoding="utf-8", errors="replace"):
    if line.startswith("tmp_path = "):
        continue
    line = re.sub(r"/tmp/pytest-of-[^/\s'\"]+", "<pytest-tmp>", line)
    line = re.sub(r"/tmp/ci22-[a-z]+", "<base-worktree>", line)
    line = re.sub(r"/home/[^/\s]+/[^\s'\"]*", "<repo>", line)
    kept.append(line)
open(out, "w", encoding="utf-8").write("".join(kept))
print(f"sanitized {raw} -> {out} ({len(kept)} lines)")
