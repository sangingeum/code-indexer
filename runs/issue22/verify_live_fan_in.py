"""Independent check: the store's per-file fan-in aggregate equals a hand
count of the same live manifest (read-only).

The hand count reads every row the old sampled scan intended to read —
imports and all of `refs`, with the first-definition rule — and is then
compared with `Manifest.file_fan_in()`.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[2] / "src")
sys.path.insert(0, SRC)

from code_indexer.manifest import Manifest  # noqa: E402

INDEX_ROOT = Path.home() / ".code-indexer"


def hand_count(slug: str) -> dict[str, int]:
    conn = sqlite3.connect(
        f"file:{INDEX_ROOT / slug / 'manifest.db'}?mode=ro", uri=True)
    try:
        definer: dict[str, str] = {}
        for name, path in conn.execute(
                "SELECT name, file FROM symbols ORDER BY file, start_line"):
            definer.setdefault(name, path)
        counts: dict[str, int] = {}
        for src_file, resolved in conn.execute(
                "SELECT file, resolved_file FROM imports"):
            if resolved is not None and resolved != src_file:
                counts[resolved] = counts.get(resolved, 0) + 1
        for src_file, to_name in conn.execute(
                "SELECT file, to_name FROM refs"):
            target = definer.get(to_name)
            if target is not None and src_file != target:
                counts[target] = counts.get(target, 0) + 1
        return counts
    finally:
        conn.close()


def main() -> None:
    ok = True
    for slug in ("vivarium-sim", "b1b7f48b"):
        m = Manifest(str(INDEX_ROOT / slug / "manifest.db"))
        try:
            aggregated = dict(m.file_fan_in())
        finally:
            m.close()
        manual = hand_count(slug)
        same_keys = set(aggregated) == set(manual)
        diffs = {k: (aggregated.get(k), manual.get(k))
                 for k in set(aggregated) | set(manual)
                 if aggregated.get(k) != manual.get(k)}
        total_agg = sum(aggregated.values())
        total_manual = sum(manual.values())
        print(f"{slug}: files={len(aggregated)} same_keys={same_keys} "
              f"mismatches={len(diffs)} total_aggregate={total_agg} "
              f"total_manual={total_manual}")
        if diffs:
            ok = False
            print(f"  first mismatches: {list(diffs.items())[:5]}")
    print("verdict:", "MATCH" if ok else "MISMATCH")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
