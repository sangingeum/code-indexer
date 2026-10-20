"""Ordering-key tie groups in the live manifests, read-only.

`ORDER BY file, line` is not total on either table:
  * imports PK is (file, raw_target, line) — two rows can share (file, line);
  * refs    PK is (file, line, to_name, relationship) — many rows can.
A capped read is a reproducible prefix only when no tie group straddles the
cut, which is what these counts measure.
"""
import os
import sqlite3

ROOT = os.path.expanduser("~/.code-indexer")

for slug in ("vivarium-sim", "b1b7f48b"):
    path = os.path.join(ROOT, slug, "manifest.db")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    imp_total = con.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
    imp_groups = con.execute(
        "SELECT COUNT(*) FROM (SELECT file, line FROM imports"
        " GROUP BY file, line HAVING COUNT(*) > 1)").fetchone()[0]
    ref_total = con.execute("SELECT COUNT(*) FROM refs").fetchone()[0]
    ref_groups = con.execute(
        "SELECT COUNT(*) FROM (SELECT file, line FROM refs"
        " GROUP BY file, line HAVING COUNT(*) > 1)").fetchone()[0]
    biggest = con.execute(
        "SELECT file, line, COUNT(*) AS n FROM refs GROUP BY file, line"
        " ORDER BY n DESC, file, line LIMIT 1").fetchone()
    print(f"=== {slug} ===")
    print(f"  imports: {imp_groups} (file, line) tie groups / {imp_total} rows")
    print(f"  refs:    {ref_groups} (file, line) tie groups / {ref_total} rows")
    print(f"  largest refs group: {biggest[2]} rows at {biggest[0]}:{biggest[1]}")
    con.close()
