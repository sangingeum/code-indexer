"""Second probe: prefix-scoped fan-in damage and symbols/files set relation."""
import os
import sqlite3

ROOT = os.path.expanduser("~/.code-indexer")

for slug, prefix in (("vivarium-sim", "docs/"), ("b1b7f48b", "docs/")):
    path = os.path.join(ROOT, slug, "manifest.db")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    print(f"=== {slug} (prefix {prefix!r}) ===")
    orphan_syms = con.execute(
        "SELECT COUNT(*) FROM symbols WHERE file NOT IN (SELECT path FROM files)"
    ).fetchone()[0]
    print(f"-- symbol rows whose file is not in files: {orphan_syms}")

    # refs rows that the OLD rule (first definition among in-prefix files)
    # would attribute inside the prefix, but the NEW rule (global MIN(file))
    # attributes outside it and then drops on the `f in files` filter.
    row = con.execute(
        "WITH m AS (SELECT name, MIN(file) AS file FROM symbols GROUP BY name),"
        " inside AS (SELECT name, MIN(file) AS file FROM symbols"
        "   WHERE file LIKE ? || '%' GROUP BY name)"
        " SELECT COUNT(*) FROM refs r JOIN m ON r.to_name = m.name"
        " JOIN inside i ON i.name = m.name"
        " WHERE r.file != m.file AND m.file NOT LIKE ? || '%'",
        (prefix, prefix)).fetchone()
    print(f"-- refs rows lost under the prefix (new rule vs old): {row[0]}")

    total = con.execute("SELECT COUNT(*) FROM refs").fetchone()[0]
    print(f"-- refs total: {total}")
    con.close()
