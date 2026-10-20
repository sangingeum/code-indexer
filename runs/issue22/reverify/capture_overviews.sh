#!/usr/bin/env bash
# Capture `overview --path-prefix` at the tip and at the pre-repair base,
# for four project/prefix pairs. Each capture names the tree it ran.
#
#   CI22_BASE=<worktree at 0b66431> bash capture_overviews.sh
set -u
O="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$O/../../.." && pwd)"
BASE="${CI22_BASE:?set CI22_BASE to a worktree at 0b66431}"
PY="$ROOT/.venv/bin/python"
cd "$ROOT" || exit 1

run() {  # $1=label $2=src $3=project $4=prefix
  local f
  f="$O/$1-overview-$3-$(echo "$4" | tr '/.' '__').json"
  echo "--- $1 $3 $4 -> ${f#"$ROOT"/}"
  CI_TREE="$1" CI_SRC="$2" "$PY" "$O/run_cli.py" overview --project "$3" \
    --path-prefix "$4" --skip-stale-check --json >"$f" 2>"${f%.json}.err"
  rc=$?
  head -1 "${f%.json}.err"
  echo "exit=$rc bytes=$(wc -c <"$f")"
}
for pre in docs/ docs/verification; do
  run tip "$ROOT/src" vivarium-sim "$pre"
  run base "$BASE/src" vivarium-sim "$pre"
done
for pre in docs/ docs/adr/; do
  run tip "$ROOT/src" b1b7f48b "$pre"
  run base "$BASE/src" b1b7f48b "$pre"
done
