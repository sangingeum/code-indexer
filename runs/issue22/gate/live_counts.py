"""Live manifest row counts + mtime, read-only, for the no-side-effects bar."""

import os
import sqlite3
from pathlib import Path

ROOT = Path.home() / ".code-indexer"
for slug in ("vivarium-sim", "b1b7f48b", "code-indexer"):
    path = ROOT / slug / "manifest.db"
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    refs, imports, files, syms = (
        conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("refs", "imports", "files", "symbols"))
    ver = conn.execute(
        "SELECT value FROM meta WHERE key='extraction_version'").fetchone()
    conn.close()
    print(f"{slug}: refs={refs} imports={imports} files={files}"
          f" symbols={syms} extraction_version={ver[0] if ver else None}"
          f" mtime={os.path.getmtime(path):.0f}")
