"""B2 and B3 bars, runnable against any checkout.

B2: the diff follows the pass's ``allow_sensitive`` override -- with it set,
a rowless scanned path is ``added`` (what the next pass writes); unset, the
same path is the withheld state.

B3: the re-check budget is charged in bytes actually read, so one 12 MiB new
file sorted first does not cost a later sensitive file its classification.

Real passes, real manifests, real verdicts. Prints one line per case and does
not assert, so the same script runs on the pre-fold tip and on the fold tip.

Usage: python probe_b2_b3.py <checkout>
"""

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

SECRET = "aws_access_key_id = " + "AKIA" + "IOSFODNN7" + "EXAMPLE\n"
SECRET_FILE = "CONFIG = {" + repr(SECRET) + "}\n"


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _core(index_root: str, max_file_bytes: int = 1048576) -> Core:
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=index_root, embed_batch=48, upsert_batch=256,
                 max_file_bytes=max_file_bytes, watch_debounce=3,
                 watch_sweep_interval=300)
    return Core(cfg, skip_stale_check=True)


def _family(block: dict) -> str:
    """The block's own name for the withheld files, whatever this tip calls it."""
    return "withheld" if "withheld_count" in block else "skipped"


def _report(label: str, verdict) -> None:
    block = staleness.block(verdict)
    family = _family(block)
    print(f"{label}: state={verdict.state} "
          f"reasons={[(r.kind, r.file) for r in verdict.reasons]} "
          f"{family}_count={block[family + '_count']} "
          f"{family}={[(f['file'], f['reason']) for f in block[family + '_files']]}")


def b2() -> None:
    """The override decides the diff exactly as it decides the pass."""
    tmp = tempfile.mkdtemp(prefix="fold-b2-")
    project = os.path.join(tmp, "proj")
    os.makedirs(project)
    _write(os.path.join(project, "a.py"), "def a():\n    return 1\n")
    _write(os.path.join(project, "secretive.py"), SECRET_FILE)
    state = os.path.join(tmp, "state")
    os.makedirs(state)
    core = _core(state)
    reg = Registry(os.path.join(state, "registry.db"))
    entry = reg.add(project, name="proj")
    core.registry = reg
    result = core.run_index(entry.slug, project, vectors="off")
    assert result["state"] == "idle", result

    _report("B2 allow_sensitive unset ", core.staleness(entry))
    m = core.manifest_for(entry.slug)
    try:
        m.set_meta("allow_sensitive", "1")
    finally:
        m.close()
    _report("B2 allow_sensitive set   ", core.staleness(entry))
    reg.close()


def b3() -> None:
    """One huge new file must not spend the whole re-check budget."""
    tmp = tempfile.mkdtemp(prefix="fold-b3-")
    project = os.path.join(tmp, "proj")
    os.makedirs(project)
    _write(os.path.join(project, "a.py"), "def a():\n    return 1\n")
    state = os.path.join(tmp, "state")
    os.makedirs(state)
    core = _core(state, max_file_bytes=64 * 1024 * 1024)
    reg = Registry(os.path.join(state, "registry.db"))
    entry = reg.add(project, name="proj")
    core.registry = reg
    result = core.run_index(entry.slug, project, vectors="off")
    assert result["state"] == "idle", result

    # 12 MiB, sorted before the secret: charged by size it would spend the
    # whole 8 MiB budget and leave the secret unclassified.
    _write(os.path.join(project, "aa_big.py"),
           "# filler\n" * (12 * 1024 * 1024 // 9))
    _write(os.path.join(project, "z_secret.py"), SECRET_FILE)
    _report("B3 12 MiB first, secret ", core.staleness(entry))
    reg.close()


if __name__ == "__main__":
    b2()
    b3()
