"""Live e2e for plan v2 code-intel tools: symbol index, get_code_context,
find_references, semantic_search json, §2 staleness regression.

Run: uv run python scripts/audit_live.py   (uses a throwaway temp repo)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(__file__))


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

    def tool(self, tool_name, **kwargs):
        r = self.call("tools/call", {"name": tool_name, "arguments": kwargs})
        content = r.get("result", {}).get("content", [])
        return "\n".join(c.get("text", "") for c in content)

    def close(self):
        self.proc.terminate()


def make_cpp_repo(root):
    os.makedirs(os.path.join(root, "src", "transfer"), exist_ok=True)
    with open(os.path.join(root, "src", "transfer", "session.hpp"), "w") as f:
        f.write(
            "#pragma once\n"
            "namespace ft {\n\n"
            "class FileTransferSession {\n"
            " public:\n"
            "  void start();\n"
            "  void stop();\n"
            " private:\n"
            "  int state_;\n"
            "};\n\n"
            "enum class Mode { kPush, kPull };\n\n"
            "}  // namespace ft\n"
        )
    with open(os.path.join(root, "src", "transfer", "session.cpp"), "w") as f:
        f.write(
            '#include "transfer/session.hpp"\n'
            "namespace ft {\n\n"
            "void FileTransferSession::start() {\n"
            "  state_ = 1;\n"
            "}\n\n"
            "void FileTransferSession::stop() {\n"
            "  state_ = 0;\n"
            "}\n\n"
            "}  // namespace ft\n"
        )
    with open(os.path.join(root, "src", "main.cpp"), "w") as f:
        f.write(
            '#include "transfer/session.hpp"\n'
            "int run() {\n"
            "  ft::FileTransferSession s;\n"
            "  s.start();\n"
            "  return 0;\n"
            "}\n"
        )


def wait_idle(c, repo, timeout=120):
    for _ in range(timeout):
        st = c.tool("index_status", path=repo)
        if "state=idle" in st:
            return st
        time.sleep(1)
    raise RuntimeError("index never went idle")


def main():
    repo = tempfile.mkdtemp(prefix="e2e2-repo-")
    make_cpp_repo(repo)
    c = MCPClient()
    ok = True
    try:
        c.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                              "clientInfo": {"name": "e2e2", "version": "0"}})
        print("== add_project ==")
        print(c.tool("add_project", path=repo))
        print(wait_idle(c, repo))

        print("\n== find_symbol exact (AST confidence) ==")
        print(c.tool("find_symbol", name="FileTransferSession", project=repo))
        print("\n== find_symbol substring fallback (labelled) ==")
        print(c.tool("find_symbol", name="Transfer", project=repo))
        print("\n== find_symbol type filter ==")
        print(c.tool("find_symbol", name="Mode", project=repo, symbol_type="enum"))
        print("\n== find_definition ==")
        print(c.tool("find_definition", name="FileTransferSession", project=repo))

        print("\n== get_code_context line-range ==")
        print(c.tool("get_code_context", file="src/main.cpp", project=repo,
                     start_line=2, end_line=4))
        print("\n== get_code_context symbol= (multiple sites) ==")
        print(c.tool("get_code_context", file="src/transfer/session.cpp",
                     project=repo, symbol="FileTransferSession"))
        print("\n== get_code_context path escape rejected ==")
        print(c.tool("get_code_context", file="../../etc/passwd", project=repo,
                     start_line=1, end_line=2))

        print("\n== find_references ==")
        print(c.tool("find_references", name="FileTransferSession", project=repo))
        print("\n== find_references relationship=includes ==")
        print(c.tool("find_references", name="transfer/session.hpp",
                     project=repo, relationship="includes"))

        print("\n== semantic_search json ==")
        out = c.tool("semantic_search", query="transfer session start",
                     project=repo, limit=2, format="json")
        print(out)
        parsed = json.loads(out)
        assert isinstance(parsed, list) and "symbol_type" in parsed[0], "json shape"
        print("json contract OK")

        print("\n== §2 regression: modify / create / delete, verify after TTL ==")
        with open(os.path.join(repo, "src", "main.cpp"), "a") as f:
            f.write("\nint another_new_function() {\n  return 42;\n}\n")
        with open(os.path.join(repo, "src", "new_file.py"), "w") as f:
            f.write("def brand_new_helper():\n    return 'fresh'\n")
        os.remove(os.path.join(repo, "src", "transfer", "session.hpp"))

        found = False
        for _ in range(90):
            time.sleep(1)
            out = c.tool("find_symbol", name="another_new_function", project=repo)
            if "another_new_function" in out:
                found = True
                break
        print("modified-file symbol appears:", found)
        out2 = c.tool("find_symbol", name="brand_new_helper", project=repo)
        print("created-file symbol appears:", "brand_new_helper" in out2, "|", out2)
        out3 = c.tool("find_symbol", name="FileTransferSession", project=repo)
        print("deleted-file symbol gone:", "no symbols" in out3, "|", out3)
        print("\n== index_status final ==")
        print(c.tool("index_status", path=repo))
    finally:
        c.close()
        shutil.rmtree(repo, ignore_errors=True)
    print("DONE ok=%s" % ok)


if __name__ == "__main__":
    main()
