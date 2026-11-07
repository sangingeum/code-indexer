"""B1 bar: one semantic-search JSON document with a fan-out skip list AND a
non-zero withheld count.

In-process through the production methods only -- ``Core.search`` for the
fan-out and ``Core.format_hits`` for the JSON composition (the
``payload.update(staleness_block)`` site the review proved). The embedder and
store stubs are the ones ``tests/test_search_isolation.py`` uses, so no
service is contacted; everything else (the index pass, the verdict, the
withheld file) is real.

Usage: python probe_b1_composition.py <checkout>
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

root = sys.argv[1]
sys.path.insert(0, os.path.join(root, "src"))
sys.path.insert(0, os.path.join(root, "tests"))

from code_indexer.config import Config          # noqa: E402
from code_indexer.core import Core              # noqa: E402
from code_indexer.registry import Registry      # noqa: E402
from code_indexer import staleness              # noqa: E402
import test_search_isolation as rig             # noqa: E402

SECRET = "aws_access_key_id = " + "AKIA" + "IOSFODNN7" + "EXAMPLE\n"
SECRET_FILE = "CONFIG = {" + repr(SECRET) + "}\n"


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="fold-b1-")
    live = os.path.join(tmp, "live")
    dead = os.path.join(tmp, "dead")
    dead2 = os.path.join(tmp, "dead2")
    for path in (live, dead, dead2):
        os.makedirs(path)
        _write(os.path.join(path, "a.py"), "def handler():\n    return 1\n")
    _write(os.path.join(live, "secretive.py"), SECRET_FILE)

    state = os.path.join(tmp, "state")
    os.makedirs(state)
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=state, embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=3,
                 watch_sweep_interval=300)
    core = Core(cfg, skip_stale_check=True)
    reg = Registry(os.path.join(state, "registry.db"))
    live_entry = reg.add(live, name="liveproj")
    dead_entry = reg.add(dead, name="deadproj")
    dead2_entry = reg.add(dead2, name="deadproj2")
    core.registry = reg
    core.embedder = rig.StubEmbedder()
    core.indexer.embedder = core.embedder
    live_slug = live_entry.slug

    class _Store(rig.StubStore):
        """Answers for the live project's collections only, like Qdrant."""

        def collection_exists(self, name):
            return live_slug in name

        def swap_collection(self, collection, alias):
            return None

        def drop_collection(self, name):
            return None

        def delete_collection(self, name):
            return None

        def search(self, name, *a, **kw):
            if live_slug not in name:
                raise RuntimeError(
                    f"UnexpectedResponse: Collection `{name}` doesn't exist")
            return [rig._Hit("a.py")]

    core.store = _Store(f"idx_{live_slug}")
    core.indexer.store = core.store
    for entry in (live_entry, dead_entry, dead2_entry):
        result = core.run_index(entry.slug, entry.path, vectors="on")
        assert result["state"] == "idle", result

    # The fan-out: the live project answers; the dead one's collection is
    # absent, so it is skipped with the reason its store raised.
    result = core.search("handler", all_projects=True, mode="dense")
    verdict = core.staleness(live_entry)
    block = staleness.block(verdict)
    document = core.format_hits(result["hits"], "json",
                                dropped=result.get("dropped", 0),
                                skipped=result["skipped"],
                                partial=result["partial"],
                                staleness_block=block)
    payload = json.loads(document)

    def sanitized(text: str) -> str:
        """No host paths in a committed capture."""
        return text.replace(tmp, "<scratch>")

    print(sanitized(json.dumps(payload, indent=2, ensure_ascii=False)))

    fan_out = payload["skipped"]
    print("\n-- checks --")
    print(f"fan-out skipped:  {[sanitized(s['project']) for s in fan_out]}")
    print(f"skipped_count:    {payload['skipped_count']} "
          f"(== len(skipped): {payload['skipped_count'] == len(fan_out)})")
    print(f"withheld_count:   {payload.get('withheld_count')}")
    print(f"withheld_files:   {payload.get('withheld_files')}")
    print(f"withheld_summary: {payload.get('withheld_summary')}")
    print(f"keys:             {sorted(payload)}")
    ok = (payload["skipped_count"] == len(fan_out) == 2
          and payload.get("withheld_count") == 1)
    print(f"PASS: {ok}")
    reg.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
