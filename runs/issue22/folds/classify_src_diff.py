"""Classify the fold's src diff: comment / docstring / string only, no logic.

For every file that differs between BASE and HEAD under src/, parse both
versions and run two comparisons:

1. docstrings stripped — if the AST dumps match, the file changed only in
   comments and docstrings (comments never reach the AST at all).
2. docstrings stripped and every string literal's *value* flattened — if the
   dumps match at this level but not at (1), the only difference left is the
   text of a string literal, which is reported as the multiset difference of
   the literals.

Anything that survives both normalisations is an executable change and fails
the check.

Run: uv run python runs/issue22/folds/classify_src_diff.py [BASE]
"""
from __future__ import annotations

import ast
import collections
import pathlib
import subprocess
import sys

BASE = sys.argv[1] if len(sys.argv) > 1 else "79906a1"
REPO = pathlib.Path(__file__).resolve().parents[3]
DOC_HOLDERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if not isinstance(node, DOC_HOLDERS):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            node.body = body[1:]
    return tree


def flatten_strings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = "<str>"
    return tree


def literals(tree: ast.AST) -> collections.Counter:
    return collections.Counter(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and len(node.value) > 1
    )


def main() -> int:
    changed = subprocess.run(
        ["git", "diff", "--name-only", f"{BASE}..HEAD", "--", "src/"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    if not changed:
        print(f"no src/ changes in {BASE}..HEAD")
        return 0
    problems = 0
    for rel in changed:
        base_src = subprocess.run(
            ["git", "show", f"{BASE}:{rel}"],
            cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout
        head_src = (REPO / rel).read_text()
        base_tree = strip_docstrings(ast.parse(base_src))
        head_tree = strip_docstrings(ast.parse(head_src))
        docs_only = (ast.dump(base_tree, include_attributes=False)
                     == ast.dump(head_tree, include_attributes=False))
        if docs_only:
            print(f"{rel}: comments/docstrings only (code tree identical)")
            continue
        before = literals(strip_docstrings(ast.parse(base_src)))
        after = literals(strip_docstrings(ast.parse(head_src)))
        base_flat = ast.dump(
            flatten_strings(strip_docstrings(ast.parse(base_src))),
            include_attributes=False)
        head_flat = ast.dump(
            flatten_strings(strip_docstrings(ast.parse(head_src))),
            include_attributes=False)
        if base_flat != head_flat:
            problems += 1
            print(f"{rel}: EXECUTABLE DIFFERENCE beyond docstrings and string "
                  f"literals — not allowed by this leg")
            continue
        before = literals(base_tree)
        after = literals(head_tree)
        print(f"{rel}: string literal text only (code tree identical)")
        for value in (before - after).elements():
            print(f"  - {value!r}")
        for value in (after - before).elements():
            print(f"  + {value!r}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
