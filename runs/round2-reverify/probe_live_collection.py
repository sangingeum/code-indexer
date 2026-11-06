"""FOLD-3 evidence against a LIVE collection, not a stub.

Registers a throwaway project with vectors on, and after each of the three
vector-writing CLI paths (initial full pass, incremental pass via index-more,
the vectors rebuild via reindex-project) asks Qdrant itself what exists: the
physical collection's name, and what the slug's alias points at. Prints every
observation, then deletes the alias and the collection it created.

Usage: QDRANT_URL=<vector store> uv run python runs/round2-reverify/probe_live_collection.py
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
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


def state():
    cols = [c["name"] for c in api("/collections")["result"]["collections"]]
    aliases = {a["alias_name"]: a["collection_name"]
               for a in api("/aliases")["result"]["aliases"]}
    return cols, aliases


def count_points(name):
    try:
        return api(f"/collections/{name}/points/count", "POST",
                   {"exact": True})["result"]["count"]
    except Exception as exc:  # noqa: BLE001
        return f"unavailable ({exc})"


def run(env, cwd, *argv):
    e = dict(env)
    e["PYTHONPATH"] = SRC + (os.pathsep + e["PYTHONPATH"] if e.get("PYTHONPATH") else "")
    proc = subprocess.run([sys.executable, "-m", "code_indexer.cli", *argv],
                          cwd=cwd, env=e, capture_output=True, text=True,
                          timeout=600)
    print(f"  $ code-indexer {' '.join(argv)}  ->  exit={proc.returncode}")
    for line in proc.stdout.splitlines():
        print("    OUT | " + line)
    for line in proc.stderr.splitlines():
        print("    ERR | " + line)
    return proc


def main() -> int:
    root = tempfile.mkdtemp(prefix="round2-reverify-live-", dir=os.environ.get("TMPDIR"))
    sys.stdout = MaskedStream(sys.stdout, root)
    state_dir = os.path.join(root, "state")
    os.makedirs(state_dir)
    env = os.environ.copy()
    env["INDEX_ROOT"] = state_dir
    project = os.path.join(root, "proj")
    os.makedirs(os.path.join(project, "pkg"))
    with open(os.path.join(project, "a.py"), "w", encoding="utf-8") as fh:
        fh.write("def alpha(x):\n    return x + 1\n\n\ndef beta(y):\n    return alpha(y)\n")
    with open(os.path.join(project, "pkg", "b.py"), "w", encoding="utf-8") as fh:
        fh.write("def gamma(z):\n    return z * 2\n")

    failures = []
    print("=" * 72)
    print("FOLD-3 live  three vector-writing paths against the real Qdrant")
    print("=" * 72)
    print(f"    qdrant={QDRANT}")

    proc = run(env, project, "add-project", project, "--vectors", "on")
    if proc.returncode != 0:
        print("SETUP FAILED")
        return 2
    conn = sqlite3.connect(os.path.join(state_dir, "registry.db"))
    try:
        cols = [c[1] for c in conn.execute("PRAGMA table_info(projects)")]
        table = "projects" if "slug" in cols else "project"
        slug = conn.execute(f"SELECT slug FROM {table} WHERE path = ?",
                            (project,)).fetchone()[0]
    finally:
        conn.close()
    manifest = os.path.join(state_dir, slug, "manifest.db")
    mconn = sqlite3.connect(f"file:{manifest}?mode=ro", uri=True)
    identity = mconn.execute(
        "SELECT value FROM meta WHERE key = 'embedding_version'").fetchone()[0]
    mconn.close()
    sys.path.insert(0, SRC)
    from code_indexer.fingerprint import identity_digest
    expected = f"idx_{slug}__{identity_digest(identity)}"
    alias = f"idx_{slug}"
    print(f"    slug={slug}  identity={identity}")
    print(f"    expected physical name: {expected}")

    def check(label):
        present, aliases = state()
        print(f"  [{label}] collections with the slug: "
              f"{sorted(c for c in present if slug in c)}")
        print(f"  [{label}] alias {alias} -> {aliases.get(alias)}")
        print(f"  [{label}] points in {alias}: {count_points(alias)}")
        if aliases.get(alias) != expected:
            failures.append(f"{label}: alias {alias} -> {aliases.get(alias)}, "
                            f"expected {expected}")
        if expected not in present:
            failures.append(f"{label}: physical collection {expected} absent")
        if alias in present:
            failures.append(f"{label}: the plain slug name is a physical collection")
        if any("__new" in c for c in present if slug in c):
            failures.append(f"{label}: a __new scratch collection exists")
    check("full pass")

    print()
    print("  -- incremental pass: one file edited, then a read with --refresh")
    with open(os.path.join(project, "a.py"), "a", encoding="utf-8") as fh:
        fh.write("\n\ndef delta(y):\n    return beta(y) + 1\n")
    run(env, project, "find-symbol", "delta", "--refresh")
    check("incremental pass")

    print()
    print("  -- vectors rebuild (rows current: embeds from chunks, swaps alias)")
    run(env, project, "reindex-project", project, "--vectors", "on")
    check("vectors rebuild")

    print()
    print("  cleanup: drop the alias and every physical collection the slug owns")
    present, aliases = state()
    try:
        if alias in aliases:
            api("/collections/aliases", "POST", {"actions": [
                {"delete_alias": {"alias_name": alias}}]})
        for name in [c for c in present if c.startswith(alias)]:
            api(f"/collections/{name}", "DELETE")
    except Exception as exc:  # noqa: BLE001
        print(f"    cleanup error: {exc}")
    left, left_aliases = state()
    print(f"    remaining with the slug: "
          f"{sorted(c for c in left if slug in c)}  alias={left_aliases.get(alias)}")
    shutil.rmtree(root, ignore_errors=True)

    print()
    print("=" * 72)
    print(f"RESULT: {'PASS' if not failures else 'FAIL'}")
    for f in failures:
        print("  FAIL | " + f)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
