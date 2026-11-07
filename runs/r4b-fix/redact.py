#!/usr/bin/env python3
"""Replace machine-specific prefixes with published placeholders.

Usage: text | redact.py "<placeholder>=/host/path" ...

Longest value first, so a nested path is replaced before the directory that
contains it. Stdout only; nothing is written back to the source.
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    pairs: list[tuple[str, str]] = []
    for arg in argv:
        placeholder, sep, value = arg.partition("=")
        if not sep or not placeholder or not value:
            print(f"redact: expected <placeholder>=<value>, got {arg!r}",
                  file=sys.stderr)
            return 2
        pairs.append((placeholder, value))
    data = sys.stdin.read()
    for placeholder, value in sorted(pairs, key=lambda p: len(p[1]),
                                     reverse=True):
        data = data.replace(value, placeholder)
    sys.stdout.write(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
