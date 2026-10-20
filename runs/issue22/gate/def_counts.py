"""How many definition rows exist for a symbol name, read-only."""

import sqlite3
import sys
from pathlib import Path

slug = sys.argv[1]
names = sys.argv[2:]
conn = sqlite3.connect(
    f"file:{Path.home() / '.code-indexer' / slug / 'manifest.db'}?mode=ro",
    uri=True)
for name in names:
    n = conn.execute("SELECT COUNT(*) FROM symbols WHERE name = ?",
                     (name,)).fetchone()[0]
    files = conn.execute(
        "SELECT COUNT(DISTINCT file) FROM symbols WHERE name = ?",
        (name,)).fetchone()[0]
    print(f"{slug}: symbols where name={name!r}: rows={n} files={files}")
conn.close()
