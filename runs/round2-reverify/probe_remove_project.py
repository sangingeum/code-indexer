"""Side probe: does `remove-project` also remove the identity-stamped collection?

Not one of the eight folds — a deleter, not a writer — but the fold moved every
physical collection to `idx_<slug>__<identity digest>`, and remove-project
names the collection `f"idx_{slug}"` (the alias). This observes what Qdrant is
left holding after the command, then cleans it up.

Usage: QDRANT_URL=<vector store> uv run python runs/round2-reverify/probe_remove_project.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, "src")
QDRANT = os.environ.get("QDRANT_URL", "http://localhost:6333").rstrip("/")


class MaskedStream:
    def __init__(self, stream, root):
        self.stream, self.root = stream, root

    def write(self, text):
        return self.stream.write(text.replace(self.root, "<workdir>")
                                 .replace(QDRANT, "<qdrant>"))

    def flush(self):
        self.stream.flush()


def api(path, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(QDRANT + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode()
    return json.loads(body) if body else {}


def state(slug):
    cols = [c["name"] for c in api("/collections")["result"]["collections"]]
    aliases = {a["alias_name"]: a["collection_name"]
               for a in api("/aliases")["result"]["aliases"]}
    return ([c for c in cols if slug in c],
            {a: t for a, t in aliases.items() if slug in a or slug in t})


def run(env, cwd, *argv):
    e = dict(env)
    e["PYTHONPATH"] = SRC + (os.pathsep + e["PYTHONPATH"] if e.get("PYTHONPATH") else "")
    proc = subprocess.run([sys.executable, "-m", "code_indexer.cli", *argv],
                          cwd=cwd, env=e, capture_output=True, text=True,
                          timeout=600)
    print(f"  $ code-indexer {' '.join(argv)}  ->  exit={proc.returncode}")
    for line in (proc.stdout + proc.stderr).splitlines():
        print("    | " + line)
    return proc


def main() -> int:
    root = tempfile.mkdtemp(prefix="round2-reverify-remove-", dir=os.environ.get("TMPDIR"))
    sys.stdout = MaskedStream(sys.stdout, root)
    state_dir = os.path.join(root, "state")
    os.makedirs(state_dir)
    env = os.environ.copy()
    env["INDEX_ROOT"] = state_dir
    project = os.path.join(root, "proj")
    os.makedirs(project)
    with open(os.path.join(project, "a.py"), "w", encoding="utf-8") as fh:
        fh.write("def alpha(x):\n    return x + 1\n")

    print("=" * 72)
    print("SIDE  remove-project vs the identity-stamped physical collection")
    print("=" * 72)
    proc = run(env, project, "add-project", project, "--vectors", "on")
    if proc.returncode != 0:
        return 2
    slug = None
    for line in proc.stdout.splitlines():
        if "(slug " in line:
            slug = line.split("(slug ")[1].rstrip(")").strip()
    print(f"    slug={slug}")
    cols, aliases = state(slug)
    print(f"    after the pass: collections={cols} aliases={aliases}")
    run(env, project, "remove-project", project)
    cols, aliases = state(slug)
    print(f"    after remove-project: collections={cols} aliases={aliases}")
    leaked = bool(cols or aliases)
    print(f"    residue left behind: {leaked}")
    if leaked:
        for name in cols:
            api(f"/collections/{name}", "DELETE")
        for alias in list(aliases):
            api("/collections/aliases", "POST", {"actions": [
                {"delete_alias": {"alias_name": alias}}]})
        print(f"    cleaned up: {state(slug)}")
    shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
