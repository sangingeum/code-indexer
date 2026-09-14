"""Cleanup: deregister audit temp repos left in the registry/index root."""

import glob
import os
import shutil
import sqlite3

DB = os.path.expanduser("~/.mcp-code-indexer/registry.db")
conn = sqlite3.connect(DB)
rows = conn.execute("SELECT slug, path FROM projects").fetchall()
conn.close()
for slug, path in rows:
    if path.startswith("/tmp/audit-repo-") or path.startswith("/tmp/e2e-repo-"):
        print("purging", slug, path)
        for f in glob.glob(os.path.expanduser(f"~/.mcp-code-indexer/{slug}*")):
            try:
                os.remove(f)
            except OSError:
                shutil.rmtree(f, ignore_errors=True)
        conn = sqlite3.connect(DB)
        conn.execute("DELETE FROM projects WHERE slug=?", (slug,))
        conn.commit()
        conn.close()
