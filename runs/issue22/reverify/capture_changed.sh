#!/usr/bin/env bash
# Captures for the changed-symbols items: --impact over the fix range, at a
# generous cap (only the per-name read can cut) and at a small cap (the shared
# node budget cuts), text and JSON.
set -u
O="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$O/../../.." && pwd)"
PY="$ROOT/.venv/bin/python"
cd "$ROOT" || exit 1
for cap in 400 8; do
  echo "=== --max-nodes $cap : text ==="
  CI_TREE=tip CI_SRC="$ROOT/src" "$PY" "$O/run_cli.py" changed-symbols \
    --project code-indexer --base 58473d9 --head c496e7a --impact \
    --max-nodes "$cap" --skip-stale-check >"$O/changed-$cap.txt" 2>&1
  echo "exit=$?"
  grep -E "^  impact:|^modified|^added|^removed" "$O/changed-$cap.txt" | head -40
  echo "=== --max-nodes $cap : json ==="
  CI_TREE=tip CI_SRC="$ROOT/src" "$PY" "$O/run_cli.py" changed-symbols \
    --project code-indexer --base 58473d9 --head c496e7a --impact \
    --max-nodes "$cap" --json --skip-stale-check \
    >"$O/changed-$cap.json" 2>"$O/changed-$cap.err"
  echo "exit=$?"
  jq -r '.impact | to_entries[] |
    "\(.key) callers=\(.value.callers|length) truncated=\(.value.truncated)" +
    " omitted=\(.value.omitted) bounds=\(.value.bounds|tostring)"' \
    "$O/changed-$cap.json"
done
