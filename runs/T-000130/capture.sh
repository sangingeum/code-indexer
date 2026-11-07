#!/usr/bin/env bash
# R4b gate captures. Every read is a TRUE fresh process: env -i, only
# INDEX_ROOT passed, so nothing but the tree and the index can speak.
set -u
S=/home/keum/.hermes/profiles/<profile>/cache/scratch/r4b/sandbox
TIP=/home/keum/.hermes/profiles/<profile>/cache/scratch/r4b/tip/.venv/bin/code-indexer
BASE=/home/keum/.hermes/profiles/<profile>/cache/scratch/r4b/base/.venv/bin/code-indexer
OUT=/home/keum/.hermes/profiles/<profile>/cache/scratch/r4b/captures
mkdir -p "$OUT"

# proj4 -> unknown (no index manifest for this project)
rm -f "$S/index/9841c395/manifest.db" "$S/index/9841c395/manifest.db-wal" "$S/index/9841c395/manifest.db-shm"

one() {  # one <binary> <outfile> <args...>
  local bin=$1 out=$2; shift 2
  env -i INDEX_ROOT="$S/index" "$bin" "$@" >"$out.stdout" 2>"$out.stderr"
  local rc=$?
  { echo "exit=$rc"; echo "stderr_bytes=$(wc -c <"$out.stderr")"; echo "stdout_bytes=$(wc -c <"$out.stdout")";
    echo "-- stderr --"; cat "$out.stderr"; echo "-- stdout --"; head -c 400 "$out.stdout"; echo; } >"$out.txt"
}

five() {  # five <binary> <tag>
  local bin=$1 tag=$2 i
  : >"$OUT/$tag.txt"
  for i in 1 2 3 4 5; do
    one "$bin" "$OUT/$tag-$i" overview --project "$S/proj"
    { echo "== read $i =="; cat "$OUT/$tag-$i.txt"; echo; } >>"$OUT/$tag.txt"
  done
}

fivejson() {
  local bin=$1 tag=$2 i
  : >"$OUT/$tag.txt"
  for i in 1 2 3 4 5; do
    one "$bin" "$OUT/$tag-$i" overview --project "$S/proj" --json
    { echo "== read $i =="; cat "$OUT/$tag-$i.txt"; echo; } >>"$OUT/$tag.txt"
  done
}

# --- bar 1: base and tip, five fresh reads each ---
five "$BASE" base-five
five "$TIP" tip-five

# --- bar 3: JSON, base and tip ---
fivejson "$BASE" base-json
fivejson "$TIP" tip-json

# --- bar 2: two separate shells, different environments, same index ---
one "$TIP" tip-shellA overview --project "$S/proj"
( cd /tmp && env -i INDEX_ROOT="$S/index" TZ=UTC-07 LANG=C TERM=dumb SHELL=/bin/sh \
    CODE_INDEXER_STALE_TOKEN=nonsense-$$ "$TIP" overview --project "$S/proj" \
    >"$OUT/tip-shellB.stdout" 2>"$OUT/tip-shellB.stderr" ); echo "exit=$?" >"$OUT/tip-shellB.rc"
{ echo "exit=$(cat "$OUT/tip-shellB.rc")"; echo "stderr_bytes=$(wc -c <"$OUT/tip-shellB.stderr")";
  echo "-- stderr --"; cat "$OUT/tip-shellB.stderr"; } >"$OUT/tip-shellB.txt"

# --- bar 4: fresh and unknown ---
one "$TIP" tip-fresh overview --project "$S/proj3"
one "$BASE" base-fresh overview --project "$S/proj3"
one "$TIP" tip-unknown overview --project "$S/proj4"
one "$BASE" base-unknown overview --project "$S/proj4"

# --- bar 5: double fault, text, json, index-status (tip and base) ---
one "$TIP" tip-double-text overview --project "$S/proj2"
one "$BASE" base-double-text overview --project "$S/proj2"
one "$TIP" tip-double-json overview --project "$S/proj2" --json
one "$TIP" tip-double-status index-status --project "$S/proj2"
one "$BASE" base-double-status index-status --project "$S/proj2"

# --- bar 6: the stale line's own words (same captures as bar 1, kept named) ---
one "$TIP" tip-stale-line overview --project "$S/proj"

# --- bar 8: exit codes ---
one "$TIP" tip-exit-stale overview --project "$S/proj"
echo "captures written: $(ls "$OUT" | wc -l) files"
