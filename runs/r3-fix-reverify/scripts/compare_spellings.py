"""Compare two capture trees' stdout bodies for the unchanged spellings.

Usage: python compare_spellings.py <dirA> <dirB> <labelA> <labelB>

Payloads carry two volatile fields by construction (the `last_indexed`
timestamp of the run, and the throughput rate in `index-status`), so equality
is judged after masking those two and the absolute rig path. Masking is
reported line by line: a row is EQUAL only when the masked bodies are byte-
identical.
"""

from __future__ import annotations

import os
import re
import sys

A, B, LA, LB = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
MASK = os.environ.get("MASK", "")

VOLATILE = [
    (re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"), "<timestamp>"),
    (re.compile(r"\d+\.\d+ chunks/s"), "<rate> chunks/s"),
    (re.compile(r"\"(docs_per_s|eta_s|duration_s|chunks_per_s)\": [0-9.]+"),
     "\"<rate-field>\""),
]


def norm(text: str) -> str:
    if MASK:
        text = text.replace(MASK, "<rig>")
    for pattern, repl in VOLATILE:
        text = pattern.sub(repl, text)
    return text


COMMANDS = ["overview", "find-symbol", "find-definition", "semantic-search",
            "skeleton", "outline", "index-status"]
SPELLINGS = ["none", "json", "format-json", "format-text", "format-compact",
             "json+format-json"]

print(f"{LA} vs {LB}: masked stdout comparison, volatile cells replaced with <timestamp>/<rate> at byte level")
print("command\tspelling\tbytesA\tbytesB\tmasked_equal")
for command in COMMANDS:
    for spelling in SPELLINGS:
        pa = os.path.join(A, f"{LA}-{command}-{spelling}.out")
        pb = os.path.join(B, f"{LB}-{command}-{spelling}.out")
        if not (os.path.exists(pa) and os.path.exists(pb)):
            print(f"{command}\t{spelling}\t-\t-\tMISSING")
            continue
        ra = open(pa, encoding="utf-8").read()
        rb = open(pb, encoding="utf-8").read()
        same = norm(ra) == norm(rb)
        print(f"{command}\t{spelling}\t{len(ra.encode())}\t{len(rb.encode())}\t"
              f"{'yes' if same else 'NO'}")
