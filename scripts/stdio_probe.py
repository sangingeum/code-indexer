"""Stdio handshake probe: spawn the real MCP server via stdio, send
initialize + tools/list, and print the tool names. Uses select() to avoid
blocking reads."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys


def main() -> None:
    env = dict(os.environ)
    env.setdefault("INDEX_ROOT", "/tmp/mci-probe-state")
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_code_indexer"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env,
    )
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    try:
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "probe", "version": "0.0.1"},
            }},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        for r in reqs:
            proc.stdin.write(json.dumps(r) + "\n")
        proc.stdin.flush()

        tools = None
        server_info = None
        buf = ""
        import time
        deadline = __import__("time").time() + 30
        while __import__("time").time() < deadline:
            events = sel.select(timeout=1.0)
            if not events:
                continue
            chunk = proc.stdout.readline()
            if not chunk:
                break
            buf += chunk
            try:
                resp = json.loads(buf)
            except json.JSONDecodeError:
                continue
            buf = ""
            if resp.get("id") == 1:
                server_info = resp["result"]["serverInfo"]
                print("initialize OK:", server_info)
            elif resp.get("id") == 2:
                tools = [t["name"] for t in resp["result"]["tools"]]
                break
        if tools is None:
            print("FAILED: no tools/list response within 30s")
            sys.exit(1)
        expected = {"add_project", "remove_project", "list_projects",
                    "semantic_search", "index_status", "reindex_project"}
        print("tools/list:", sorted(tools))
        missing = expected - set(tools)
        assert not missing, f"missing tools: {missing}"
        assert len(tools) == 6, f"expected exactly 6 tools, got {len(tools)}"
        print(f"HANDSHAKE PASS — {len(tools)} tools exposed")
    finally:
        proc.terminate()
        err = proc.stderr.read()
        if err.strip():
            print("--- server stderr (tail) ---")
            print("\n".join(err.strip().splitlines()[-4:]))


if __name__ == "__main__":
    main()