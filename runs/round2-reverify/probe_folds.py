"""Independent re-verify probe for the round-2 folds.

Written from the fold table, not from the fold leg's probe. Every check here
runs the real CLI in a subprocess against a throwaway index root, then reads
the sqlite manifest file directly for the facts a CLI line cannot show
(row counts, recorded revision, table shape).

Usage: uv run python runs/round2-reverify/probe_folds.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, "src")
CLOSED = "http://127.0.0.1:9"

READS = [
    ("find-symbol", ["find-symbol", "alpha"]),
    ("find-definition", ["find-definition", "alpha"]),
    ("find-references", ["find-references", "alpha"]),
    ("skeleton", ["skeleton"]),
    ("overview", ["overview"]),
    ("outline", ["outline", "a.py"]),
]


class Run:
    def __init__(self, argv, proc):
        self.argv = argv
        self.proc = proc

    @property
    def exit(self):
        return self.proc.returncode

    @property
    def out(self):
        return self.proc.stdout

    @property
    def err(self):
        return self.proc.stderr


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def run(env, cwd, *argv):
    e = dict(env)
    e["PYTHONPATH"] = SRC + (os.pathsep + e["PYTHONPATH"] if e.get("PYTHONPATH") else "")
    proc = subprocess.run([sys.executable, "-m", "code_indexer.cli", *argv],
                          cwd=cwd, env=e, capture_output=True, text=True,
                          timeout=300)
    return Run(argv, proc)


def show(r, stream="both"):
    print(f"    exit={r.exit} stdout={len(r.out)}B stderr={len(r.err)}B")
    if stream in ("out", "both") and r.out:
        for line in r.out.splitlines():
            print("    OUT | " + line)
    if stream in ("err", "both") and r.err:
        for line in r.err.splitlines():
            print("    ERR | " + line)


def manifest_db(state, slug):
    return os.path.join(state, slug, "manifest.db")


def slug_of(state, project_path):
    """Find the registered slug for a project path from the registry."""
    db = os.path.join(state, "registry.db")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        tables = [r[0] for r in rows]
        for table in tables:
            cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})")]
            if "slug" in cols and "path" in cols:
                hit = conn.execute(
                    f"SELECT slug FROM {table} WHERE path = ?",
                    (project_path,)).fetchone()
                if hit:
                    return hit[0]
        raise SystemExit(f"no registry row for {project_path} in {db} (tables={tables})")
    finally:
        conn.close()


def meta(db, key):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def scalar(db, sql, args=()):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def symbol_columns(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [c[1] for c in conn.execute("PRAGMA table_info(symbols)")]
    finally:
        conn.close()


class MaskedStream:
    """Stdout that names no host path: the capture lands in git."""

    def __init__(self, stream, root):
        self.stream = stream
        self.root = root
        self.repo = REPO

    def write(self, text):
        return self.stream.write(text.replace(self.root, "<workdir>")
                                 .replace(self.repo + os.sep, ""))

    def flush(self):
        self.stream.flush()


def main() -> int:
    root = tempfile.mkdtemp(prefix="round2-reverify-", dir=os.environ.get("TMPDIR"))
    sys.stdout = MaskedStream(sys.stdout, root)
    state = os.path.join(root, "state")
    os.makedirs(state, exist_ok=True)
    env = os.environ.copy()
    env["INDEX_ROOT"] = state
    env["OLLAMA_URL"] = CLOSED
    env["QDRANT_URL"] = CLOSED

    project = os.path.join(root, "proj")
    os.makedirs(project)
    with open(os.path.join(project, "a.py"), "w", encoding="utf-8") as fh:
        fh.write("def alpha(x):\n    return beta(x)\n\n\ndef beta(x):\n    return x + 1\n")

    print("=" * 72)
    print("SETUP  register + one vectors-off pass (no service is reachable)")
    print("=" * 72)
    r = run(env, project, "add-project", project, "--vectors", "off")
    show(r)
    if r.exit != 0:
        print("SETUP FAILED")
        return 2
    slug = slug_of(state, project)
    db = manifest_db(state, slug)
    print(f"    slug={slug}")
    print(f"    manifest={db}")

    failures = []

    # ---------------------------------------------------------------- FOLD-1a
    print()
    print("=" * 72)
    print("FOLD-1a  a read leaves a CURRENT manifest byte-identical")
    print("=" * 72)
    before = sha(db)
    print(f"    sha256 before: {before}")
    print(f"    schema recorded: {meta(db, 'schema_version')}")
    for name, argv in READS:
        h = sha(db)
        r = run(env, project, *argv)
        after = sha(db)
        same = h == after
        print(f"  {name:16s} exit={r.exit} stdout={len(r.out)}B "
              f"sha_same={same}")
        if r.exit != 0 or not same:
            failures.append(f"FOLD-1a {name}: exit={r.exit} sha_same={same}")
            show(r)
    after_all = sha(db)
    print(f"    sha256 after:  {after_all}")
    print(f"    byte-identical across the whole batch: {before == after_all}")
    if before != after_all:
        failures.append("FOLD-1a: manifest bytes changed across the read batch")

    r = run(env, project, "index-status")
    print("  index-status:")
    show(r)
    if "schema=7" not in r.out:
        failures.append("FOLD-1a: index-status did not print schema=7")

    # ---------------------------------------------------------------- FOLD-1b
    print()
    print("=" * 72)
    print("FOLD-1b  a PRE-7 manifest: every row surface refuses, nothing is lost")
    print("=" * 72)
    legacy_project = os.path.join(root, "legacy")
    shutil.copytree(project, legacy_project)
    r = run(env, legacy_project, "add-project", legacy_project, "--vectors", "off")
    show(r)
    legacy_slug = slug_of(state, legacy_project)
    legacy_db = manifest_db(state, legacy_slug)
    # Downgrade: the revision the file records, and the pre-7 symbol table
    # shape (line-bearing key, no decl_hash) with one row in it.
    conn = sqlite3.connect(legacy_db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("DROP TABLE symbols")
    conn.execute("CREATE TABLE symbols(file TEXT NOT NULL, name TEXT NOT NULL, "
                 "start_line INTEGER NOT NULL, "
                 "PRIMARY KEY(file, name, start_line))")
    conn.execute("INSERT INTO symbols(file, name, start_line) "
                 "VALUES ('a.py', 'alpha', 1)")
    conn.execute("UPDATE meta SET value = '6' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    print(f"    downgraded: schema_version={meta(legacy_db, 'schema_version')}, "
          f"symbol rows={scalar(legacy_db, 'SELECT COUNT(*) FROM symbols')[0]}, "
          f"symbol columns={symbol_columns(legacy_db)}")
    before = sha(legacy_db)
    print(f"    sha256 before: {before}")
    for name, argv in READS:
        r = run(env, legacy_project, *argv)
        after = sha(legacy_db)
        lines = [ln for ln in r.err.splitlines() if ln.strip()]
        ok_shape = (r.exit == 4 and r.out == "" and len(lines) == 1
                    and lines[0].startswith("RebuildRequiredError:")
                    and "reindex-project" in lines[0])
        print(f"  {name:16s} exit={r.exit} stdout={len(r.out)}B "
              f"stderr_lines={len(lines)} refused={ok_shape} "
              f"sha_same={before == after}")
        if r.exit == 4:
            for ln in lines[:2]:
                print("    ERR | " + ln)
        if not ok_shape:
            failures.append(f"FOLD-1b {name}: refusal shape wrong (exit={r.exit}, "
                            f"stdout={len(r.out)}B, lines={len(lines)})")
            show(r, "out")
        if before != after:
            failures.append(f"FOLD-1b {name}: pre-7 manifest bytes changed")
    print(f"    rows after every read: "
          f"{scalar(legacy_db, 'SELECT COUNT(*) FROM symbols')[0]} "
          f"(expected 1)")
    print(f"    recorded schema after every read: "
          f"{meta(legacy_db, 'schema_version')} (expected 6)")
    print(f"    symbol columns after every read: {symbol_columns(legacy_db)}")
    print(f"    byte-identical: {before == sha(legacy_db)}")
    if scalar(legacy_db, "SELECT COUNT(*) FROM symbols")[0] != 1:
        failures.append("FOLD-1b: the old-key symbol row did not survive the reads")
    if "decl_hash" in symbol_columns(legacy_db):
        failures.append("FOLD-1b: the symbols table was migrated by a read")
    if meta(legacy_db, "schema_version") != "6":
        failures.append("FOLD-1b: the recorded schema advanced during a read")
    r = run(env, legacy_project, "index-status")
    print("  index-status on the pre-7 manifest:")
    show(r)
    if "schema=6" not in r.out:
        failures.append("FOLD-1b: index-status did not print schema=6")

    # ----------------------------------------------------------------- FOLD-2
    print()
    print("=" * 72)
    print("FOLD-2  vectors-off + no reachable service: ConfigError naming --vectors on")
    print("=" * 72)
    r = run(env, project, "semantic-search", "alpha")
    show(r)
    ok = (r.exit == 4 and r.out == "" and r.err.startswith("ConfigError:")
          and "--vectors on" in r.err)
    print(f"    shape ok (exit 4, ConfigError, --vectors on, empty stdout): {ok}")
    if not ok:
        failures.append(f"FOLD-2: refusal shape wrong (exit={r.exit})")

    # ----------------------------------------------------------------- FOLD-4
    print()
    print("=" * 72)
    print("FOLD-4  a zero-chunk project reports rows=current after a completed pass")
    print("=" * 72)
    empty_project = os.path.join(root, "empty")
    os.makedirs(empty_project)
    open(os.path.join(empty_project, "empty.py"), "w").close()
    r = run(env, empty_project, "add-project", empty_project, "--vectors", "off")
    show(r)
    empty_slug = slug_of(state, empty_project)
    empty_db = manifest_db(state, empty_slug)
    chunks = scalar(empty_db, "SELECT COUNT(*) FROM chunks")[0]
    last_indexed = meta(empty_db, "last_indexed")
    print(f"    chunks stored={chunks}  last_indexed={last_indexed is not None}")
    r = run(env, empty_project, "index-status")
    show(r)
    if chunks != 0:
        failures.append(f"FOLD-4: the fixture was not zero-chunk (chunks={chunks})")
    if "rows=current" not in r.out:
        failures.append("FOLD-4: zero-chunk project did not report rows=current")

    print()
    print("=" * 72)
    print(f"RESULT: {'PASS' if not failures else 'FAIL'}")
    print("=" * 72)
    for f in failures:
        print("  FAIL | " + f)
    print(f"    workdir kept for inspection: {root}")
    if not failures:
        shutil.rmtree(root, ignore_errors=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
