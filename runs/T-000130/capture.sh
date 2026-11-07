#!/usr/bin/env bash
# Gate captures. Every read is a TRUE fresh process: env -i with only
# INDEX_ROOT in the environment, so nothing but the tree and the index speaks.
# Run from the gate scratch directory (S, TIP, BASE, OUT are $PWD-relative).
set -u
S=${S:-$PWD/sandbox}
TIP=${TIP:-$PWD/tip/.venv/bin/code-indexer}
BASE=${BASE:-$PWD/base/.venv/bin/code-indexer}
OUT=${OUT:-$PWD/captures}
mkdir -p "$OUT"

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
one "$TIP" "$OUT/tip-shellA" overview --project "$S/proj"
( cd /tmp && env -i INDEX_ROOT="$S/index" TZ=UTC-07 LANG=C TERM=dumb SHELL=/bin/sh \
    CODE_INDEXER_STALE_TOKEN=nonsense-$$ "$TIP" overview --project "$S/proj" \
    >"$OUT/tip-shellB.stdout" 2>"$OUT/tip-shellB.stderr" ); shell_b_rc=$?
{ echo "exit=$shell_b_rc"; echo "stderr_bytes=$(wc -c <"$OUT/tip-shellB.stderr")";
  echo "-- stderr --"; cat "$OUT/tip-shellB.stderr"; } >"$OUT/tip-shellB.txt"

# --- bar 4: fresh and unknown (the unknown project's manifest is removed) ---
rm -f "$S/index"/*/manifest.db 2>/dev/null || true
one "$TIP" "$OUT/tip-fresh" overview --project "$S/proj3"
one "$TIP" "$OUT/tip-unknown" overview --project "$S/proj4"

# --- bar 5: double fault, text, json, index-status ---
one "$TIP" "$OUT/tip-double-text" overview --project "$S/proj2"
one "$BASE" "$OUT/base-double-text" overview --project "$S/proj2"
one "$TIP" "$OUT/tip-double-json" overview --project "$S/proj2" --json
one "$TIP" "$OUT/tip-double-status" index-status --project "$S/proj2"
one "$BASE" "$OUT/base-double-status" index-status --project "$S/proj2"

# --- bar 6: the stale line's own words ---
one "$TIP" "$OUT/tip-stale-line" overview --project "$S/proj"

# --- bar 8: exit codes ---
one "$TIP" "$OUT/tip-exit-stale" overview --project "$S/proj"
echo "captures written: $(ls "$OUT" | wc -l) files"
