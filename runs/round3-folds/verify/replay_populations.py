"""Replay the round record's populations against the live index, read-only.

Opens the manifest with ``mode=ro`` so the replay cannot write, and prints
the revision it read beside every population so a reader can see whether the
record's numbers still describe the index in front of them.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path.home() / ".code-indexer" / "code-indexer" / "manifest.db"

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
# One read transaction, so every population below is the same snapshot even
# if the standing watcher commits while this script runs.
conn.execute("BEGIN")


def one(sql: str, *args):
    return conn.execute(sql, args).fetchone()[0]


revision = one("SELECT value FROM meta WHERE key = 'index_revision'")
last = one("SELECT value FROM meta WHERE key = 'last_indexed'")
print(f"index_revision = {revision}")
print(f"last_indexed = {last}")
print(f"symbols = {one('SELECT COUNT(*) FROM symbols')}")
print("ledger (source, verdict, rows, label):")
for source, verdict, n in conn.execute(
        "SELECT s.source, COALESCE(f.parse_verdict, '<none>'), COUNT(*) "
        "FROM symbols s LEFT JOIN files f ON f.path = s.file "
        "GROUP BY 1, 2 ORDER BY 3 DESC"):
    label = ("ast" if verdict == "parsed" and source == "ast" else
             "heuristic" if verdict == "parsed" else "unknown")
    print(f"    {label}: {verdict}/{source} = {n}")
print("parse verdicts:")
for verdict, n in conn.execute(
        "SELECT parse_verdict, COUNT(*) FROM files GROUP BY 1 ORDER BY 1"):
    print(f"    {verdict} = {n}")
print("files holding symbols without an AST extractor = "
      f"{one('SELECT COUNT(DISTINCT s.file) FROM symbols s LEFT JOIN files f ON f.path = s.file WHERE f.parse_verdict IS NULL OR f.parse_verdict <> ?', 'parsed')}")
print("populations the record quotes:")
print("    kind_class = "
      f"{one('SELECT COUNT(*) FROM symbols WHERE symbol_type = ?', 'class')}")
print("    container_Manifest = "
      f"{one('SELECT COUNT(*) FROM symbols WHERE container = ?', 'Manifest')}")
print("    visibility_private = "
      f"{one('SELECT COUNT(*) FROM symbols WHERE visibility = ?', 'private')}")
print("    language_markdown = "
      f"{one('SELECT COUNT(*) FROM symbols WHERE language = ? COLLATE NOCASE', 'markdown')}")
print("    language_python = "
      f"{one('SELECT COUNT(*) FROM symbols WHERE language = ? COLLATE NOCASE', 'python')}")
print("    has_call_find_symbols = "
      f"{one('SELECT COUNT(*) FROM symbols s WHERE EXISTS (SELECT 1 FROM refs r WHERE r.relationship = ? AND r.file = s.file AND r.from_symbol = s.name AND r.to_name = ?)', 'call', 'find_symbols')}")
print("    inherits_IndexerError = "
      f"{one('SELECT COUNT(*) FROM symbols s WHERE EXISTS (SELECT 1 FROM refs r WHERE r.relationship = ? AND r.file = s.file AND r.from_symbol = s.name AND r.to_name = ?)', 'base_class', 'IndexerError')}")
