"""Debug: registry state consistency check.

Manifest 'files' rows vs actual scan, and _LAST_SCAN timing, for the
e2e repo. Registered only for diagnosis, then removed.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(__file__))
from e2e_codeintel import MCPClient, make_cpp_repo  # noqa: E402


def main():
    repo = tempfile.mkdtemp(prefix="dbg-repo-")
    make_cpp_repo(repo)
    c = MCPClient()
    try:
        c.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                              "clientInfo": {"name": "dbg", "version": "0"}})
        print(c.tool("add_project", path=repo))
        for _ in range(60):
            st = c.tool("index_status", path=repo)
            if "state=idle" in st:
                break
            time.sleep(1)
        print("index_status:", st)
        # raw registry introspection via a second in-process manifest open
        import sqlite3
        # find slug via registry.db
        db = os.path.expanduser("~/.mcp-code-indexer/registry.db")
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT slug, path FROM projects WHERE path=?", (repo,)).fetchall()
        conn.close()
        print("registry:", rows)
        if rows:
            slug = rows[0][0]
            mdb = os.path.expanduser(f"~/.mcp-code-indexer/{slug}/manifest.db")
            conn = sqlite3.connect(mdb)
            print("files:", conn.execute("SELECT path, status FROM files").fetchall())
            print("symbols:",
                  conn.execute("SELECT file, name FROM symbols").fetchall())
            conn.close()
        # Now call find_symbol via server
        print(c.tool("find_symbol", name="FileTransferSession", project=repo))
        print(c.tool("get_code_context", file="src/transfer/session.cpp",
                     project=repo, symbol="start"))
    finally:
        c.tool("remove_project", path=repo)
        c.close()
        shutil.rmtree(repo, ignore_errors=True)


if __name__ == "__main__":
    main()
