#!/usr/bin/env bash
# The updated test files against the pre-fix sources.
#
#   CI22_BASE=<worktree at 58473d9> bash revert_sensitivity.sh
#
# The four test files from the tip are copied over the base's versions, and
# pytest runs with PYTHONPATH pointing at the BASE src — a helper prints which
# tree the package resolved to before the run.
set -u
O="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$O/../../.." && pwd)"
BASE="${CI22_BASE:?set CI22_BASE to a worktree at 58473d9}"
VENV="$ROOT/.venv/bin/python"
TESTS="tests/test_changed_symbols.py tests/test_graph.py tests/test_manifest_store_reads.py tests/test_overview.py"

for t in $TESTS; do
  cp "$ROOT/$t" "$BASE/$t"
done
echo "--- copied tip test files into the base worktree"
echo "--- tree check before the run"
CI_SRC="$BASE/src" "$VENV" "$O/which_tree.py"
echo "--- does the base source carry definitions_of?"
rg -c "def definitions_of" "$BASE/src/code_indexer/manifest.py" || echo "0 (absent, as expected)"

cd "$BASE" || exit 1
PYTHONPATH="$BASE/src" "$VENV" -m pytest $TESTS -q -W error -p no:cacheprovider \
  > "$O/pins-fail-on-base-full.txt" 2>&1
echo "pytest exit=$?"
"$VENV" "$O/sanitize_capture.py" "$O/pins-fail-on-base-full.txt" \
  "$O/pins-fail-on-base.txt"
tail -3 "$O/pins-fail-on-base.txt"
