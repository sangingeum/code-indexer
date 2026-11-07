"""Does a truncated re-check disclose itself? 33+ rowless candidates, each
256 KiB, must exhaust the 8 MiB budget and be counted."""

from __future__ import annotations

import os
import sys
import tempfile

root = sys.argv[1]
sys.path.insert(0, os.path.join(root, "src"))

from code_indexer.config import Config          # noqa: E402
from code_indexer.core import Core              # noqa: E402
from code_indexer.registry import Registry      # noqa: E402
from code_indexer import staleness              # noqa: E402


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


tmp = tempfile.mkdtemp(prefix="probe-unexamined-")
project = os.path.join(tmp, "proj")
os.makedirs(project)
_write(os.path.join(project, "a.py"), "def a():\n    return 1\n")

state = os.path.join(tmp, "state")
os.makedirs(state)
cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
             index_root=state, embed_batch=48, upsert_batch=256,
             max_file_bytes=1048576, watch_debounce=3,
             watch_sweep_interval=300)
core = Core(cfg, skip_stale_check=True)
reg = Registry(os.path.join(state, "registry.db"))
entry = reg.add(project, name="proj")
core.registry = reg
result = core.run_index(entry.slug, project, vectors="off")
assert result["state"] == "idle", result

# 34 rowless candidates: 32 x 256 KiB (exactly the 8 MiB budget) plus two.
for i in range(32):
    _write(os.path.join(project, f"big{i:02d}.py"), "# filler\n" * 35000)
for i in range(2):
    _write(os.path.join(project, f"tail{i}.py"), "def t():\n    return 2\n")

verdict = core.staleness(entry)
block = staleness.block(verdict)
print("state", verdict.state)
print("reason_count", block["stale_reason_count"],
      "added", sum(1 for r in verdict.reasons if r.kind == "added"))
print("withheld_count", block["withheld_count"],
      "unexamined", block["withheld_unexamined_count"])
print("tail0 is added:", ("added", "tail0.py") in [(r.kind, r.file)
                                                  for r in verdict.reasons])
reg.close()
