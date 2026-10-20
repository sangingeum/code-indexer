"""Bar 3 probe: per-file fan-in under --path-prefix, three ways.

1. `scoped`  — my own aggregate, ownership resolved among the prefix's files
               (what the fix claims, and what the base's row scan did).
2. `global`  — the shipped-before rule: ownership over the whole store, then
               filter to the prefix (the rows the fix recovers).
3. `overview` — the tip CLI's own numbers, loaded from a JSON capture, and the
               base worktree's numbers, loaded from another.

Read-only: the live manifest is copied with SQLite's backup API first.

usage: rv_prefix.py SLUG PREFIX [TIP_OVERVIEW_JSON] [BASE_OVERVIEW_JSON]
"""
import json
import os
import sqlite3
import sys
import tempfile

INDEX_ROOT = os.path.expanduser("~/.code-indexer")


def snapshot(slug: str) -> str:
    live = os.path.join(INDEX_ROOT, slug, "manifest.db")
    dst_dir = os.path.join(tempfile.gettempdir(), "ci22-rv")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, f"{slug}.manifest.db")
    for suffix in ("-wal", "-shm"):
        if os.path.exists(dst + suffix):
            os.remove(dst + suffix)
    src = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    out = sqlite3.connect(dst)
    src.backup(out)
    out.close()
    src.close()
    return dst


def fan_in(conn: sqlite3.Connection, prefix: str) -> dict:
    files = {r[0] for r in conn.execute("SELECT path FROM files")
             if r[0].startswith(prefix)}
    syms = [(r[0], r[1], r[2]) for r in conn.execute(
        "SELECT file, name, start_line FROM symbols ORDER BY file, start_line")]
    owner_global: dict[str, str] = {}
    owner_scoped: dict[str, str] = {}
    for file, name, _line in syms:          # (file, start_line) order
        owner_global.setdefault(name, file)
        if file in files:
            owner_scoped.setdefault(name, file)
    counts = {"scoped": {}, "global": {}}
    for file, to_name in conn.execute("SELECT file, to_name FROM refs"):
        owner = owner_scoped.get(to_name)
        if owner is not None and file != owner:
            counts["scoped"][owner] = counts["scoped"].get(owner, 0) + 1
        gowner = owner_global.get(to_name)
        if gowner is not None and gowner in files and file != gowner:
            counts["global"][gowner] = counts["global"].get(gowner, 0) + 1
    for file, resolved in conn.execute(
            "SELECT file, resolved_file FROM imports"
            " WHERE resolved_file IS NOT NULL"):
        if resolved in files and resolved != file:
            counts["scoped"][resolved] = counts["scoped"].get(resolved, 0) + 1
            counts["global"][resolved] = counts["global"].get(resolved, 0) + 1
    moved = {n: (owner_scoped[n], owner_global[n])
             for n in owner_scoped
             if owner_global.get(n) not in (None, owner_scoped[n])}
    return {"files": len(files), "counts": counts, "moved": moved,
            "owners_equal": all(owner_global.get(n) == owner_scoped[n]
                                for n in owner_scoped)}


def top(counts: dict[str, int], n: int = 10) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def main() -> int:
    slug, prefix = sys.argv[1], sys.argv[2]
    tip_json = sys.argv[3] if len(sys.argv) > 3 else None
    base_json = sys.argv[4] if len(sys.argv) > 4 else None
    conn = sqlite3.connect(snapshot(slug))
    data = fan_in(conn, prefix)
    conn.close()
    print(f"### {slug}  --path-prefix {prefix!r}")
    print(f"files_under_prefix={data['files']}"
          f" names_whose_owner_moves_out_of_scope={len(data['moved'])}")
    print(f"owner_scoped == owner_global for every in-prefix name:"
          f" {data['owners_equal']}")
    for label in ("scoped", "global"):
        c = data["counts"][label]
        print(f"{label}_total={sum(c.values())} files_with_fan_in={len(c)}")
        print(f"{label}_top10={top(c)}")
    s, g = data["counts"]["scoped"], data["counts"]["global"]
    deltas = sorted(((f, s.get(f, 0) - g.get(f, 0)) for f in s),
                    key=lambda kv: (-kv[1], kv[0]))[:5]
    print(f"recovered_rows_total={sum(s.values()) - sum(g.values())}"
          f" biggest_file_deltas={deltas}")
    for label, path in (("tip_overview", tip_json), ("base_overview", base_json)):
        if not path:
            continue
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        fan = payload["hotspots"]["fan_in"]
        print(f"{label}_path={os.path.basename(path)}")
        print(f"{label}_top10={[(d['file'], d['fan_in']) for d in fan]}")
        if label == "tip_overview":
            mine = top(s, 10)
            theirs = [(d["file"], d["fan_in"]) for d in fan]
            print(f"tip_matches_scoped_aggregate={mine == theirs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
