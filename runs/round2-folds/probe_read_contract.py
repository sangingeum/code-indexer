"""Capture for the read contract: bytes before/after, and the refusal shape.

Two manifests, the same read batch over each:

  * a CURRENT one (written by this build) - the file must be byte-identical
    after every read command;
  * a PRE-REVISION-7 one (hand-built v6 DDL with one old-key symbol row) -
    every row surface must refuse with exit 4 and an unchanged file, and
    ``index-status`` must report the recorded schema.

Then the vectors-off refusal with an unreachable embedding service: exit 4,
``ConfigError`` naming ``--vectors on``, never the embedder's exit 3.

Paths are masked before printing so the capture is publishable.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile

CLOSED = "http://127.0.0.1:9"

V6_DDL = """
CREATE TABLE files (path TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
    size INTEGER NOT NULL, chunk_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok', mtime_ns INTEGER, inode INTEGER,
    loc INTEGER, language TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE symbols (file TEXT NOT NULL, name TEXT NOT NULL,
    symbol_type TEXT NOT NULL, start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'regex',
    signature TEXT, visibility TEXT, PRIMARY KEY (file, name, start_line));
CREATE TABLE refs (file TEXT NOT NULL, line INTEGER NOT NULL,
    from_symbol TEXT, to_name TEXT NOT NULL, relationship TEXT NOT NULL,
    confidence TEXT NOT NULL, receiver TEXT,
    PRIMARY KEY (file, line, to_name, relationship));
CREATE TABLE imports (file TEXT NOT NULL, raw_target TEXT NOT NULL,
    resolved_file TEXT, kind TEXT NOT NULL, line INTEGER NOT NULL,
    PRIMARY KEY (file, raw_target, line));
CREATE TABLE index_errors (file TEXT NOT NULL, chunk_index INTEGER,
    error TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (file, chunk_index));
INSERT INTO meta VALUES ('schema_version', '6');
INSERT INTO symbols VALUES ('pre7.py', 'search', 'function', 1, 2, 'ast',
    'def search(self):', 'public');
"""

READS = [
    ["find-symbol", "alpha"],
    ["find-definition", "alpha"],
    ["find-references", "alpha"],
    ["skeleton"],
    ["overview"],
    ["outline", "a.py"],
]


class Environment:
    def __init__(self, root: pathlib.Path, project: pathlib.Path) -> None:
        self.root = root
        self.project = project
        self.env = dict(os.environ,
                        INDEX_ROOT=str(root),
                        OLLAMA_URL=CLOSED,
                        QDRANT_URL=CLOSED)

    def run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "code_indexer.cli", *args],
            capture_output=True, text=True, env=self.env, timeout=120)

    def mask(self, text: str) -> str:
        return text.replace(str(self.project), "<project>").replace(
            str(self.root), "<state>")


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_project(base: pathlib.Path) -> pathlib.Path:
    project = base / "proj"
    project.mkdir(parents=True)
    (project / "a.py").write_text(
        "def alpha():\n    return 1\n\n\ndef caller():\n    return alpha()\n",
        encoding="utf-8")
    (project / "b.py").write_text("def other():\n    return 2\n",
                                  encoding="utf-8")
    return project


def read_batch(env: Environment, manifest: pathlib.Path, label: str) -> None:
    print("\n== " + label + " read batch (" + manifest.name + ") ==")
    before = sha256(manifest)
    print("sha256 before: " + before)
    for args in READS:
        result = env.run([*args, "--project", str(env.project)])
        after = sha256(manifest)
        lines = result.stderr.strip().splitlines() or [""]
        print("  {:<16} exit={} stdout={}B sha256_same={}".format(
            args[0], result.returncode, len(result.stdout), after == before))
        if lines[0]:
            print("    stderr: " + env.mask(lines[0]))
    print("sha256 after:  " + sha256(manifest))
    print("byte-identical: " + str(sha256(manifest) == before))


def main() -> None:
    base = pathlib.Path(tempfile.mkdtemp(prefix="round2-folds-"))
    legacy_base = pathlib.Path(tempfile.mkdtemp(prefix="round2-folds-v6-"))
    try:
        root = base / "state"
        project = build_project(base)
        env = Environment(root, project)

        added = env.run(["add-project", str(project)])
        print("== vectors-off registration, both services at a closed port ==")
        print("add-project exit=" + str(added.returncode))
        print(env.mask(added.stdout.strip()))

        (manifest,) = list(root.glob("*/manifest.db"))
        read_batch(env, manifest, "CURRENT (revision written by this build)")

        status = env.run(["index-status", "--project", str(project)])
        print("\nindex-status exit=" + str(status.returncode))
        print(env.mask(status.stdout.strip()))

        search = env.run(["semantic-search", "alpha",
                          "--project", str(project)])
        print("\n== semantic-search on a vectors-off project, embedder at a "
              "closed port ==")
        print("exit={} stdout={!r}".format(search.returncode, search.stdout))
        for line in search.stderr.strip().splitlines():
            print("stderr: " + env.mask(line))

        # A hand-built pre-revision-7 manifest, in its own index root.
        legacy_root = legacy_base / "state"
        legacy_project = build_project(legacy_base)
        legacy_env = Environment(legacy_root, legacy_project)
        registered = legacy_env.run(["add-project", str(legacy_project)])
        assert registered.returncode == 0, registered.stderr
        (legacy_manifest,) = list(legacy_root.glob("*/manifest.db"))
        legacy_manifest.unlink()
        conn = sqlite3.connect(legacy_manifest)
        conn.executescript(V6_DDL)
        conn.commit()
        conn.close()

        legacy_status = legacy_env.run(
            ["index-status", "--project", str(legacy_project)])
        print("\n== PRE-7 manifest: the surface that needs no rows ==")
        print("index-status exit=" + str(legacy_status.returncode))
        print(legacy_env.mask(legacy_status.stdout.strip()))
        read_batch(legacy_env, legacy_manifest, "PRE-7 (hand-built v6)")

        conn = sqlite3.connect(legacy_manifest)
        try:
            rows = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            version = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        finally:
            conn.close()
        print("symbol rows after every read: " + str(rows))
        print("recorded schema after every read: " + version)
    finally:
        shutil.rmtree(base, ignore_errors=True)
        shutil.rmtree(legacy_base, ignore_errors=True)


if __name__ == "__main__":
    main()
