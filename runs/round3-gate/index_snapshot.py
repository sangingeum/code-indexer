"""Read-only snapshot of the live index the gate ruled against.

Opens the manifest with ``mode=ro`` so the snapshot cannot write, and prints
the populations every claim in the record is drawn from.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path.home() / ".code-indexer" / "code-indexer" / "manifest.db"

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
try:
    def one(sql: str, *args):
        return conn.execute(sql, args).fetchone()[0]

    print("meta (chunk_hashes elided):")
    for key, value in conn.execute(
            "SELECT key, value FROM meta WHERE key <> 'chunk_hashes' ORDER BY key"):
        print(f"    {key} = {value}")
    print("populations:")
    print(f"    symbols = {one('SELECT COUNT(*) FROM symbols')}")
    print("    distinct_names = "
          f"{one('SELECT COUNT(DISTINCT name) FROM symbols')}")
    print("    duplicated_names = "
          f"{one('SELECT COUNT(*) FROM (SELECT name FROM symbols GROUP BY name HAVING COUNT(*) > 1)')}")
    print("    rows_under_duplicated_names = "
          f"{one('SELECT COALESCE(SUM(n), 0) FROM (SELECT COUNT(*) AS n FROM symbols GROUP BY name HAVING COUNT(*) > 1)')}")
    print("    files_holding_symbols_without_ast = "
          f"{one('SELECT COUNT(DISTINCT s.file) FROM symbols s LEFT JOIN files f ON f.path = s.file WHERE f.parse_verdict IS NULL OR f.parse_verdict <> ?', 'parsed')}")
    print("    refs = "
          f"{one('SELECT COUNT(*) FROM refs')}")
    print("parse verdicts:")
    for verdict, n in conn.execute(
            "SELECT parse_verdict, COUNT(*) FROM files GROUP BY 1 ORDER BY 1"):
        print(f"    {verdict} = {n}")
    print("refs by relationship/confidence:")
    for rel, conf, n, attributed in conn.execute(
            "SELECT relationship, confidence, COUNT(*), SUM(from_symbol IS NOT NULL) "
            "FROM refs GROUP BY 1, 2 ORDER BY 3 DESC"):
        print(f"    {rel}/{conf} = {n} (attributed {attributed})")
    print("evidence over all symbol rows:")
    for verdict, source, n in conn.execute(
            "SELECT COALESCE(f.parse_verdict, '<none>') AS verdict, s.source, COUNT(*) "
            "FROM symbols s LEFT JOIN files f ON f.path = s.file "
            "GROUP BY 1, 2 ORDER BY 3 DESC"):
        label = ("ast" if verdict == "parsed" and source == "ast" else
                 "heuristic" if verdict == "parsed" else "unknown")
        print(f"    {label}: {verdict}/{source} = {n}")
    gate = "runs/round3-gate/%"
    print("repository-only populations (this gate's own files excluded):")
    print("    symbols = "
          f"{one('SELECT COUNT(*) FROM symbols WHERE file NOT LIKE ?', gate)}")
    print("    files_holding_symbols_without_ast = "
          f"{one('SELECT COUNT(DISTINCT s.file) FROM symbols s LEFT JOIN files f ON f.path = s.file WHERE (f.parse_verdict IS NULL OR f.parse_verdict <> ?) AND s.file NOT LIKE ?', 'parsed', gate)}")
    print("    language_python = "
          f"{one('SELECT COUNT(*) FROM symbols WHERE language = ? COLLATE NOCASE AND file NOT LIKE ?', 'python', gate)}")
    print("    kind_class = "
          f"{one('SELECT COUNT(*) FROM symbols WHERE symbol_type = ? AND file NOT LIKE ?', 'class', gate)}")
    print("    visibility_private = "
          f"{one('SELECT COUNT(*) FROM symbols WHERE visibility = ? AND file NOT LIKE ?', 'private', gate)}")
    print("    language_markdown = "
          f"{one('SELECT COUNT(*) FROM symbols WHERE language = ? COLLATE NOCASE AND file NOT LIKE ?', 'markdown', gate)}")
    print("files named by the gate's own captures (excluded from any count claim):")
    gate_rows = 0
    for file, n in conn.execute(
            "SELECT file, COUNT(*) FROM symbols "
            "WHERE file LIKE 'runs/round3-gate/%' GROUP BY file ORDER BY file"):
        print(f"    {file} = {n}")
        gate_rows += n
    print(f"    gate_files_total = {gate_rows}")
    print("    repository_only_symbols = "
          f"{one('SELECT COUNT(*) FROM symbols') - gate_rows}")
finally:
    conn.close()
