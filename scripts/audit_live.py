"""Live audit: drive the MCP server (stdio) and sample tool outputs.

Checks plan §2 (staleness regression), §3 (semantic_search shape),
and gathers evidence for §4/§6 gaps. Run: uv run python scripts/audit_live.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(__file__))
from e2e_helpers import make_repo  # noqa: E402


class MCPClient:
    """Minimal stdio JSON-RPC client for FastMCP."""

    def __init__(self):
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_code_indexer"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, text=True, bufsize=1,
        )
        self._id = 0

    def call(self, method, params=None):
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method,
               "params": params or {}}
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("server closed")
            obj = json.loads(line)
            if obj.get("id") == self._id:
                return obj

    def tool(self, name, **kwargs):
        r = self.call("tools/call", {"name": name, "arguments": kwargs})
        content = r.get("result", {}).get("content", [])
        return "\n".join(c.get("text", "") for c in content)

    def close(self):
        self.proc.terminate()


def main():
    repo = tempfile.mkdtemp(prefix="audit-repo-")
    make_repo(repo)
    c = MCPClient()
    try:
        c.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "audit", "version": "0"}})
        print("== add_project ==")
        print(c.tool("add_project", path=repo))
        # wait for background index
        for _ in range(60):
            st = c.tool("index_status", path=repo)
            if "state=idle" in st and "files=" in st and "chunks=0" not in st:
                break
            time.sleep(1)
        print(st)
        print("== semantic_search live sample ==")
        print(c.tool("semantic_search", query="token validation login", project=repo, limit=3))

        # §2 regression: modify + create + delete
        print("== modify file, then search after TTL ==")
        with open(os.path.join(repo, "src", "auth.py"), "a") as f:
            f.write("\n\ndef logout(session_id):\n    \"\"\"Destroy a user session.\"\"\"\n    return None\n")
        with open(os.path.join(repo, "src", "extra.py"), "w") as f:
            f.write("class Widget:\n    def render(self):\n        return '<div/>'\n")
        os.remove(os.path.join(repo, "src", "payments.py"))
        # force TTL expiry by waiting > 60s? too slow: instead poke index_status
        # repeatedly; the first call after TTL triggers re-index.
        for _ in range(70):
            time.sleep(1)
            st = c.tool("index_status", path=repo)
            if "logout" in json.dumps(st):
                break
        print(c.tool("semantic_search", query="destroy user session logout", project=repo, limit=2))
        print(c.tool("semantic_search", query="billing invoice total", project=repo, limit=2))
        print("== index_status final ==")
        print(c.tool("index_status", path=repo))
    finally:
        c.close()
        shutil.rmtree(repo, ignore_errors=True)


if __name__ == "__main__":
    main()
