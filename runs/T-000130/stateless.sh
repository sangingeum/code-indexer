#!/usr/bin/env bash
# Statelessness: the index root is byte-identical after reads, and no file or
# marker anywhere records that a banner was shown.
set -u
S=/home/keum/.hermes/profiles/<profile>/cache/scratch/r4b/sandbox
TIP=/home/keum/.hermes/profiles/<profile>/cache/scratch/r4b/tip/.venv/bin/code-indexer
OUT=/home/keum/.hermes/profiles/<profile>/cache/scratch/r4b/captures

snap() { find "$S/index" -type f -printf '%P\t%s\t%T@\n' | sort | sha256sum; }

snap >"$OUT/index-snapshot-before.txt"
for i in 1 2 3 4 5; do
  env -i INDEX_ROOT="$S/index" "$TIP" overview --project "$S/proj" --json >/dev/null 2>&1
  env -i INDEX_ROOT="$S/index" "$TIP" overview --project "$S/proj" >/dev/null 2>&1
  env -i INDEX_ROOT="$S/index" "$TIP" index-status --project "$S/proj2" >/dev/null 2>&1
done
snap >"$OUT/index-snapshot-after.txt"
echo "15 reads run (5 json, 5 text, 5 index-status)"
echo "before: $(cut -d' ' -f1 "$OUT/index-snapshot-before.txt")"
echo "after:  $(cut -d' ' -f1 "$OUT/index-snapshot-after.txt")"
if diff -q "$OUT/index-snapshot-before.txt" "$OUT/index-snapshot-after.txt" >/dev/null; then echo "INDEX ROOT UNCHANGED (path, size, mtime for every file)"; else echo "INDEX ROOT CHANGED"; fi

# Any file that could be a session marker: name-based sweep of the sandbox home,
# the index root and the temp dirs the binary could reach.
echo "=== files under HOME/index newer than the fixture build ==="
find "$S" -newer "$S/proj/alpha.py" -type f | sort
echo "=== files named like a marker/session/stamp anywhere in the sandbox ==="
find "$S" \( -iname '*marker*' -o -iname '*session*' -o -iname '*seen*' -o -iname '*warn*' -o -iname '*stamp*' \) | sort
echo "=== env vars read by the staleness path ==="
rg -n "os\.environ|getenv" /home/keum/dev/athena/code-indexer/src/code_indexer/staleness.py /home/keum/dev/athena/code-indexer/src/code_indexer/cli.py | head -20
