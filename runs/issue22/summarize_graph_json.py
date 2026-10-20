"""Summarise a find-callers/find-callees JSON capture: root count,
truncation flags, and the notes.

    python runs/issue22/summarize_graph_json.py FILE.json [...]
"""
import json
import sys


def summarise(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    roots = payload.get("roots", [])
    lines = [
        f"{path}",
        f"  roots: {len(roots)}",
        f"  truncated: {payload.get('truncated')}",
        f"  omitted: {payload.get('omitted')}",
        f"  notes: {payload.get('notes')}",
    ]
    return "\n".join(lines)


for arg in sys.argv[1:]:
    print(summarise(arg))
