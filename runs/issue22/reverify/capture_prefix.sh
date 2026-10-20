#!/usr/bin/env bash
# Run the independent prefix aggregate for the four project/prefix pairs and
# compare it to the tip's and the base's own overview captures.
set -u
O="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$O/../../.." && pwd)"
PY="$ROOT/.venv/bin/python"
cd "$ROOT" || exit 1

run() {  # $1=project $2=prefix
  local f
  f="$2"
  echo
  "$PY" "$O/rv_prefix.py" "$1" "$2" \
    "$O/tip-overview-$1-$(echo "$2" | tr '/.' '__').json" \
    "$O/base-overview-$1-$(echo "$2" | tr '/.' '__').json"
}
run vivarium-sim docs/
run vivarium-sim docs/verification
run b1b7f48b docs/
run b1b7f48b docs/adr/
