#!/usr/bin/env bash
# Fix-round evidence: the stale banner carries its meaning sentence again.
#
# Builds a throwaway stale project with this checkout's binary, then reads it:
# one text read, one JSON read, the same read from two unrelated environments,
# a double-fault project, a three-kind project, and the same JSON read from a
# worktree at the previous code tip. Captures land in runs/r4b-fix/captures/
# with host prefixes replaced by <scratch> / <index-root> / <home> / <checkout>.
set -u

REPO=$(git rev-parse --show-toplevel)
OUT=$REPO/runs/r4b-fix
CAP=$OUT/captures
S=${TMPDIR:-/tmp}/r4b-fix-sandbox
BIN=$REPO/.venv/bin/code-indexer
BASE_TIP=7704364

rm -rf "$S"
rm -rf "$CAP"
mkdir -p "$S/home" "$S/index" "$S/proj" "$S/proj2" "$S/proj5" "$CAP"

export HOME="$S/home"
export INDEX_ROOT="$S/index"

redact() { python3 "$OUT/redact.py" "<index-root>=$S/index" "<home>=$S/home" \
  "<scratch>=$S" "<checkout>=$REPO"; }

# capture <name> <command...> -- stdout, stderr and exit code, redacted.
capture() {
  local name=$1
  shift
  "$@" > "$CAP/$name.stdout.raw" 2> "$CAP/$name.stderr.raw"
  local rc=$?
  redact < "$CAP/$name.stdout.raw" > "$CAP/$name.stdout"
  redact < "$CAP/$name.stderr.raw" > "$CAP/$name.stderr"
  printf 'exit=%s\n' "$rc" > "$CAP/$name.exit"
  rm -f "$CAP/$name.stdout.raw" "$CAP/$name.stderr.raw"
  echo "captured $name (exit $rc)"
}

snapshot() { # snapshot <name> -- file set, sizes and mtimes, redacted
  find "$S/index" "$S/proj" "$S/proj2" "$S/proj5" -type f \
    -printf '%p %s %T@\n' | sort | redact > "$CAP/$1"
}

printf 'def alpha():\n    return 1\n' > "$S/proj/alpha.py"
printf 'def beta():\n    return 2\n' > "$S/proj/beta.py"
capture setup-add-project "$BIN" add-project "$S/proj" --vectors off

# Now stale: one added file, one reason.
printf 'def gamma():\n    return 3\n' > "$S/proj/gamma.py"

snapshot state-before.txt
capture bar1-text "$BIN" overview --project "$S/proj"
capture bar1-text-again "$BIN" overview --project "$S/proj"
capture bar2-json "$BIN" overview --project "$S/proj" --json

# Bar 3: two shells, different environments, one index, nothing else shared.
capture bar3-shellA env -i HOME="$S/home" INDEX_ROOT="$S/index" \
  PATH=/usr/bin:/bin TZ=UTC "$BIN" overview --project "$S/proj"
( cd / && capture bar3-shellB env -i HOME="$S/home" INDEX_ROOT="$S/index" \
  PATH=/usr/bin:/bin TZ=UTC-07 LANG=C TERM=dumb SHELL=/bin/sh \
  CODE_INDEXER_STALE_TOKEN=nonsense-shellB "$BIN" overview \
  --project "$S/proj" )
snapshot state-after.txt

diff -u "$CAP/state-before.txt" "$CAP/state-after.txt" | redact \
  > "$CAP/state-diff.txt"
find "$S/index" "$S/proj" -type f \
  \( -name '*marker*' -o -name '*session*' -o -name '*seen*' \
     -o -name '*stamp*' -o -name '*token*' \) | sort | redact \
  > "$CAP/state-marker-files.txt"

# The double fault: stale and holding a withheld file in one line.
printf 'def one():\n    return 1\n' > "$S/proj2/one.py"
printf 'aws_access_key_id = AKIAIOSFODNN7EXAMPLE\n' > "$S/proj2/notes.md"
capture setup-add-project2 "$BIN" add-project "$S/proj2" --vectors off
printf 'def extra():\n    return 9\n' > "$S/proj2/extra.py"
capture double-fault-text "$BIN" overview --project "$S/proj2"
capture double-fault-json "$BIN" overview --project "$S/proj2" --json

# Three kinds at once: modified, added and deleted on one count line.
printf 'def a():\n    return 1\n' > "$S/proj5/a.py"
printf 'def b():\n    return 2\n' > "$S/proj5/b.py"
printf 'def c():\n    return 3\n' > "$S/proj5/c.py"
capture setup-add-project5 "$BIN" add-project "$S/proj5" --vectors off
printf 'def a():\n    return 11\n' > "$S/proj5/a.py"
printf 'def d():\n    return 4\n' > "$S/proj5/d.py"
rm "$S/proj5/b.py"
capture three-kinds-text "$BIN" overview --project "$S/proj5"
capture three-kinds-json "$BIN" overview --project "$S/proj5" --json

# Bar 2: the same JSON read from a worktree at the previous code tip.
git -C "$REPO" worktree add --detach "$S/base" "$BASE_TIP" \
  > "$CAP/worktree-add.log" 2>&1
( cd "$S/base" && uv sync --frozen --all-extras > /dev/null 2>&1 )
capture base-text "$S/base/.venv/bin/code-indexer" overview \
  --project "$S/proj"
capture base-json "$S/base/.venv/bin/code-indexer" overview \
  --project "$S/proj" --json

diff -u "$CAP/base-json.stdout" "$CAP/bar2-json.stdout" \
  > "$CAP/bar2-json-diff.txt"
printf 'base_sha=%s\n' "$(git -C "$S/base" rev-parse HEAD)" \
  > "$CAP/bar2-base-sha.txt"

# Bar 4: every tracked quote of the banner.
git -C "$REPO" ls-files -z | xargs -0 grep -n "Warning: index is stale" \
  > "$CAP/bar4-grep.raw" 2>&1
redact < "$CAP/bar4-grep.raw" > "$CAP/bar4-grep.txt"
rm -f "$CAP/bar4-grep.raw"

echo "done: $CAP"
