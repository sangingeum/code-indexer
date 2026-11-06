"""FOLD-5 evidence: the last-resort declaration hash carries no line number.

Calls the real ``Manifest._symbol_row_tuple`` with rows that have neither a
decl_hash nor a signature (the only rows that reach the fallback), then shows
the resulting decl_hash for the same declaration observed at two different
lines and for a different declaration.

Usage: uv run python runs/round2-reverify/probe_line_free_hash.py
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "src"))

from code_indexer.manifest import Manifest, SymbolRow  # noqa: E402
from code_indexer.chunker import declaration_hash  # noqa: E402


def row(name, kind, start, end, signature=None, decl_hash=""):
    return SymbolRow(
        file="a.py", name=name, symbol_type=kind, start_line=start,
        end_line=end, source="ast", signature=signature, visibility=None,
        qualified_name=None, container=None, language="python",
        decl_hash=decl_hash)


def decl(symbol):
    tup = Manifest._symbol_row_tuple("a.py", symbol, 1)
    return tup[3]


first = row("alpha", "function", 10, 12)
moved = row("alpha", "function", 99, 120)
other = row("beta", "function", 10, 12)
kind = row("alpha", "class", 10, 12)
with_sig = row("alpha", "function", 10, 12, signature="def alpha():")

checks = [
    ("same declaration, lines 10-12 vs 99-120 -> same hash",
     decl(first) == decl(moved), True),
    ("different name -> different hash", decl(first) != decl(other), True),
    ("different kind -> different hash", decl(first) != decl(kind), True),
    ("a signature takes precedence over the fallback",
     decl(with_sig) != decl(first), True),
    ("the basis is recomputable from name and kind alone",
     decl(first) == declaration_hash("alpha (function)"), True),
]

print("=" * 72)
print("FOLD-5  the last-resort declaration hash is line-free")
print("=" * 72)
print(f"    decl_hash(alpha function 10-12) = {decl(first)}")
print(f"    decl_hash(alpha function 99-120) = {decl(moved)}")
print(f"    decl_hash(beta function 10-12)   = {decl(other)}")
print(f"    decl_hash(alpha class 10-12)     = {decl(kind)}")
failures = [name for name, ok, _ in checks if not ok]
for name, ok, _ in checks:
    print(f"    {'ok  ' if ok else 'FAIL'} {name}")
print()
print(f"RESULT: {'PASS' if not failures else 'FAIL'}")
sys.exit(0 if not failures else 1)
