"""Collect the disagreement message each of the measured commands prints.

Usage: python conflict_messages.py <bodies dir>

One line per (command, spelling): the stderr line count, the message itself and
the stdout byte count, so "one message, empty stdout" is a measurement.
"""

from __future__ import annotations

import os
import sys

bodies = sys.argv[1]
commands = ["overview", "find-symbol", "find-definition", "semantic-search",
            "skeleton", "outline", "index-status"]
print("command\tspelling\tstderr_lines\tstdout_bytes\tmessage")
for command in commands:
    for spelling in ("json+format-text", "json+format-compact"):
        err_path = os.path.join(bodies, f"tip-{command}-{spelling}.err")
        out_path = os.path.join(bodies, f"tip-{command}-{spelling}.out")
        text = open(err_path, encoding="utf-8").read()
        out = open(out_path, encoding="utf-8").read()
        lines = [line for line in text.splitlines() if line.strip()]
        print("\t".join([command, spelling, str(len(lines)),
                         str(len(out.encode())),
                         (lines[0] if lines else "-")]))
