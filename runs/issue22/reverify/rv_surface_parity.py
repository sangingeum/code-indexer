"""Bar 1 counterpart probe: do the exact-name surfaces agree with the store?

Compares, for one name, the store's definition rows (read through the manifest)
with what each agent-facing surface answers:
  find-symbol --limit 100 --json
  find-definition            (text: one line per definition)
  find-callers --max-nodes 400 --json   (its definition roots)
and then probes get-code-context --symbol in EVERY file that defines the name,
which is the one exact-name read still going through the capped library
default (find_symbols limit=25).
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from code_indexer.manifest import Manifest  # noqa: E402

PY = os.path.join(ROOT, ".venv", "bin", "python")
RUNNER = os.path.join(HERE, "run_cli.py")
NAME = "step_definition_surface"
NAME = sys.argv[1] if len(sys.argv) > 1 else "__init__"
PROJECT = sys.argv[2] if len(sys.argv) > 2 else "code-indexer"


def cli(*args, json_out=False):
    env = dict(os.environ, CI_SRC=os.path.join(ROOT, "src"))
    proc = subprocess.run([PY, RUNNER, *args, "--project", PROJECT,
                           "--skip-stale-check"],
                          env=env, capture_output=True, text=True)
    return proc


def rows_from_store() -> list[tuple[str, int]]:
    path = os.path.join(os.path.expanduser("~/.code-indexer"), PROJECT,
                        "manifest.db")
    m = Manifest(path)
    try:
        return [(r.file, r.start_line) for r in m.definitions_of(NAME)]
    finally:
        m.close()


def main() -> int:
    truth = rows_from_store()
    print(f"store definitions_of({NAME!r}) -> {len(truth)} rows,"
          f" {len({f for f, _ in truth})} files")

    p = cli("find-symbol", NAME, "--limit", "100", "--json")
    sym = [(r["file"], r["start_line"]) for r in json.loads(p.stdout)]
    print(f"find-symbol --limit 100      -> {len(sym)} rows"
          f" same_set={set(sym) == set(truth)} same_order={sym == truth}")

    p = cli("find-definition", NAME)
    lines = [ln for ln in p.stdout.strip().splitlines() if ln.strip()]
    seen = set()
    for ln in lines:
        if "::" in ln:
            tok = ln.split("::", 1)[1].split()[0]      # file:398-399
            file_part, rng = tok.rsplit(":", 1)
            seen.add(f"{file_part}:{rng.split('-')[0]}")
    expect = {f"{f}:{n}" for f, n in truth}
    print(f"find-definition              -> {len(lines)} lines"
          f" covers_every_row={expect <= seen}"
          f" missing={sorted(expect - seen)[:3]}")
    def elided(line: str) -> str:
        return "<project>::" + line.split("::", 1)[1] if "::" in line else line
    print(f"  first/last: {elided(lines[0])!r} / {elided(lines[-1])!r}")

    p = cli("find-callers", NAME, "--max-nodes", "400", "--json")
    payload = json.loads(p.stdout)
    roots = [(r["file"], r["line"]) for r in payload["roots"]]
    print(f"find-callers roots           -> {len(roots)} roots"
          f" same_set={set(roots) == set(truth)}"
          f" truncated={payload['truncated']} omitted={payload['omitted']}")

    print("\n### the residual: get-code-context --symbol, one call per"
          " defining file")
    ok, bad = [], []
    for file in sorted({f for f, _ in truth}):
        p = cli("get-code-context", file, "--symbol", NAME, "--json")
        last = [ln for ln in p.stderr.strip().splitlines() if ln.strip()]
        (ok if p.returncode == 0 else bad).append(
            (file, p.returncode, (last[-1] if last else "")[:110]))
    print(f"files answered={len(ok)} files refused={len(bad)}")
    for file, rc, err in bad:
        idx = [i for i, (f, _n) in enumerate(truth) if f == file]
        print(f"  refused rc={rc} {file}: {err}"
              f"  [its row is #{idx[0] + 1} of {len(truth)} in the read order]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
