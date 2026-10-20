"""Bar 5 support: recompute the per-name bound of changed-symbols' impact.

For one changed file's impact entry, compare the payload against the store:
  * len(callers) must equal the rows the per-name reads hand over
    (sum(min(population, CALLERS_PER_NAME)));
  * omitted must equal population - len(callers)  (the refusals counted), so
    the claim is complete for the bounds it names, not merely non-zero.

Usage: <python> check_changed_bounds.py SLUG CHANGED_JSON IMPACT_JSON FILE
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

INDEX_ROOT = Path.home() / ".code-indexer"
PER_NAME = 50  # CALLERS_PER_NAME in changed.py


def main() -> None:
    slug, changed_json, impact_json, target = sys.argv[1:5]
    changed = json.loads(Path(changed_json).read_text())
    impact = json.loads(Path(impact_json).read_text())["impact"]
    names = [s["name"] for s in changed.get(target, {}).get("symbols", [])
             if s.get("name")]
    names += [r["name"] for r in changed.get(target, {})
              .get("removed_symbols", []) if r.get("name")]
    conn = sqlite3.connect(
        f"file:{INDEX_ROOT / slug / 'manifest.db'}?mode=ro", uri=True)
    pops = {}
    for name in names:
        pops[name] = conn.execute(
            "SELECT COUNT(*) FROM refs WHERE to_name = ?",
            (name,)).fetchone()[0]
    conn.close()

    population = sum(pops.values())
    handed = sum(min(n, PER_NAME) for n in pops.values())
    entry = impact[target]
    print(f"file={target} names={len(names)} per_name_cap={PER_NAME}")
    print(f"populations={sorted(pops.items(), key=lambda kv: -kv[1])}")
    print(f"population={population} handed_by_per_name_reads={handed}")
    print(f"payload: callers={len(entry['callers'])}"
          f" omitted={entry['omitted']} truncated={entry['truncated']}")
    print(f"callers == handed: {len(entry['callers']) == handed}")
    print(f"omitted == population - callers:"
          f" {entry['omitted'] == population - len(entry['callers'])}")


if __name__ == "__main__":
    main()
