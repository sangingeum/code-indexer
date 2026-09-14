"""E2E: server-side find_symbol fresh-server regression + get_code_context
symbol= multi-site verification (after the schema_version staleness fix).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(__file__))
from e2e_codeintel import MCPClient, make_cpp_repo  # noqa: E402


def main():
    repo = tempfile.mkdtemp(prefix="fresh-repo-")
    make_cpp_repo(repo)
    c = MCPClient()
    try:
        c.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                              "clientInfo": {"name": "fresh", "version": "0"}})
        print(c.tool("add_project", path=repo))
        for _ in range(60):
            st = c.tool("index_status", path=repo)
            if "state=idle" in st:
                break
            time.sleep(1)
        print("== find_symbol ==")
        print(c.tool("find_symbol", name="FileTransferSession", project=repo))
        print("== get_code_context symbol=start (multi-site across files) ==")
        print(c.tool("get_code_context", file="src/transfer/session.cpp",
                     project=repo, symbol="start", context_lines=1))
    finally:
        c.tool("remove_project", path=repo)
        c.close()
        shutil.rmtree(repo, ignore_errors=True)
    print("FRESH-SERVER OK")


if __name__ == "__main__":
    main()
