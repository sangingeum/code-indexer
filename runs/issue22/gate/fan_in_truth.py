"""Bar 3: independent per-file fan-in, computed with its own SQL.

fan_in[F] = imports rows whose resolved_file is F (and importer != F)
          + refs rows whose to_name is DEFINED IN F (first definition by
            (file, start_line) via ROW_NUMBER) and whose referring file != F

Usage: <python> fan_in_truth.py SLUG [TOPJSON]

Prints the top-10 and, when TOPJSON is given (overview --json capture), the
diff against overview's hotspots.fan_in.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

INDEX_ROOT = Path.home() / ".code-indexer"


def connect(slug: str) -> sqlite3.Connection:
    path = INDEX_ROOT / slug / "manifest.db"
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def main() -> None:
    slug = sys.argv[1]
    topjson = sys.argv[2] if len(sys.argv) > 2 else None
    conn = connect(slug)
    try:
        imports = conn.execute(
            "SELECT resolved_file, COUNT(*) FROM imports"
            " WHERE resolved_file IS NOT NULL AND resolved_file != file"
            " GROUP BY resolved_file").fetchall()
        refs = conn.execute(
            "WITH first_def AS ("
            "  SELECT name, file FROM ("
            "    SELECT name, file, ROW_NUMBER() OVER"
            "      (PARTITION BY name ORDER BY file, start_line) AS rn"
            "    FROM symbols) WHERE rn = 1) "
            "SELECT s.file, COUNT(*) FROM refs r"
            " JOIN first_def s ON s.name = r.to_name"
            " WHERE r.file != s.file GROUP BY s.file").fetchall()
        indexed = {r[0] for r in conn.execute("SELECT path FROM files")}
    finally:
        conn.close()

    counts: dict[str, int] = {}
    for f, n in imports:
        counts[f] = counts.get(f, 0) + n
    for f, n in refs:
        counts[f] = counts.get(f, 0) + n
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    print(f"{slug}: files_with_fan_in={len(ranked)}"
          f" total={sum(counts.values())}"
          f" imports_rows={sum(n for _f, n in imports)}"
          f" ref_rows={sum(n for _f, n in refs)}")
    print("independent top-10 (all files in the store):")
    for f, n in ranked[:10]:
        print(f"  {n:>7}  {f}  [indexed={f in indexed}]")
    filt = [(f, n) for f, n in ranked if f in indexed]
    print("independent top-10 (manifest files only, as overview filters):")
    for f, n in filt[:10]:
        print(f"  {n:>7}  {f}")

    if topjson:
        cap = json.loads(Path(topjson).read_text())
        hs = cap["hotspots"]["fan_in"]
        theirs = [(h["file"], h["fan_in"]) for h in hs]
        mine = filt[:10]
        print("comparison vs overview --json:")
        for i in range(max(len(theirs), len(mine))):
            a = mine[i] if i < len(mine) else ("-", -1)
            b = theirs[i] if i < len(theirs) else ("-", -1)
            mark = "OK " if a == b else "DIFF"
            print(f"  {mark} #{i + 1} independent={a[1]:>7} {a[0]}")
            print(f"       {'':4}#{i + 1} overview   ={b[1]:>7} {b[0]}")
        print(f"identical_top10={mine[:10] == theirs[:10]}")


if __name__ == "__main__":
    main()
