# JSON output schema (version 1)

Every JSON-emitting command includes `"schema": 1` so consumers can pin a
contract. Field names are stable; new fields may be added (append-only), but
existing names never change meaning.

## semantic-search --json

```json
{"schema": 1, "hits": [hit...], "truncated": bool, "dropped": int}
```

hit (search and hybrid fusion share the contract):

```json
{"project": str, "file": str, "score": float,
 "symbol": str|null, "symbol_type": str|null, "lang": str,
 "start_line": int, "end_line": int, "snippet": str,
 "match": "symbol"|"lexical"|"fused"|missing,
 "rerank_delta": float|missing, "merged_count": int|missing}
```

Ordering: score desc, ties by (file, start_line). `match` appears only in
lexical/hybrid modes; `rerank_delta` only when the reranker is enabled;
`merged_count` on same-file merged hits.

## skeleton --json / outline --json

skeleton: `{"schema": 1, "files": [{"file", "lang", "total_symbols",
"symbols": [{"name", "symbol_type", "signature"|null, "start_line",
"end_line"}]}], ...}` or the tree projection `{"schema": 1, "dirs": [...]}`
(whichever projection was requested, plus `schema`).

outline: `{"schema": 1, "file": str, "lang": str, "declarations": [{"type",
"name", "start_line", "end_line", "signature"|null, ...}]}`.

## get-code-context --json

```json
{"schema": 1, "file": str, "stale": bool, "re_resolved": bool,
 "segments": [{"start_line", "end_line", "text", "symbol"|null,
               "error"|missing}]}
```

## overview --json

Project map dict (project, files, loc, languages, directories,
entry_points, tests, config_files, hotspots) + `schema: 1`.

## doctor --json

`{"checks": [{"status": "ok|warn|fail", "name": str, "detail": str}],
 "healthy": bool}`.

## find-callers/find-callees --json

`{"roots": [node...]}` where node = `{"file", "symbol"|null, "line"|null,
"relationship", "confidence", "children": [node...]}`.

## deps --json

`{"out": {level: [file...]}, "in": {level: [file...]}}` (level = 1-based).

## Error contract (non-JSON)

Failures print exactly one line `ErrorType: description` to stderr:
ArgumentError, ConfigError, NotFoundError, BackendError, LockedError,
InternalError. Exit codes: 0 success, 1 failure, 2 usage (argument) error.
The only non-verbose stderr output on success is the documented single-line
stale-file Warning.