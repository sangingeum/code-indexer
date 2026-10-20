"""Read-only query-plan and semantic probes for the issue #22 review."""
import os
import sqlite3

ROOT = os.path.expanduser("~/.code-indexer")

REF_AGG = ("SELECT s.file, COUNT(*) FROM refs r JOIN"
           " (SELECT name, MIN(file) AS file FROM symbols GROUP BY name) s"
           " ON s.name = r.to_name WHERE r.file != s.file"
           " GROUP BY s.file")
IMP_AGG = ("SELECT resolved_file, COUNT(*) FROM imports"
           " WHERE resolved_file IS NOT NULL AND resolved_file != file"
           " GROUP BY resolved_file")
CALLERS = ("SELECT file, line, from_symbol, relationship, confidence, receiver"
           " FROM refs WHERE to_name = ? ORDER BY file, line")
IMPORTS = ("SELECT file, raw_target, resolved_file, kind, line"
           " FROM imports ORDER BY file, line")


def probes(slug, prefix):
    path = os.path.join(ROOT, slug, "manifest.db")
    if not os.path.exists(path):
        print(f"{slug}: MISSING {path}")
        return
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    print(f"=== {slug} ===")
    for label, sql, args in (("refs aggregate", REF_AGG, ()),
                             ("imports aggregate", IMP_AGG, ()),
                             ("callers_of", CALLERS, ("pairA",)),
                             ("import_rows", IMPORTS, ())):
        print(f"-- plan: {label}")
        for row in con.execute("EXPLAIN QUERY PLAN " + sql, args):
            print("   ", row[1:])
    n_refs = con.execute("SELECT COUNT(*) FROM refs").fetchone()[0]
    n_imp = con.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
    n_sym = con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    n_files = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    print(f"-- rows: refs={n_refs} imports={n_imp} symbols={n_sym} files={n_files}")

    # names defined in more than one file
    multi = con.execute(
        "SELECT COUNT(*) FROM (SELECT name FROM symbols"
        " GROUP BY name HAVING COUNT(DISTINCT file) > 1)").fetchone()[0]
    print(f"-- names defined in >1 file: {multi}")

    # how many names whose MIN(file) sits OUTSIDE a prefix while another
    # definition sits INSIDE it (the prefix-scoped overview mechanism)
    row = con.execute(
        "WITH d AS (SELECT name, file FROM symbols),"
        " m AS (SELECT name, MIN(file) AS file FROM symbols GROUP BY name)"
        " SELECT COUNT(*) FROM m WHERE m.file NOT LIKE ? || '%' AND EXISTS"
        " (SELECT 1 FROM d WHERE d.name = m.name AND d.file LIKE ? || '%')",
        (prefix, prefix)).fetchone()
    print(f"-- names first-defined outside {prefix!r} but also defined inside: {row[0]}")

    # tie groups in the ordering keys (rows sharing the ORDER BY key)
    ties_imp = con.execute(
        "SELECT COUNT(*) FROM (SELECT file, line FROM imports"
        " GROUP BY file, line HAVING COUNT(*) > 1)").fetchone()[0]
    ties_ref = con.execute(
        "SELECT COUNT(*) FROM (SELECT file, line FROM refs"
        " GROUP BY file, line HAVING COUNT(*) > 1)").fetchone()[0]
    print(f"-- duplicate (file,line) groups: imports={ties_imp} refs={ties_ref}")

    # floats for the shares of refs rows the old sample covered
    share = con.execute(
        "SELECT COUNT(*) FROM refs WHERE to_name = 'the'").fetchone()[0]
    print(f"-- refs to 'the': {share}")
    con.close()


for slug in ("vivarium-sim", "b1b7f48b"):
    probes(slug, "docs/")
