"""Prefix-scope damage, measured two ways at once, per live index.

For each (index, prefix):

* rows_before — the fan-in rows the prefix's files receive when the owner of a
  name is its FIRST definition over the whole store (the shipped aggregate),
  summed over the files under the prefix;
* rows_after — the same sum when the owner is the first definition AMONG the
  prefix's files (what `overview --path-prefix` must report);
* names_affected — names defined both under the prefix and outside it.

Reads the manifests read-only. No project is written.
"""
import os
import sqlite3

ROOT = os.path.expanduser("~/.code-indexer")

CASES = (
    ("vivarium-sim", "docs/"),
    ("vivarium-sim", "docs/verification"),
    ("b1b7f48b", "docs/"),
)

OLD_OWNERS = "SELECT name, MIN(file) AS file FROM symbols GROUP BY name"
NEW_OWNERS = ("SELECT name, MIN(file) AS file FROM symbols"
              " WHERE substr(file, 1, ?) = ? GROUP BY name")


def scalar(con, sql, args):
    return con.execute(sql, args).fetchone()[0]


def main() -> None:
    for slug, prefix in CASES:
        path = os.path.join(ROOT, slug, "manifest.db")
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        before = scalar(
            con,
            "WITH m AS (" + OLD_OWNERS + ")"
            " SELECT COUNT(*) FROM refs r JOIN m ON r.to_name = m.name"
            " WHERE r.file != m.file AND substr(m.file, 1, ?) = ?",
            (len(prefix), prefix))
        after = scalar(
            con,
            "WITH m AS (" + NEW_OWNERS + ")"
            " SELECT COUNT(*) FROM refs r JOIN m ON r.to_name = m.name"
            " WHERE r.file != m.file",
            (len(prefix), prefix))
        affected = scalar(
            con,
            "WITH outside AS (" + OLD_OWNERS + "),"
            " inside AS (" + NEW_OWNERS + ")"
            " SELECT COUNT(*) FROM inside i JOIN outside o ON o.name = i.name"
            " WHERE o.file != i.file",
            (len(prefix), prefix))
        total_refs = scalar(con, "SELECT COUNT(*) FROM refs", ())
        print(f"=== {slug}  prefix {prefix!r} ===")
        print(f"  ref rows under the prefix, before: {before}")
        print(f"  ref rows under the prefix, after:  {after}")
        print(f"  rows recovered:                    {after - before}")
        print(f"  names defined inside AND outside:  {affected}")
        print(f"  refs table total:                  {total_refs}")
        con.close()


if __name__ == "__main__":
    main()
