"""Gate probe: compare the store reads' default cap on a manifest snapshot.

Usage: <python> probe_store_reads.py SRC_ROOT SLUG

Opens the live manifest READ-ONLY, takes a sqlite backup snapshot into a
scratch path, then imports code_indexer.manifest from SRC_ROOT and calls the
three store reads on the SNAPSHOT only, so the live index is never touched
(Manifest.__init__ runs migrations; a live connection would write).
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

INDEX_ROOT = Path.home() / ".code-indexer"
SCRATCH = Path(tempfile.gettempdir()) / "ci-gate-22"


def snapshot(slug: str) -> Path:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    src_path = INDEX_ROOT / slug / "manifest.db"
    dest = SCRATCH / f"snap-{slug}.db"
    for suffix in ("-wal", "-shm"):
        p = Path(str(dest) + suffix)
        if p.exists():
            p.unlink()
    if dest.exists():
        dest.unlink()
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(dest))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return dest


def main() -> None:
    root, slug = sys.argv[1], sys.argv[2]
    sys.path.insert(0, root)
    snap = snapshot(slug)
    ro = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)

    import code_indexer
    from code_indexer.manifest import Manifest

    print(f"src_root={root}")
    print(f"package={code_indexer.__file__}")
    print(f"slug={slug} snapshot={snap} size={snap.stat().st_size}")

    refs_total = ro.execute("SELECT COUNT(*) FROM refs WHERE to_name='pairA'"
                            ).fetchone()[0]
    imports_total = ro.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
    print(f"ground: refs(pairA)={refs_total} imports(all)={imports_total}")

    # callees: a (file, symbol) with more than 200 out-edges
    pair = ro.execute(
        "SELECT file, from_symbol, COUNT(*) n FROM refs"
        " WHERE from_symbol IS NOT NULL"
        " GROUP BY file, from_symbol HAVING n > 200"
        " ORDER BY n DESC LIMIT 1").fetchone()
    # and a whole file with more than 200 out-edges
    fpair = ro.execute(
        "SELECT file, COUNT(*) n FROM refs GROUP BY file"
        " HAVING n > 200 ORDER BY n DESC LIMIT 1").fetchone()
    print(f"ground: busiest (file,symbol)={pair} busiest file={fpair}")

    m = Manifest(str(snap))
    try:
        d = m.callers_of("pairA")
        c = m.callers_of("pairA", limit=200)
        print(f"callers_of('pairA') default={len(d)} limit=200={len(c)}")

        if pair:
            f, sym, n = pair
            d = m.callees_of_file_symbol(f, sym)
            c = m.callees_of_file_symbol(f, sym, limit=200)
            print(f"callees_of_file_symbol({f}, {sym}): ground={n}"
                  f" default={len(d)} limit=200={len(c)}")
        if fpair:
            f, n = fpair
            d = m.callees_of_file_symbol(f, None)
            c = m.callees_of_file_symbol(f, None, limit=200)
            print(f"callees_of_file_symbol({f}, None): ground={n}"
                  f" default={len(d)} limit=200={len(c)}")

        d = m.import_rows()
        print(f"import_rows() default={len(d)} ground(imports)={imports_total}")

        # a capped import read must be a deterministic, ordered prefix
        capped = [m.import_rows(limit=5) for _ in range(3)]
        same = all(capped[0] == c for c in capped[1:])
        keys = [(r["file"], r["line"]) for r in capped[0]]
        ordered = keys == sorted(keys)
        print(f"import_rows(limit=5) repeated_identical={same}"
              f" ordered_by_file_line={ordered}")
        print(f"  capped prefix: {keys}")
        print(f"  capped is prefix of whole={capped[0] == d[:5]}")
    finally:
        m.close()
        ro.close()


if __name__ == "__main__":
    main()
