#!/usr/bin/env bash
# Notes-fold evidence: base tree vs tip tree, one shared index, same moment.
# usage: capture-seven.sh REPO_ROOT BASE_SRC TIP_SRC
set -u
REPO=${1:?}; BASE_SRC=${2:?}; TIP_SRC=${3:?}
S=${TMPDIR:-/tmp}/r4b-notes-sandbox
CAP=$REPO/runs/r4b-notes/captures
PY=$REPO/.venv/bin/python
rm -rf "$S" "$CAP"
mkdir -p "$S/home" "$S/index" "$S/proj" "$S/proj2" "$S/proj3" "$S/proj4" "$S/pa" "$S/pb" "$CAP"
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
  printf 'exit=%s\n' "$rc" > "$CAP/$name.exit"
  rm -f "$CAP/$name.stdout.raw" "$CAP/$name.stderr.raw"
  echo "$name exit=$rc"
}

# shape 1: stale overview, one reason kind, text and json
printf 'def alpha():\n    return 1\n' > "$S/proj/alpha.py"
run "$TIP_SRC" setup-proj add-project "$S/proj" --vectors off
printf 'def gamma():\n    return 3\n' > "$S/proj/gamma.py"
run "$BASE_SRC" base-text overview --project "$S/proj"
run "$TIP_SRC"  tip-text overview --project "$S/proj"
run "$BASE_SRC" base-json overview --project "$S/proj" --json
run "$TIP_SRC"  tip-json overview --project "$S/proj" --json

# shape 2: index-status
run "$BASE_SRC" base-status index-status --project "$S/proj"
run "$TIP_SRC"  tip-status index-status --project "$S/proj"
run "$BASE_SRC" base-status-json index-status --project "$S/proj" --json
run "$TIP_SRC"  tip-status-json index-status --project "$S/proj" --json

# shape 3: unknown verdict
printf 'def z():\n    return 0\n' > "$S/proj4/z.py"
run "$BASE_SRC" base-unknown overview --project "$S/proj4"
run "$TIP_SRC"  tip-unknown overview --project "$S/proj4"
run "$BASE_SRC" base-unknown-json overview --project "$S/proj4" --json
run "$TIP_SRC"  tip-unknown-json overview --project "$S/proj4" --json

# shape 4: three reason kinds at once
printf 'def a():\n    return 1\n' > "$S/proj3/a.py"
printf 'def b():\n    return 2\n' > "$S/proj3/b.py"
printf 'def c():\n    return 3\n' > "$S/proj3/c.py"
run "$TIP_SRC" setup-proj3 add-project "$S/proj3" --vectors off
printf 'def a():\n    return 11\n' > "$S/proj3/a.py"
printf 'def d():\n    return 4\n' > "$S/proj3/d.py"
rm "$S/proj3/b.py"
run "$BASE_SRC" base-three overview --project "$S/proj3"
run "$TIP_SRC"  tip-three overview --project "$S/proj3"
run "$BASE_SRC" base-three-json overview --project "$S/proj3" --json
run "$TIP_SRC"  tip-three-json overview --project "$S/proj3" --json

# shapes 5 and 6: stale plus an intentionally withheld file, then the override
printf 'def one():\n    return 1\n' > "$S/proj2/one.py"
printf 'aws_access_key_id = AKIAIOSFODNN7EXAMPLE\n' > "$S/proj2/notes.md"
run "$TIP_SRC" setup-proj2 add-project "$S/proj2" --vectors off
printf 'def extra():\n    return 9\n' > "$S/proj2/extra.py"
run "$BASE_SRC" base-double overview --project "$S/proj2"
run "$TIP_SRC"  tip-double overview --project "$S/proj2"
run "$BASE_SRC" base-double-json overview --project "$S/proj2" --json
run "$TIP_SRC"  tip-double-json overview --project "$S/proj2" --json
run "$BASE_SRC" base-double-allow overview --project "$S/proj2" --allow-sensitive
run "$TIP_SRC"  tip-double-allow overview --project "$S/proj2" --allow-sensitive
run "$BASE_SRC" base-double-allow-json overview --project "$S/proj2" --allow-sensitive --json
run "$TIP_SRC"  tip-double-allow-json overview --project "$S/proj2" --allow-sensitive --json
run "$BASE_SRC" base-double-status index-status --project "$S/proj2"
run "$TIP_SRC"  tip-double-status index-status --project "$S/proj2"

# shape 7: the --all-projects fan-out, text and json
printf 'def core_pa():\n    return 1\n' > "$S/pa/mod.py"
printf 'def core_pb():\n    return 1\n' > "$S/pb/mod.py"
run "$TIP_SRC" setup-pa add-project "$S/pa" --vectors off
run "$TIP_SRC" setup-pb add-project "$S/pb" --vectors off
run "$BASE_SRC" base-sem semantic-search "core" --all-projects
run "$TIP_SRC"  tip-sem semantic-search "core" --all-projects
run "$BASE_SRC" base-sem-json semantic-search "core" --all-projects --json
run "$TIP_SRC"  tip-sem-json semantic-search "core" --all-projects --json

echo '--- diffs (empty means byte-identical) ---'
for tag in text json status status-json unknown unknown-json three three-json double double-json double-allow double-allow-json double-status sem sem-json; do
  diff -u "$CAP/base-$tag.stdout" "$CAP/tip-$tag.stdout" > "$CAP/diff-$tag.stdout.txt" 2>&1 || true
  diff -u "$CAP/base-$tag.stderr" "$CAP/tip-$tag.stderr" > "$CAP/diff-$tag.stderr.txt" 2>&1 || true
  diff -u "$CAP/base-$tag.exit" "$CAP/tip-$tag.exit" > "$CAP/diff-$tag.exit.txt" 2>&1 || true
  echo "[$tag] stdout=$(wc -c < "$CAP/diff-$tag.stdout.txt") stderr=$(wc -c < "$CAP/diff-$tag.stderr.txt") exit=$(wc -c < "$CAP/diff-$tag.exit.txt") base_exit=$(cat "$CAP/base-$tag.exit") tip_exit=$(cat "$CAP/tip-$tag.exit")"
done
echo done
