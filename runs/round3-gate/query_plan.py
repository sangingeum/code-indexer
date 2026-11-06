"""Print SQLite's plan for each structural-filter read, and time a few calls.

Diagnostics only: opens the live manifest read-only and asks the store what
plan it uses for each clause. No index is added, nothing is written.
"""
from __future__ import annotations

from pathlib import Path

from code_indexer.manifest import Manifest

DB = Path.home() / ".code-indexer" / "code-indexer" / "manifest.db"

PLANS = [
    ("name exact (Core)", dict(name="Core")),
    ("prefix GLOB (Core*)", dict(name_pattern="Core*")),
    ("suffix GLOB (*Manager)", dict(name_pattern="*Manager")),
    ("file filter", dict(file="src/code_indexer/manifest.py")),
    ("container filter", dict(container="Manifest")),
    ("language filter", dict(language="markdown")),
    ("kind filter", dict(symbol_type="class")),
    ("visibility filter", dict(visibility="private")),
    ("inherits filter", dict(inherits="IndexerError")),
    ("has-call filter", dict(has_call="find_symbols")),
    ("browse, no filter", dict()),
]

m = Manifest(str(DB), writable=False)
try:
    for label, kwargs in PLANS:
        print(label)
        for line in m.symbol_query_plan(**kwargs):
            print("    " + line)
finally:
    m.close()
