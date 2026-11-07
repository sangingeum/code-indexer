#!/usr/bin/env bash
# Ten real read invocations, base tree vs tip tree, one shared index, same
# moment. usage: capture-invocations.sh REPO_ROOT BASE_SRC TIP_SRC MODE
#   MODE=base     records the base side only (before any edit)
#   MODE=compare  runs both sides and writes the diff table
set -u
REPO=${1:?}; BASE_SRC=${2:?}; TIP_SRC=${3:?}; MODE=${4:-compare}
S=${TMPDIR:-/tmp}/r3-flags-sandbox
CAP=$REPO/runs/r3-flags/captures
PY=$REPO/.venv/bin/python
rm -rf "$S" "$CAP"
mkdir -p "$S/home" "$S/index" "$S/proj" "$CAP"
export HOME="$S/home"
export INDEX_ROOT="$S/index"
redact() { sed -e "s|$S|<scratch>|g" -e "s|$REPO|<checkout>|g"; }

run() { # run SRC NAME ARGS...
  local src=$1 name=$2; shift 2
  ( cd "$S" && PYTHONPATH=$src "$PY" -m code_indexer.cli "$@" ) \
    > "$CAP/$name.stdout.raw" 2> "$CAP/$name.stderr.raw"
  local rc=$?
  redact < "$CAP/$name.stdout.raw" > "$CAP/$name.stdout"
  redact < "$CAP/$name.stderr.raw" > "$CAP/$name.stderr"
  printf '%s\n' "$rc" > "$CAP/$name.exit"
  rm -f "$CAP/$name.stdout.raw" "$CAP/$name.stderr.raw"
  echo "$name exit=$rc"
}

cat > "$S/proj/mod.py" <<'PY'
def helper(x):
    """Add one."""
    return x + 1


def caller():
    return helper(1)


class Widget:
    def render(self):
        return helper(2)
PY
cat > "$S/proj/util.py" <<'PY'
def util_fn():
    return 1
PY

run "$TIP_SRC" setup add-project "$S/proj" --vectors off

# The ten invocations: the orientation reads the evidence in runs/ captured,
# one from each read surface that the survey exercised.
run "$BASE_SRC" base-01-overview overview --project "$S/proj"
run "$BASE_SRC" base-02-overview-json overview --project "$S/proj" --json
run "$BASE_SRC" base-03-status index-status --project "$S/proj"
run "$BASE_SRC" base-04-status-json index-status --project "$S/proj" --json
run "$BASE_SRC" base-05-skeleton skeleton --project "$S/proj"
run "$BASE_SRC" base-06-outline outline mod.py --project "$S/proj"
run "$BASE_SRC" base-07-find-symbol find-symbol helper --project "$S/proj"
run "$BASE_SRC" base-08-find-definition-json find-definition helper --project "$S/proj" --json
run "$BASE_SRC" base-09-context get-code-context mod.py --project "$S/proj" --symbol helper
run "$BASE_SRC" base-10-references-json find-references helper --project "$S/proj" --json

if [ "$MODE" = "compare" ]; then
  run "$TIP_SRC" tip-01-overview overview --project "$S/proj"
  run "$TIP_SRC" tip-02-overview-json overview --project "$S/proj" --json
  run "$TIP_SRC" tip-03-status index-status --project "$S/proj"
  run "$TIP_SRC" tip-04-status-json index-status --project "$S/proj" --json
  run "$TIP_SRC" tip-05-skeleton skeleton --project "$S/proj"
  run "$TIP_SRC" tip-06-outline outline mod.py --project "$S/proj"
  run "$TIP_SRC" tip-07-find-symbol find-symbol helper --project "$S/proj"
  run "$TIP_SRC" tip-08-find-definition-json find-definition helper --project "$S/proj" --json
  run "$TIP_SRC" tip-09-context get-code-context mod.py --project "$S/proj" --symbol helper
  run "$TIP_SRC" tip-10-references-json find-references helper --project "$S/proj" --json
fi

echo '--- ten invocations ---'
for tag in 01-overview 02-overview-json 03-status 04-status-json 05-skeleton \
           06-outline 07-find-symbol 08-find-definition-json 09-context \
           10-references-json; do
  if [ "$MODE" = "compare" ]; then
    diff -u "$CAP/base-$tag.stdout" "$CAP/tip-$tag.stdout" \
      > "$CAP/diff-$tag.stdout.txt" 2>&1 || true
    diff -u "$CAP/base-$tag.stderr" "$CAP/tip-$tag.stderr" \
      > "$CAP/diff-$tag.stderr.txt" 2>&1 || true
    echo "[$tag] stdout_diff=$(wc -c < "$CAP/diff-$tag.stdout.txt")B stderr_diff=$(wc -c < "$CAP/diff-$tag.stderr.txt")B base_exit=$(cat "$CAP/base-$tag.exit") tip_exit=$(cat "$CAP/tip-$tag.exit")"
  else
    echo "[$tag] $(cat "$CAP/base-$tag.exit") stdout=$(wc -c < "$CAP/base-$tag.stdout")B"
  fi
done
echo done
