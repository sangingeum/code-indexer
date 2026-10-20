"""Read-only manifest probes for the store-cap round (issue #22).

Reproduces the reachability numbers: how many rows the capped store reads
would answer with, against what the manifest actually holds. Opens every
database with mode=ro so a probe can never mutate a live index.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

INDEX_ROOT = Path.home() / ".code-indexer"
PROJECTS = ["vivarium-sim", "b1b7f48b", "code-indexer", "calc-engine", "tackboard"]


def connect(slug: str) -> sqlite3.Connection:
    path = INDEX_ROOT / slug / "manifest.db"
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def probe(slug: str) -> dict:
    conn = connect(slug)
    try:
        q = conn.execute
        refs = q("SELECT COUNT(*) FROM refs").fetchone()[0]
        names_over_200 = q(
            "SELECT COUNT(*) FROM (SELECT to_name FROM refs"
            " GROUP BY to_name HAVING COUNT(*) > 200)").fetchone()[0]
        # (file, from_symbol) fan-out pairs: the unit callees_of_file_symbol
        # answers with, capped at 200.
        pairs_over_200 = q(
            "SELECT COUNT(*) FROM (SELECT 1 FROM refs GROUP BY file,"
            " from_symbol HAVING COUNT(*) > 200)").fetchone()[0]
        imports = q("SELECT COUNT(*) FROM imports").fetchone()[0]
        top = q("SELECT to_name, COUNT(*) AS n FROM refs GROUP BY to_name"
                " ORDER BY n DESC LIMIT 3").fetchall()
        pair_a = q("SELECT COUNT(*) FROM refs WHERE to_name = 'pairA'"
                   ).fetchone()[0]
        the = q("SELECT COUNT(*) FROM refs WHERE to_name = 'the'"
                ).fetchone()[0]
        return {"refs": refs, "names_over_200": names_over_200,
                "pairs_over_200": pairs_over_200, "imports": imports,
                "top_names": top, "pairA": pair_a, "the": the}
    finally:
        conn.close()


def main() -> None:
    for slug in PROJECTS:
        d = probe(slug)
        line = (f"{slug}: refs={d['refs']} imports={d['imports']} "
                f"names_gt_200={d['names_over_200']} "
                f"file_from_pairs_gt_200={d['pairs_over_200']} "
                f"pairA={d['pairA']} the={d['the']}")
        if d["pairA"] > 200:
            line += f" (a 200-row read answers {200 / d['pairA'] * 100:.1f}% of pairA)"
        print(line)
        print(f"  top names: {d['top_names']}")


if __name__ == "__main__":
    main()
