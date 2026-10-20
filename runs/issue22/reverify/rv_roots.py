"""Bar 1 probe: the definition-root read, read directly from the store.

Opens each live manifest through a read-only SQLite connection and copies it
with the backup API, so no live index is ever opened for write. Reports the
definition rows for a name straight from SQL, then what each manifest read
returns at the three limits that matter.

usage: rv_roots.py NAME SLUG [SLUG ...]
"""
import os
import sqlite3
import sys
import tempfile

INDEX_ROOT = os.path.expanduser("~/.code-indexer")

sys.path.insert(0, os.environ.get("CI_SRC",
                                  os.path.join(os.path.dirname(__file__),
                                               "..", "..", "..", "src")))
import code_indexer  # noqa: E402
from code_indexer.manifest import Manifest  # noqa: E402
_SRC_PARENT = os.path.dirname(os.path.abspath(os.environ.get(
    "CI_SRC", os.path.join(os.path.dirname(__file__), "..", "..", "..",
                           "src"))))


def snapshot(slug: str) -> str:
    live = os.path.join(INDEX_ROOT, slug, "manifest.db")
    dst_dir = os.path.join(tempfile.gettempdir(), "ci22-rv")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, f"{slug}.manifest.db")
    for suffix in ("-wal", "-shm"):
        if os.path.exists(dst + suffix):
            os.remove(dst + suffix)
    src = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    out = sqlite3.connect(dst)
    src.backup(out)
    out.close()
    src.close()
    return dst


def main() -> int:
    name = sys.argv[1]
    slugs = sys.argv[2:]
    print("code_indexer =",
          os.path.relpath(os.path.abspath(code_indexer.__file__), _SRC_PARENT))
    for slug in slugs:
        path = snapshot(slug)
        raw = sqlite3.connect(path)
        rows = raw.execute(
            "SELECT file, COUNT(*), MIN(start_line) FROM symbols"
            " WHERE name = ? GROUP BY file ORDER BY file", (name,)).fetchall()
        total = raw.execute("SELECT COUNT(*) FROM symbols WHERE name = ?",
                            (name,)).fetchone()[0]
        print(f"\n### {slug}")
        print(f"symbols where name={name!r}: rows={total} files={len(rows)}")
        raw.close()

        m = Manifest(path)
        try:
            d = m.definitions_of(name)
            print(f"definitions_of          -> {len(d)} rows,"
                  f" {len({r.file for r in d})} files")
            s100 = m.find_symbols(name, substring=False, limit=100)
            print(f"find_symbols(limit=100) -> {len(s100)} rows")
            s25 = m.find_symbols(name, substring=False)
            print(f"find_symbols(default)   -> {len(s25)} rows (browse-path default)")
            sall = m.find_symbols(name, substring=False, limit=None)
            print(f"find_symbols(limit=None)-> {len(sall)} rows")
            key = lambda r: (r.file, r.start_line)  # noqa: E731
            print(f"definitions_of == find_symbols(limit=None): "
                  f"{[key(r) for r in d] == [key(r) for r in sall]}")
            print(f"first 3 / last 3 by (file,start_line): "
                  f"{[key(r) for r in d[:3]]} ... {[key(r) for r in d[-3:]]}")
        finally:
            m.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
