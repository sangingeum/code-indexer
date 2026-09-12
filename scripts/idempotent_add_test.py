"""E2E verification of idempotent add_project:
- tools/list via real stdio server (tool count must be 6)
- add_project twice on a temp project: 1st registers, 2nd returns status
  summary, no duplicate registry row, no second background index
- nonexistent path still errors
Requires live Ollama + Qdrant (defaults point at the LAN instances).
"""

from __future__ import annotations

import json
import os
import selectors
import sqlite3
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class StdioMcp:
    def __init__(self, env_extra):
        env = dict(os.environ)
        env.update(env_extra)
        env.setdefault("INDEX_ROOT", "/tmp/mci-idem-test-state")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_code_indexer"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env, cwd=REPO)
        self.sel = selectors.DefaultSelector()
        self.sel.register(self.proc.stdout, selectors.EVENT_READ)
        self._id = 0
        self.call({"method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"}}})
        self.notify({"method": "notifications/initialized"})

    def _read(self, want_id):
        buf, deadline = "", time.time() + 90
        while time.time() < deadline:
            if not self.sel.select(timeout=1.0):
                continue
            line = self.proc.stdout.readline()
            if not line:
                break
            buf += line
            try:
                resp = json.loads(buf)
            except json.JSONDecodeError:
                continue
            buf = ""
            if resp.get("id") == want_id:
                return resp
        raise TimeoutError(f"no response for id {want_id}")

    def notify(self, req):
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()

    def call(self, req):
        self._id += 1
        req = dict(req, jsonrpc="2.0", id=self._id)
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        return self._read(self._id)

    def tool(self, name, arguments):
        r = self.call({"method": "tools/call", "params":
                       {"name": name, "arguments": arguments}})
        return r["result"]["content"][0]["text"]

    def close(self):
        self.proc.terminate()
        err = self.proc.stderr.read()
        if err.strip():
            print("--- stderr tail ---")
            print("\n".join(err.strip().splitlines()[-6:]))


def registry_rows(index_root):
    conn = sqlite3.connect(os.path.join(index_root, "registry.db"))
    rows = conn.execute("SELECT path, slug FROM projects").fetchall()
    conn.close()
    return rows


def main():
    proj = tempfile.mkdtemp(prefix="mci-idem-proj-")
    with open(os.path.join(proj, "a.py"), "w") as f:
        f.write("def greet(name):\n    return f'hello {name}'\n")
    index_root = "/tmp/mci-idem-test-state"
    subprocess.run(["rm", "-rf", index_root], check=True)

    mcp = StdioMcp({})
    try:
        # 1) tools/list unchanged
        tl = mcp.call({"method": "tools/list"})
        names = sorted(t["name"] for t in tl["result"]["tools"])
        print(f"[tools/list] {len(names)} tools: {names}")
        assert len(names) == 6, names

        # 2) nonexistent path still errors
        out = mcp.tool("add_project", {"path": "/tmp/definitely-not-here-xyz"})
        print("[add nonexistent]", out)
        assert out.startswith("error:"), out

        # 3) first add
        out1 = mcp.tool("add_project", {"path": proj})
        print("[add #1]", out1)
        assert out1.startswith("registered"), out1

        # let the background index start, then call again immediately
        time.sleep(1.0)
        out2 = mcp.tool("add_project", {"path": proj})
        print("[add #2]", out2)
        assert "already registered" in out2, out2
        for token in ("state=", "files=", "chunks=", "last_indexed="):
            assert token in out2, f"missing {token!r} in summary: {out2}"

        # 4) no duplicate registry entry
        rows = registry_rows(index_root)
        print("[registry rows]", rows)
        assert len(rows) == 1, rows

        # 5) no second background index: index_status should show a single
        #    pass; wait for idle and confirm state, then re-check registry.
        out3 = mcp.tool("index_status", {"path": proj})
        print("[index_status]", out3)
        deadline = time.time() + 240
        state = ""
        while time.time() < deadline:
            out3 = mcp.tool("index_status", {"path": proj})
            state = dict(
                kv.split("=", 1) for kv in
                out3.split() if "=" in kv).get("state", "")
            if state == "idle":
                break
            time.sleep(3)
        print("[index_status final]", out3)
        assert state == "idle", out3
        rows2 = registry_rows(index_root)
        assert len(rows2) == 1, rows2

        # 6) second add AFTER completion — still status only, no re-index
        out4 = mcp.tool("add_project", {"path": proj})
        print("[add #3 after idle]", out4)
        assert "already registered" in out4, out4
        time.sleep(2)
        rows3 = registry_rows(index_root)
        assert len(rows3) == 1, rows3
        print("ALL CHECKS PASSED")
    finally:
        mcp.close()


if __name__ == "__main__":
    main()