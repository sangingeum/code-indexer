"""Third probe: reachability of the find_symbols(limit=25) cap under the walks."""
import os
import sqlite3

ROOT = os.path.expanduser("~/.code-indexer")
for slug in ("vivarium-sim", "b1b7f48b", "code-indexer", "calc-engine",
             "tackboard"):
    path = os.path.join(ROOT, slug, "manifest.db")
    if not os.path.exists(path):
        print(f"{slug}: MISSING")
        continue
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    top = con.execute(
        "SELECT name, COUNT(*) AS n FROM symbols GROUP BY name"
        " ORDER BY n DESC, name LIMIT 3").fetchall()
    over25 = con.execute(
        "SELECT COUNT(*) FROM (SELECT name FROM symbols GROUP BY name"
        " HAVING COUNT(*) > 25)").fetchone()[0]
    over25f = con.execute(
        "SELECT COUNT(*) FROM (SELECT name FROM symbols GROUP BY name"
        " HAVING COUNT(DISTINCT file) > 25)").fetchone()[0]
    print(f"{slug}: top={top} names_over_25_rows={over25} "
          f"names_in_over_25_files={over25f}")
    con.close()
