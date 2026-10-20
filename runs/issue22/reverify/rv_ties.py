"""Bar 4 probe: are the capped reads' order keys total, and does it matter?

(a) live tie counts on the three indexes, counted independently of the round's
    own probe (same predicate, fresh SQL);
(b) a fixture built on a COPY of the code-indexer manifest: tie groups whose
    rows were inserted in the REVERSE of the key order, so a capped read that
    inherits insertion order is visibly wrong, and a cut that falls inside a
    tie group;
(c) the uncapped consumers: the whole read, and the aggregate that carries no
    order clause at all.
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..",
                                "src"))
from code_indexer.manifest import Manifest  # noqa: E402

INDEX_ROOT = os.path.expanduser("~/.code-indexer")
WORK = os.path.join(tempfile.gettempdir(), "ci22-rv")


def snapshot(slug: str, name: str) -> str:
    os.makedirs(WORK, exist_ok=True)
    dst = os.path.join(WORK, name)
    for suffix in ("-wal", "-shm"):
        if os.path.exists(dst + suffix):
            os.remove(dst + suffix)
    src = sqlite3.connect(
        f"file:{os.path.join(INDEX_ROOT, slug, 'manifest.db')}?mode=ro",
        uri=True)
    out = sqlite3.connect(dst)
    src.backup(out)
    out.close()
    src.close()
    return dst


def live_ties() -> None:
    print("### (a) tie groups on the live indexes — (file, line) with >1 row")
    for slug in ("vivarium-sim", "b1b7f48b", "code-indexer"):
        path = snapshot(slug, f"ties-{slug}.manifest.db")
        conn = sqlite3.connect(path)
        for table in ("refs", "imports"):
            groups, rows = conn.execute(
                f"SELECT COUNT(*), COALESCE(SUM(n), 0) FROM ("
                f" SELECT COUNT(*) AS n FROM {table}"
                f" GROUP BY file, line HAVING COUNT(*) > 1)").fetchone()
            total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            biggest = conn.execute(
                f"SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM {table}"
                f" GROUP BY file, line)").fetchone()[0]
            print(f"{slug:14s} {table:8s} tie_groups={groups:6d}"
                  f" rows_in_ties={rows:6d} rows={total:7d}"
                  f" largest_group={biggest}")
        conn.close()


def fixture() -> None:
    print("\n### (b) fixture: tie rows inserted in REVERSE key order")
    path = snapshot("code-indexer", "tie-fixture.manifest.db")
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO symbols (file, name, symbol_type, start_line,"
                 " end_line, source) VALUES"
                 " ('zz_owner.py','tie_target','function',1,9,'ast')")
    for rel in ("member", "identifier", "call"):   # worst-first insertion
        conn.execute("INSERT INTO refs (file, line, from_symbol, to_name,"
                     " relationship, confidence, receiver) VALUES"
                     " ('zz_tie_probe.py', 7, 'probe', 'tie_target', ?,"
                     " 'ast', NULL)", (rel,))
    # 'aa_' so the tie group sorts at the HEAD of the table: a cap of 1 then
    # lands inside it, and insertion order would answer 'pkg.zeta'.
    for target in ("pkg.zeta", "pkg.alpha"):
        conn.execute("INSERT INTO imports (file, raw_target, resolved_file,"
                     " kind, line) VALUES ('AA_import_probe.py', ?, NULL,"
                     " 'import', 7)", (target,))
    conn.commit()
    conn.close()

    m = Manifest(path)
    try:
        whole = m.callers_of("tie_target")
        print("callers_of() uncapped -> "
              f"{[(r['line'], r['relationship']) for r in whole]}")
        for cap in (1, 2, 3):
            reads = {tuple((r["line"], r["relationship"])
                           for r in m.callers_of("tie_target", limit=cap))
                     for _ in range(5)}
            first = sorted(reads)[0]
            prefix = tuple((r["line"], r["relationship"]) for r in whole[:cap])
            print(f"callers_of(limit={cap}) x5 distinct_results="
                  f"{len(reads)} rows={first}"
                  f" is_ordered_prefix_of_whole={first == prefix}"
                  f" cut_inside_tie_group={len({l for l, _ in first}) < cap}")
        imp_all = [(r["file"], r["raw_target"]) for r in m.import_rows()]
        imp_head = imp_all[:2]
        caps = {tuple((r["file"], r["raw_target"])
                      for r in m.import_rows(limit=1))
                for _ in range(5)}
        print(f"import_rows() uncapped head -> {imp_head}")
        print(f"import_rows(limit=1) x5 distinct_results={len(caps)}"
              f" rows={sorted(caps)[0]}"
              f" is_ordered_prefix_of_whole={sorted(caps)[0] == tuple(imp_head[:1])}")
        print("  (head rows are one (file, line) tie group inserted zeta-first;"
              " the key answers alpha, insertion order would answer zeta)")
        print("\n### (c) the uncapped consumers are order-blind aggregates")
        first = dict(m.file_fan_in())
        second = dict(m.file_fan_in())
        print(f"file_fan_in() twice identical={first == second}"
              f" zz_owner.py fan_in={first.get('zz_owner.py')}"
              " (3 inserted non-self ref rows)")
        print(f"file_fan_in() total={sum(n for _f, n in m.file_fan_in())} — a"
              " GROUP BY aggregate over every row, no ORDER BY and no LIMIT")
    finally:
        m.close()


if __name__ == "__main__":
    live_ties()
    fixture()
