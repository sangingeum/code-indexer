#!/usr/bin/env bash
# Statelessness: the index root is byte-identical after reads, and no file or
# marker anywhere records that a banner was shown. Run from the scratch dir.
set -u
S=${S:-$PWD/sandbox}
TIP=${TIP:-$PWD/tip/.venv/bin/code-indexer}
OUT=${OUT:-$PWD/captures}
REPO=${REPO:-$PWD/repo}

snap() { find "$S/index" -type f -printf '%P\t%s\t%T@\n' | sort; }

snap >"$OUT/snap-before.txt"
for i in 1 2 3 4 5; do
  env -i INDEX_ROOT="$S/index" "$TIP" overview --project "$S/proj" --json >/dev/null 2>&1
  env -i INDEX_ROOT="$S/index" "$TIP" overview --project "$S/proj" >/dev/null 2>&1
done
snap >"$OUT/snap-after.txt"
echo "10 reads run (5 json, 5 text)"
if diff -q "$OUT/snap-before.txt" "$OUT/snap-after.txt" >/dev/null; then echo "INDEX ROOT UNCHANGED"; else diff "$OUT/snap-before.txt" "$OUT/snap-after.txt"; fi

# Any file that could be a session marker, anywhere the binary can reach.
echo "=== files named like a marker/session/stamp ==="
find "$S" \( -iname '*marker*' -o -iname '*session*' -o -iname '*seen*' -o -iname '*warn*' -o -iname '*stamp*' \) | sort
echo "=== env vars read by the staleness path ==="
rg -n "os\.environ|getenv" "$REPO/src/code_indexer/staleness.py" || echo "(no hits)"
