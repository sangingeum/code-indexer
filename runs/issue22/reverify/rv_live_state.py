"""Bar 11 probe: the live indexes were not touched — read-only row counts and
the recorded extraction version, straight from each manifest (no CLI, no pass).

    rv_live_state.py
"""
import os
import sqlite3

INDEX_ROOT = os.path.expanduser("~/.code-indexer")
EXPECTED = {           # the gate's record, 58473d9
    "vivarium-sim": (608055, 488),
    "b1b7f48b": (934672, 12),
    "code-indexer": (49785, 1193),
}

for slug, (refs, imports) in EXPECTED.items():
    path = os.path.join(INDEX_ROOT, slug, "manifest.db")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    meta = {k: v for k, v in conn.execute("SELECT key, value FROM meta")}
    got_refs = conn.execute("SELECT COUNT(*) FROM refs").fetchone()[0]
    got_imp = conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    syms = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    conn.close()
    print(f"{slug:14s} refs={got_refs:7d} (was {refs:7d}) imports={got_imp:6d}"
          f" (was {imports:6d}) files={files} symbols={syms}"
          f" extraction_version={meta.get('extraction_version')}"
          f" unchanged={got_refs == refs and got_imp == imports}")
