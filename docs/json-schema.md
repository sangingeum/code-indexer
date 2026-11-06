# JSON output schema (version 1)

Every JSON-emitting command includes `"schema": 1` so consumers can pin a
contract. Field names are stable; new fields may be added (append-only), but
existing names never change meaning.

## semantic-search --json

```json
{"schema": 1, "hits": [hit...], "truncated": bool, "dropped": int,
 "skipped": [{"project": str, "reason": str}...], "skipped_count": int,
 "partial": bool, <staleness block>}
```

`skipped` names the projects whose index could not answer (or that the 30 s
wall did not reach — `reason: "budget"`), so a JSON consumer can tell a
partial result from a complete one; `skipped_count` is its length and
`partial` is true when it is non-empty. All are always present (empty list /
0 / false when nothing was skipped). The staleness block is the one described
below; with `--all-projects` it is the aggregate plus a `staleness` array of
per-project blocks.

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
 "segments": [{"start_line": int, "end_line": int, "lines": [str],
               "symbol_rows": int,   (symbol mode only)
               "error": str}]}       (instead of "lines", when the start
                                      line is past the end of the file)
```

`lines` holds the rendered `NNNNN| text` rows of the segment, in file order.
A segment whose `start_line` is beyond the end of the file carries `error` in
place of `lines`.

`symbol_rows` is the number of stored symbol rows the span merged — 2 for a
decorated definition, 1 for a plain one. It appears in `--symbol` mode only,
where the merge happens: line-range mode merges nothing and its segment shape
is unchanged. The field is additive; `schema` stays `1`.

## overview --json

Project map dict (project, files, loc, languages, directories,
entry_points, tests, config_files, hotspots) + `schema: 1`.

`hotspots.fan_in` is `[{"file": str, "fan_in": int}]`, the top ten by a
**complete** per-file count: resolved imports into the file plus references
to a name that file defines, self-references excluded. It is a count, never a
sample of the reference table, so two runs over one index agree exactly.
Under `--path-prefix` the count is scoped to the files being reported on: a
name defined both inside and outside the prefix belongs to its in-prefix
definition, so the prefix keeps the references it owns.

## changed-symbols --json

The changed map plus `schema`:

```json
{"schema": 1, "<file>": {"kind": str, "symbols": [sym...],
 "removed_symbols": [sym...], "files": [str]},
 "impact": {"<file>": {"callers": [caller...], "candidate_tests": [str],
                       "truncated": bool, "omitted": int,
                       "bounds": {"per_name": int, "node_budget": int,
                                  "max_nodes": int}}}}
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

`bounds` splits that total by which bound refused what, because the two have
different remedies — `per_name` rows were cut by the fixed 50-callers-per-
symbol read (raise `--max-nodes` does not help; the walk that answers one
symbol's fan-in is `find-callers <symbol> --max-nodes N`, with `N` above the
count), `node_budget` rows by the shared `--max-nodes`
budget. The text output names the bound that fired rather than advising both.

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
the node cap refused an edge, the depth cap stopped a frontier that still had
unvisited edges, or the definition-root list was itself cut. `omitted` is an
`int` counting the edges the walk examined and refused at the **node** cap; it
is a lower bound, because a refused edge is never expanded and so its own
edges are not walked.

Both walks read EVERY definition of the subject and then spend the node budget
on those roots. When the root set alone exceeds `--max-nodes` the payload
carries the first `cap` roots, `truncated: true`, and a note naming the real
situation (`40 definition roots exist for this subject, node cap 10 — 30 roots
not shown; raise --max-nodes`). Those refused rows are definitions, not edges,
so they are NOT counted in `omitted`. `omitted` may still be positive on the
same answer: roots that spend the whole budget leave no slot for level-0
edges, and those refusals are counted (measured on this tool's own index at
`--max-nodes 10`: `roots 10, truncated: true, omitted: 124`). So
`truncated: true` with `omitted: 0` is one reachable pair — the depth-capped
answer — and not the shape of a root-capped one: decide completeness from
`truncated`, never from `omitted`.

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

## The staleness block (every read answer)

Every JSON answer from a command that reads the index is additive with this
block (`schema` stays `1`):

```json
{"stale": bool, "stale_known": bool,
 "stale_reasons": [{"kind": "modified|added|deleted", "file": str}],
 "stale_reason_count": int, "index_revision": str|null,
 "last_indexed": str|null}
```

`stale_known` false means the verdict is `unknown` (project path absent, no
completed pass, or the scan exceeded its budget), so `stale: false` alone
never means "checked and fresh". `stale_reasons` is capped at five while
`stale_reason_count` is the total before the cap. `index_revision` names the
pass the answer was read from and `last_indexed` is that pass's timestamp in
ISO-8601 UTC.

`get-code-context --json` keeps `stale` as its FILE-level field and adds the
other five fields; the file-level warning line is unchanged.

## list-projects --json

```json
{"schema": 1, "projects": [project...],
 "stale": bool, "stale_known": bool, "stale_reasons": [...],
 "stale_reason_count": int, "index_revision": null, "last_indexed": null}
```

project = `{"path", "slug", "state", "files", "chunks",
"last_indexed", "reason"|null, "collection"|"missing"|null, "note"|null}`
plus that project's own staleness block. The top-level block aggregates them:
`stale` is true when any project is stale and `stale_known` is true only when
every verdict was reachable.

## lookup-project --json

```json
{"schema": 1, "registered": bool, "path": str,
 "slug"|missing, "state"|missing, "files"|missing, "chunks"|missing,
 "last_indexed"|missing, "collection"|missing,
 <staleness block>}
```

An unregistered selector answers `registered: false` with its `path` and an
`unknown` staleness block (exit 0 — it is a check, not an error).

## index-status --json

```json
{"schema": 1, "project": str, "state": str,
 "rows": "current"|"absent", "vectors": "on"|"off",
 "vectors_state": "current"|"absent"|"stale"|"error",
 "fingerprint"|"needs-reindex"|"unverified", "reason"|missing,
 "last_pass"|missing, "progress"|missing, "sensitive_skipped"|missing,
 "scope"|missing, "not_indexed"|missing, "last_errors"|missing,
 "parsed": int, "parse_error": int, "parse_unavailable": int,
 "note"|missing, <staleness block>}
```

The fields the text line prints, under the same names, plus the staleness
block. A missing `fingerprint` field still means the index matched. The three
`parse_*` counts are the per-file parse verdicts (text:
`parse=parsed:N,parse_error:N,parse_unavailable:N`) and are always all three,
zero included — a verdict is the parser's own result, never the chunker's
fallback. `index-status` answers on a `needs-reindex` project by contract.

The three vector fields are read from the manifest alone — a status read never
dials Qdrant. `rows` is `current` only when this build's extraction wrote the
stored rows; `vectors` is the recorded mode; `vectors_state` is `stale` when
the recorded embedding identity is not the one this build would write,
`absent` on a project that was never vectored, `error` when the last embedding
pass lost chunks, and `current` otherwise.

## find-symbol --json

```json
{"schema": 1, "symbols": [{"file", "name", "type",
                  "start_line", "end_line", "signature",
                  "qualified_name", "container", "language",
                  "decl_hash", "symbol_id"}],
 "match": "exact"|"substring"|"browse", <staleness block>}
```

`schema` stays 1: the identity fields are additive. `container` is `null` at
top level; `decl_hash` is `sha256:`-prefixed; `symbol_id` is the
revision-scoped surrogate and belongs to the `index_revision` in the staleness
block — it is not a durable identifier. No rows is `symbols: []` with exit 0.

## find-definition --json

```json
{"schema": 1, "name": str, "project": str,
 "definitions": [{"file", "name", "type", "start_line", "end_line",
                  "confidence": "exact"|"heuristic",
                  "qualified_name", "container", "language",
                  "decl_hash", "symbol_id"}],
 <staleness block>}
```

No definitions is `definitions: []` with exit 0 (an answer, not a failure).

## find-references --json

```json
{"schema": 1, "name": str, "project": str, "relationship": str|null,
 "references": [{"file", "line", "from_symbol", "target",
                  "relationship", "confidence", "receiver",
                  "project", "target_confidence"}],
 "total": int, "unfiltered_count"|missing,
 "unfiltered_relationships"|missing, <staleness block>}
```

The two `unfiltered_*` fields appear only when a relationship filter came
back empty and the store does carry the name under another relationship.

## Error contract (non-JSON)

Failures print exactly one line `ErrorType: description` to stderr:
ArgumentError, ConfigError, NotFoundError, BackendError,
RebuildRequiredError, LockedError, InternalError. Exit codes: 0 success
(including zero hits), 1 other runtime failure, 2 usage (argument) error,
3 backend or extension unavailable, 4 the index requires a rebuild, 5 lock
or concurrency conflict. The only non-verbose stderr output on success is
the documented single-line staleness Warning.

## Error contract (--json)

With `--json`, a failure prints one JSON object to stdout carrying
`"schema": 1`, `"ErrorType"` and `"error"`, and exits with the taxonomy's
code — **except a `needs-reindex` refusal**, which stays a refusal on every
surface and in every mode: stdout is EMPTY, one `RebuildRequiredError:` line
(names `reindex-project` for that project) goes to stderr, and the exit is 4.
A machine-readable mode never turns "these rows are obsolete" into a payload
that looks like an answer.

stdout is still empty for the non-JSON read commands (a failed
`semantic-search` writes its one `ErrorType: message` line to stderr).