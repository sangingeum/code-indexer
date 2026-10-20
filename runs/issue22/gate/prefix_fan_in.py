"""Bar 2 support: what a --path-prefix overview loses under a global
first-definition rule, versus the same question scoped to the prefix.

A = the shipped rule: first definition over the WHOLE store (MIN(file)), then
    keep only files under the prefix.
B = the same question scoped to the prefix: first definition among the prefix's
    own files, then count.

Usage: <python> prefix_fan_in.py SLUG PREFIX
"""

import sqlite3
import sys
from pathlib import Path

slug, prefix = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(
    f"file:{Path.home() / '.code-indexer' / slug / 'manifest.db'}?mode=ro",
    uri=True)


def total(sql: str, args: tuple = ()) -> int:
    return conn.execute(sql, args).fetchone()[0]


refs_total = total("SELECT COUNT(*) FROM refs")
a = total(
    "WITH first_def AS ("
    "  SELECT name, MIN(file) AS file FROM symbols GROUP BY name) "
    "SELECT COUNT(*) FROM refs r JOIN first_def s ON s.name = r.to_name"
    " WHERE r.file != s.file AND s.file LIKE ?", (prefix + "%",))
b = total(
    "WITH first_def AS ("
    "  SELECT name, file FROM ("
    "    SELECT name, file, ROW_NUMBER() OVER"
    "      (PARTITION BY name ORDER BY file, start_line) AS rn"
    "    FROM symbols WHERE file LIKE ?) WHERE rn = 1) "
    "SELECT COUNT(*) FROM refs r JOIN first_def s ON s.name = r.to_name"
    " WHERE r.file != s.file", (prefix + "%",))
names_global_in = total(
    "SELECT COUNT(DISTINCT name) FROM symbols WHERE file LIKE ?",
    (prefix + "%",))
print(f"{slug} prefix={prefix}")
print(f"refs_total={refs_total}")
print(f"A shipped rule (global first definition, then filter): {a}")
print(f"B prefix-scoped first definition:                     {b}")
print(f"rows lost by the shipped rule: {b - a}")
print(f"names defined under the prefix: {names_global_in}")
conn.close()
