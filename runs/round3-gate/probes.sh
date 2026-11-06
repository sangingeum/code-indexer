#!/usr/bin/env bash
# Gate probes for the structural query surface. Every command is run against
# this repository's own live index. Output lands beside this script, one file
# per bar; host paths are masked afterwards by mask.py before commit.
set -u
set -o pipefail
cd "$(dirname "$0")/../.." || exit 1
P=runs/round3-gate
DB="$HOME/.code-indexer/code-indexer/manifest.db"
rm -f "$P"/b*.txt "$P"/graph-*.json

cap() {
  local name="$1"; local cmd="$2"
  { printf '$ %s\n' "$cmd"; eval "$cmd"; printf '[exit=%s]\n\n' "$?"; } >> "$P/$name.txt" 2>&1
}

# ---- the filter matrix (each filter alone, its no-hit case, composition) ----
cap b2-kind 'code-indexer find-symbol --kind class --limit 3'
cap b2-kind 'code-indexer find-symbol --kind class --json | jq -c "{match_count, truncated}"'
cap b2-kind 'code-indexer find-symbol --kind class --limit 3 --json | jq -c "[.symbols[] | [.file,.start_line,.name]]"'
cap b2-kind 'code-indexer find-symbol --type class --limit 3 --json | jq -c "[.symbols[] | [.file,.start_line,.name]]"'
cap b2-kind 'code-indexer find-symbol --symbol-type class --limit 3 --json | jq -c "[.symbols[] | [.file,.start_line,.name]]"'
cap b2-kind 'code-indexer find-symbol --kind interface --json | jq -c "{match_count, shown: (.symbols|length)}"'

cap b2-name-pattern "code-indexer find-symbol --name-pattern 'Core*' --limit 5"
cap b2-name-pattern "code-indexer find-symbol --name-pattern 'Core*' --json | jq -c '{match_count, truncated, ambiguity: (.ambiguity // null)}'"
cap b2-name-pattern "code-indexer find-symbol --name-pattern '*Manager' --json | jq -c '{match_count}'"
cap b2-name-pattern "code-indexer find-symbol --name-pattern 'core*' --json | jq -c '{match_count}'"
cap b2-name-pattern "code-indexer find-symbol --name-pattern 'Core|Store' --json | jq -c '{match_count}'"
cap b2-name-pattern "code-indexer find-symbol --name-pattern '.*Core.*' --json | jq -c '{match_count}'"

cap b2-container 'code-indexer find-symbol --container Manifest --limit 4'
cap b2-container "code-indexer find-symbol --container Manifest --json | jq -c '{match_count, truncated}'"
cap b2-container "code-indexer find-symbol --container Core --json | jq -c '{match_count}'"
cap b2-container "code-indexer find-symbol --container NoSuchContainer --json | jq -c '{match_count}'"

cap b2-visibility 'code-indexer find-symbol --visibility private --limit 3'
cap b2-visibility "code-indexer find-symbol --visibility private --json | jq -c '{match_count}'"
cap b2-visibility "code-indexer find-symbol --visibility public --json | jq -c '{match_count}'"
cap b2-visibility 'code-indexer find-symbol --visibility internal'
cap b2-visibility 'code-indexer find-symbol --visibility private --container NoSuchContainer --json | jq -c "{match_count}"'

cap b2-language 'code-indexer find-symbol --language markdown --limit 3'
cap b2-language "code-indexer find-symbol --language markdown --json | jq -c '{match_count}'"
cap b2-language "code-indexer find-symbol --language python --json | jq -c '{match_count}'"
cap b2-language "code-indexer find-symbol --language cobol --json | jq -c '{match_count}'"

cap b2-inherits 'code-indexer find-symbol --inherits IndexerError --limit 5'
cap b2-inherits "code-indexer find-symbol --inherits IndexerError --json | jq -c '{match_count, names: [.symbols[].name], evidence: ([.symbols[].evidence]|unique)}'"
cap b2-inherits "code-indexer find-symbol --inherits ArgumentError --json | jq -c '{match_count, names: [.symbols[].name]}'"
cap b2-inherits "code-indexer find-symbol --inherits Base --json | jq -c '{match_count, evidence_note}'"
cap b2-inherits 'code-indexer find-symbol --inherits Base'

cap b2-has-call 'code-indexer find-symbol --has-call find_symbols --limit 5'
cap b2-has-call "code-indexer find-symbol --has-call find_symbols --json | jq -c '{match_count, evidence: ([.symbols[].evidence]|unique)}'"
cap b2-has-call "code-indexer find-symbol --has-call count_symbols --json | jq -c '{match_count}'"
cap b2-has-call "code-indexer find-symbol --has-call no_such_function --json | jq -c '{match_count, evidence_note}'"
cap b2-has-call 'code-indexer find-symbol --has-call no_such_function'

cap b2-compose 'code-indexer find-symbol --kind function --container Manifest --has-call find_symbols'
cap b2-compose "code-indexer find-symbol --kind function --container Manifest --has-call find_symbols --json | jq -c '{match_count, names:[.symbols[].name]}'"
cap b2-compose "code-indexer find-symbol --name-pattern 'test_*' --kind function --language python --json | jq -c '{match_count, truncated}'"
cap b2-compose "code-indexer find-symbol --name-pattern 'test_*' --kind function --language markdown --json | jq -c '{match_count}'"
cap b2-compose "code-indexer find-symbol --container Manifest --visibility private --language python --kind function --limit 5 --json | jq -c '{match_count, shown:(.symbols|length), truncated}'"

# ---- the name index answers a prefix glob, whatever it matches ----
cap b3-timing 'for i in 1 2 3; do /usr/bin/time -f "prefix glob Core* (1 row) %e s" code-indexer find-symbol --name-pattern "Core*" --limit 1 > /dev/null; done'
cap b3-timing 'for i in 1 2 3; do /usr/bin/time -f "prefix glob test_* (many rows) %e s" code-indexer find-symbol --name-pattern "test_*" --limit 1 > /dev/null; done'
cap b3-timing 'for i in 1 2 3; do /usr/bin/time -f "leading wildcard *Manager (0 rows, scan) %e s" code-indexer find-symbol --name-pattern "*Manager" --limit 1 > /dev/null; done'
cap b3-timing 'for i in 1 2 3; do /usr/bin/time -f "browse, no filter %e s" code-indexer find-symbol --limit 1 > /dev/null; done'

# ---- evidence on every candidate, and unknown never a negative ----
cap b4-evidence "code-indexer find-symbol --language markdown --limit 3 --json | jq -c '[.symbols[] | {name, evidence}]'"
cap b4-evidence "code-indexer find-symbol --language python --limit 3 --json | jq -c '[.symbols[] | {name, evidence}]'"
cap b4-evidence 'code-indexer find-symbol --kind class --limit 3'
cap b4-evidence 'code-indexer find-symbol --inherits ArgumentError'
cap b4-evidence "code-indexer find-symbol JSON --json | jq -c '{match_count, rows: [.symbols[] | {file, name, evidence}]}'"
cap b4-evidence 'code-indexer find-symbol JSON'
cap b4-evidence "code-indexer find-symbol --inherits Base --json | jq -c '{match_count, evidence_note}'"
cap b4-evidence 'code-indexer find-symbol --has-call no_such_function'

# ---- ambiguity envelope and the single-symbol refusal ----
cap b5-ambiguity "code-indexer find-symbol find_symbol --json | jq -c '{match_count, truncated, candidates: [.ambiguity.candidates[] | {file, range, kind, container, signature, language, decl_hash}]}'"
cap b5-ambiguity 'code-indexer find-symbol find_symbol'
cap b5-ambiguity 'code-indexer find-definition main --exactly-one'
cap b5-ambiguity 'code-indexer find-definition main --exactly-one --json'
cap b5-ambiguity 'code-indexer find-definition resolve_one --exactly-one'
cap b5-ambiguity "code-indexer find-definition resolve_one --exactly-one --json | jq -c '{definitions: [.definitions[] | .file]}'"

# ---- graph commands union over candidates ----
cap b6-graph "code-indexer find-symbol Core --json | jq -c '{match_count, candidates: [.ambiguity.candidates[] | {file, start: .range.start_line}]}'"
cap b6-graph 'code-indexer find-callers Core --max-nodes 8 --json > runs/round3-gate/graph-callers.json'
cap b6-graph "jq -c '{keys: keys, match_count, candidate_count: (.ambiguity.candidates|length)}' runs/round3-gate/graph-callers.json"
cap b6-graph "jq -c '[.roots[] | {file, line}]' runs/round3-gate/graph-callers.json"
cap b6-graph "jq -c '[.roots[].children[]? | {file, line, targets}] | .[0:6]' runs/round3-gate/graph-callers.json"
cap b6-graph 'code-indexer find-callers Core --max-nodes 4 --depth 1'
cap b6-graph 'code-indexer find-callees Core --max-nodes 8 --json > runs/round3-gate/graph-callees.json'
cap b6-graph "jq -c '{match_count, candidate_count: (.ambiguity.candidates|length), roots: [.roots[] | {file, line}]}' runs/round3-gate/graph-callees.json"
cap b6-graph "jq -c '[.roots[].children[]? | {to, targets}] | .[0:6]' runs/round3-gate/graph-callees.json"
cap b6-graph 'code-indexer find-callees Core --max-nodes 4 --depth 1'
cap b6-graph 'code-indexer find-callers Core --max-nodes 60 --depth 1 --json > runs/round3-gate/graph-callers-depth1.json'
cap b6-graph "jq -c '{root_count:(.roots|length), child_counts:[.roots[]|{file,children:(.children|length)}], target_lengths:([.roots[].children[]? | (.targets|length)] | group_by(.) | map({n:.[0], rows:length}))}' runs/round3-gate/graph-callers-depth1.json"
cap b6-graph "jq -c '[.roots[].children[]? | select((.targets|length)==0)] | length' runs/round3-gate/graph-callers-depth1.json"
cap b6-graph 'code-indexer find-callees Core --max-nodes 60 --depth 1 --json > runs/round3-gate/graph-callees-depth1.json'
cap b6-graph "jq -c '{root_count:(.roots|length), target_lengths:([.roots[].children[]? | (.targets|length)] | group_by(.) | map({n:.[0], rows:length}))}' runs/round3-gate/graph-callees-depth1.json"
cap b6-graph "jq -c '[.roots[].children[]? | select((.targets|length)>1)]' runs/round3-gate/graph-callees-depth1.json"
cap b6-graph 'code-indexer find-callers find_symbol --max-nodes 80 --depth 1 --json > runs/round3-gate/graph-callers-find-symbol.json'
cap b6-graph "jq -c '{match_count, candidates:(.ambiguity.candidates|length), target_lengths:([.roots[].children[]? | (.targets|length)] | group_by(.) | map({n:.[0], rows:length}))}' runs/round3-gate/graph-callers-find-symbol.json"

# ---- a capped answer says so ----
cap b7-truncated "code-indexer find-symbol --language python --limit 5 --json | jq -c '{match_count, shown:(.symbols|length), truncated}'"
cap b7-truncated 'code-indexer find-symbol --language python --limit 5'
cap b7-truncated 'code-indexer find-symbol --container Manifest --limit 4'
cap b7-truncated "code-indexer find-symbol --container Manifest --limit 100 --json | jq -c '{match_count, shown:(.symbols|length), truncated}'"
cap b7-truncated "code-indexer find-symbol find_symbol --json | jq -c '{match_count, shown:(.symbols|length), truncated}'"

# ---- read contract and refusal matrix spot checks ----
h1=$(sha256sum "$DB" | cut -d' ' -f1)
code-indexer find-symbol Core --json > /dev/null 2>&1
code-indexer find-callers Core --json > /dev/null 2>&1
h2=$(sha256sum "$DB" | cut -d' ' -f1)
{ printf 'manifest.db sha256 before=%s\nmanifest.db sha256 after =%s\nidentical=%s\n' \
    "$h1" "$h2" "$([ "$h1" = "$h2" ] && echo yes || echo NO)"; } > "$P/b8-read-contract.txt"
cap b8-refusals 'code-indexer find-symbol --visibility maybe'
cap b8-refusals 'code-indexer find-definition definitely_not_a_symbol'
cap b8-refusals 'code-indexer find-symbol Core --project /nonexistent-project-path --json'

# ---- docs against the shipped surface ----
cap b9-docs 'code-indexer find-symbol --help'
cap b9-docs "code-indexer find-symbol find_symbol --json | jq -c 'keys'"
cap b9-docs "code-indexer find-symbol find_symbol --json | jq -c '.ambiguity.candidates[0] | keys'"
cap b9-docs 'code-indexer --help'

echo done
