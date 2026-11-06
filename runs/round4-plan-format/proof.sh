#!/usr/bin/env bash
# Write-free proof for plan-validate and plan-show, plus the leg's acceptance
# lines. Run from the repository root; every capture lands beside this script.
# The standing watcher is frozen for the measurement and restored on exit.
set -euo pipefail

OUT="runs/round4-plan-format"
FIXTURE="$OUT/fixture"
WATCHER=355623

kill -STOP "$WATCHER"
restore_watcher() { kill -CONT "$WATCHER" 2>/dev/null || true; }
trap restore_watcher EXIT
echo "watcher $WATCHER frozen (SIGSTOP); it is SIGCONT-ed on exit"

rm -rf "$FIXTURE"
mkdir -p "$FIXTURE"
printf 'def alpha():\n    return 1\n\ndef beta():\n    return 2\n' > "$FIXTURE/sample.py"

exec > "$OUT/proof.txt" 2>&1

echo "== date"
date -u +%Y-%m-%dT%H:%M:%SZ
echo
echo "== A. register the fixture project, then the acceptance line"
set +e
code-indexer add-project "$FIXTURE" --name plan-fixture --vectors off \
  > "$OUT/add-project.json" 2>&1
ADD_EXIT=$?
set -e
echo "add-project exit $ADD_EXIT"

code-indexer plan-replace-body alpha --project plan-fixture --file sample.py \
  --text '    return 41' --print > "$OUT/print.json"
PRINTED_ID=$(jq -r .plan_id "$OUT/print.json")
COMPUTED_ID="sha256:$(jq -c 'del(.plan_id)' "$OUT/print.json" | tr -d '\n' | sha256sum | cut -d' ' -f1)"
echo "jq .plan_id             $PRINTED_ID"
echo "sha256(canonical bytes) $COMPUTED_ID"
if [ "$PRINTED_ID" = "$COMPUTED_ID" ]; then echo "MATCH"; else echo "MISMATCH"; exit 1; fi
echo "bytes on stdout: $(wc -c < "$OUT/print.json")"
echo "plan dir written by --print: $([ -d "$FIXTURE/.code-indexer" ] && echo yes || echo no)"
echo
echo "== B. the three prepare commands in an isolated fixture project"

code-indexer plan-replace-body alpha --project plan-fixture --file sample.py \
  --text '    return 41' --json > "$OUT/prepare-replace.json"
REPLACE_ID=$(jq -r .plan_id "$OUT/prepare-replace.json")
echo "replace_body  $REPLACE_ID"
echo "              $(jq -r .plan_path "$OUT/prepare-replace.json")"

code-indexer plan-insert-symbol beta --project plan-fixture --file sample.py \
  --position after --text 'def gamma():\n    return 3' --json \
  > "$OUT/prepare-insert.json"
echo "insert_symbol $(jq -r .plan_id "$OUT/prepare-insert.json")"

code-indexer plan-delete-declaration beta --project plan-fixture \
  --file sample.py --json > "$OUT/prepare-delete.json"
echo "delete_declaration $(jq -r .plan_id "$OUT/prepare-delete.json")"

echo
echo "== C. plan-validate and plan-show write nothing (fixture project)"
FINGERPRINT() { find "$1" -type f -exec sha256sum {} + | sort; }
FIX_BEFORE=$(FINGERPRINT "$FIXTURE/.code-indexer/plans")
for i in 1 2 3; do
  code-indexer plan-validate "$REPLACE_ID" --project plan-fixture --json \
    > "$OUT/validate-$i.json"
  echo "validate run $i: passed=$(jq -r .passed "$OUT/validate-$i.json")"
done
for i in 1 2; do
  code-indexer plan-show "$REPLACE_ID" --project plan-fixture --diff \
    > "$OUT/show-$i.txt"
  echo "show run $i: exit 0"
done
FIX_AFTER=$(FINGERPRINT "$FIXTURE/.code-indexer/plans")
if [ "$FIX_BEFORE" = "$FIX_AFTER" ]; then
  echo "plan dir hashes unchanged across 3 validates and 2 shows"
else
  echo "PLAN DIR CHANGED"; exit 1
fi
echo "fixture source hash before/after: $(sha256sum "$FIXTURE/sample.py" | cut -d' ' -f1)"

echo
echo "-- one validate report, verbatim"
jq -r '.checks[] | "  \(.check): \(.status) -- \(.detail)"' "$OUT/validate-1.json"
echo
echo "-- one preview, verbatim"
awk '/^--- a\//{p=1} p' "$OUT/show-1.txt" | head -20

echo
echo "== D. a stale file refuses"
printf 'def alpha():\n    return 99\n\ndef beta():\n    return 2\n' > "$FIXTURE/sample.py"
set +e
code-indexer plan-validate "$REPLACE_ID" --project plan-fixture \
  > "$OUT/stale.txt" 2> "$OUT/stale.err"
STALE_EXIT=$?
set -e
echo "exit $STALE_EXIT"
echo "stderr: $(tr -d '\n' < "$OUT/stale.err")"

echo
echo "== E. write-free proof on the real checkout"
code-indexer plan-replace-body unattributed_note --project code-indexer \
  --file src/code_indexer/identity.py --text '    return "replacement text"' \
  --json > "$OUT/prepare-real.json"
REAL_ID=$(jq -r .plan_id "$OUT/prepare-real.json")
echo "plan $REAL_ID"
echo "plan file $(jq -r .plan_path "$OUT/prepare-real.json")"
MANIFEST="$HOME/.code-indexer/code-indexer"
HASHES() { { find .code-indexer/plans -type f -exec sha256sum {} + ;
             sha256sum src/code_indexer/identity.py ;
             find "$MANIFEST" -maxdepth 1 -name 'manifest.db*' -exec sha256sum {} + ; } | sort; }
BEFORE=$(HASHES)
echo "-- hashed before"
echo "$BEFORE"
for i in 1 2 3; do
  code-indexer plan-validate "$REAL_ID" --project code-indexer --json \
    > "$OUT/real-validate-$i.json"
  echo "real validate $i: passed=$(jq -r .passed "$OUT/real-validate-$i.json")"
done
code-indexer plan-show "$REAL_ID" --project code-indexer --diff \
  > "$OUT/real-show.txt"
echo "real show: exit 0"
AFTER=$(HASHES)
echo "-- hashed after"
echo "$AFTER"
if [ "$BEFORE" = "$AFTER" ]; then
  echo "UNCHANGED: plan dir, the touched file and every manifest file"
else
  echo "CHANGED"; exit 1
fi
echo
echo "-- preview of the real plan"
awk '/^--- a\//{p=1} p' "$OUT/real-show.txt" | head -20
echo
echo "== F. cleanup"
set +e
code-indexer remove-project --project plan-fixture > "$OUT/remove-project.json" 2>&1
echo "remove-project exit $?"
set -e
exit 0
