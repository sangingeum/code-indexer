"""Diff the compact rendering of the six commands between two trees.

Usage: python compact_diff.py <bodiesA> <bodiesB> <labelA> <labelB>

Reports raw byte equality and masked equality; the mask covers only the two
cells the payload cannot hold still across runs (the pass timestamp and the
throughput rate). A renamed label would show up in the masked diff as well.
"""

from __future__ import annotations

import os
import re
import sys

A, B, LA, LB = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
MASK = os.environ.get("MASK", "")
VOLATILE = [
    (re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"), "<timestamp>"),
    (re.compile(r"\b\d+(?:\.\d+)? chunks/s"), "<rate> chunks/s"),
]


def norm(text: str) -> str:
    if MASK:
        text = text.replace(MASK, "<rig>")
    for pattern, repl in VOLATILE:
        text = pattern.sub(repl, text)
    return text


print("command\tbytesA\tbytesB\traw_equal\tmasked_equal")
for command in ("overview", "index-status", "skeleton", "outline",
                "find-symbol", "find-definition"):
    pa = os.path.join(A, f"{LA}-{command}-format-compact.out")
    pb = os.path.join(B, f"{LB}-{command}-format-compact.out")
    if not (os.path.exists(pa) and os.path.exists(pb)):
        print(f"{command}\t-\t-\tMISSING\tMISSING")
        continue
    ra = open(pa, encoding="utf-8").read()
    rb = open(pb, encoding="utf-8").read()
    print("\t".join([
        command, str(len(ra.encode())), str(len(rb.encode())),
        "yes" if ra == rb else "no",
        "yes" if norm(ra) == norm(rb) else "NO",
    ]))
