#!/usr/bin/env bash
# code-indexer watcher-keeper: ensure a background watcher runs for every
# registered project so agent sessions never pay a cold staleness pass.
# Idempotent — `watch --background` no-ops while a live watcher holds the
# PID-file flock; a dead watcher is simply restarted.
#
# Designed to run from a Hermes cron job (no-agent mode, script copied into
# ~/.hermes/scripts/):
#   hermes cron create "*/10 * * * *" --name watcher-keeper \
#     --no-agent --script watcher-keeper.sh --deliver local
set -u
LOG="${HOME}/.code-indexer/watcher-keeper.log"
fail=0
while IFS= read -r line; do
  # list-projects output line: take the path (last field that looks like a path)
  proj=$(printf '%s\n' "$line" | grep -oE '/[[:alnum:]._/@-]+' | head -1)
  [ -z "$proj" ] && continue
  [ -d "$proj" ] || continue
  out=$(code-indexer watch "$proj" --background 2>&1 | tail -1)
  echo "$out" >>"$LOG"
  lastlog=$(tail -1 "${HOME}/.code-indexer/watch.log" 2>/dev/null)
  case "$out$lastlog" in
    *"refusing"*|*"already holds"*) echo "$(date -Is) ${proj}: watcher already live" ;;
    *failed*)          echo "$(date -Is) ${proj}: WATCH START FAILED ($lastlog)"; fail=1 ;;
    *)                 echo "$(date -Is) ${proj}: watcher started" ;;
  esac
  status=$(code-indexer index-status "$proj" 2>>"$LOG" | tail -1)
  echo "$(date -Is) ${proj}: ${status}"
done < <(code-indexer list-projects 2>>"$LOG")
exit $fail
