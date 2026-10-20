# JSON output schema (version 1)

Every JSON-emitting command includes `"schema": 1` so consumers can pin a
contract. Field names are stable; new fields may be added (append-only), but
existing names never change meaning.

## semantic-search --json

```json
{"schema": 1, "hits": [hit...], "truncated": bool, "dropped": int,
 "skipped": [{"project": str, "reason": str}...], "skipped_count": int}
```

`skipped` names the projects whose index could not answer, so a JSON consumer
can tell a partial result from a complete one; `skipped_count` is its length.
Both are always present (empty list / 0 when nothing was skipped).

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

`hotspots.fan_in` is `[{"file": str, "fan_in": int}]`, the top ten by a
**complete** per-file count: resolved imports into the file plus references
to a name that file defines, self-references excluded. It is a count, never a
sample of the reference table, so two runs over one index agree exactly.

## changed-symbols --json

The changed map plus `schema`:

```json
{"schema": 1, "<file>": {"kind": str, "symbols": [sym...],
 "removed_symbols": [sym...], "files": [str]},
 "impact": {"<file>": {"callers": [caller...], "candidate_tests": [str],
                       "truncated": bool, "omitted": int}}}
```

`impact` is present only with `--impact`. caller =
`{"symbol", "file", "from", "line", "confidence"}`, where `symbol` is the
changed symbol the caller references.

In an impact entry, `truncated` is the completeness flag for that file's
caller list and `omitted` counts the reference rows its bounds refused — the
per-name store read and the shared `--max-nodes` budget. It is a count of rows
withheld, never a population total, and it is the same pair of names the graph
commands use, so one rule reads every incomplete answer: decide completeness
from `truncated`.

## doctor --json

`{"checks": [{"status": "ok|warn|fail", "name": str, "detail": str}],
 "healthy": bool}`.

## find-callers/find-callees --json

```json
{"roots": [node...], "truncated": bool, "omitted": int, "notes": [str]}
```

node = `{"file", "symbol"|null, "line"|null, "relationship", "confidence",
"receiver"|null, "children": [node...]}`.

`truncated` is the completeness flag: `true` when the answer is incomplete —
the node cap refused an edge, or the depth cap stopped a frontier that still
had unvisited edges. `omitted` is an `int` counting the edges the walk
examined and refused at the **node** cap; it is a lower bound, because a
refused edge is never expanded and so its own edges are not walked. It is
`0` on a depth-capped answer, so `truncated: true` with `omitted: 0` is a
normal, reachable combination: decide completeness from `truncated`, never
from `omitted`.

`notes` is a `[str]` of plain-language lines for the caller to print, and is
present only when non-empty: on an incomplete answer it carries the cap
lines, on a complete one the explanations (a dotted subject resolved, an
unknown definition seeded from reference rows). All three keys are additive
siblings — `roots` keeps its shape and a consumer that ignores them still
reads the same tree.

## deps --json

```json
{"out": {level: [file...]}, "in": {level: [file...]},
 "truncated": bool, "omitted": int, "notes": [str]}
```

level = 1-based. `truncated`, `omitted` and `notes` mean exactly what they
mean for `find-callers` (above): node-cap refusals are counted in `omitted`,
a depth-capped answer is `truncated` with `omitted: 0`, and `notes` is
present only when non-empty. A refusal line names its direction
(`node cap 2 reached (out direction) — ...`) because one answer can carry
refusals from both walks; `omitted` is the sum over both. A direction that
was not requested keeps its empty dict.

## Error contract (non-JSON)

Failures print exactly one line `ErrorType: description` to stderr:
ArgumentError, ConfigError, NotFoundError, BackendError, LockedError,
InternalError. Exit codes: 0 success, 1 failure, 2 usage (argument) error.
The only non-verbose stderr output on success is the documented single-line
stale-file Warning.