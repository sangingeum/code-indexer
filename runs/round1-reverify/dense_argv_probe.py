import json
import os
import sys
import tempfile

os.environ.setdefault("INDEX_ROOT", tempfile.mkdtemp(prefix="dense-argv-"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from code_indexer import server

calls = []


def fake(argv):
    calls.append(list(argv))
    return server.CliOutcome(stdout="SENTINEL", stderr="", returncode=0, duration_ms=1)


server._run_cli = fake

for mode in ("dense", "hybrid", "lexical"):
    calls.clear()
    server.semantic_search("q", mode=mode)
    print(mode + " -> " + json.dumps(calls[0]))

print("PROBE_OK")
