"""Is `ORDER BY file, line` a total order for the capped store reads?

Counts (file, line) groups that hold more than one row for one to_name: those
are ties a LIMIT can cut inside, so the prefix is not reproducible in principle.
"""

import sqlite3
import sys
from pathlib import Path

for slug in sys.argv[1:]:
    conn = sqlite3.connect(
        f"file:{Path.home() / '.code-indexer' / slug / 'manifest.db'}?mode=ro",
        uri=True)
    tied = conn.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT to_name, file, line FROM refs"
        "  GROUP BY to_name, file, line HAVING COUNT(*) > 1)").fetchone()[0]
    rows = conn.execute(
        "SELECT COALESCE(SUM(n), 0) FROM ("
        "  SELECT COUNT(*) n FROM refs"
        "  GROUP BY to_name, file, line HAVING COUNT(*) > 1)").fetchone()[0]
    print(f"{slug}: tied (to_name,file,line) groups={tied} rows_in_ties={rows}")
    conn.close()
