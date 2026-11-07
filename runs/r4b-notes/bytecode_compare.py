"""Compare compiled code objects between the base dump and the tip source.

A docstring edit must not move a single bytecode or constant: this prints the
per-function comparison so the round can claim "no behavioural change" from
something stronger than a read of the diff.
"""
from __future__ import annotations

import types
from pathlib import Path


def load(path: str, name: str):
    src = Path(path).read_text(encoding="utf-8")
    return compile(src, name, "exec")


def scalars(code: types.CodeType) -> tuple:
    """Constants with the leading docstring left out: a docstring edit is the
    one change this comparison is meant to tolerate."""
    cs = [c for c in code.co_consts if not isinstance(c, types.CodeType)]
    if cs and isinstance(cs[0], str):
        cs = cs[1:]
    return tuple(cs)


def walk(code: types.CodeType, prefix: str = "") -> dict:
    out = {prefix or "<module>": (code.co_code, scalars(code))}
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            out.update(walk(c, prefix + "." + c.co_name if prefix else c.co_name))
    return out


base = walk(load("runs/r4b-notes/staleness-base.py", "base"))
tip = walk(load("src/code_indexer/staleness.py", "tip"))
print(f"code objects: base={len(base)} tip={len(tip)}")
differing = [k for k in base if k not in tip or base[k] != tip[k]]
print("code objects differing:", differing or "none")
print("warning_line bytecode equal:",
      base["warning_line"][0] == tip["warning_line"][0])
print("warning_line non-docstring constants equal:",
      base["warning_line"][1] == tip["warning_line"][1])
