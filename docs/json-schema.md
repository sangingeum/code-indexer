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
0 / false when nothing was skipped). `skipped_count` here counts PROJECTS
only; the files the sensitive layer withholds are counted in the staleness
block as `withheld_count`, so the two facts never share a key. The staleness
block is the one described
below; with `--all-projects` it is the aggregate plus a `staleness` array of
per-project blocks.

hit (search and hybrid fusion share the contract):

```json
{"project": str, "file": str, "score": float,
 "symbol": str|null, "symbol_type": str|null, "lang": str,
 "start_line": int, "end_line": int, "snippet": str, "identity": str,
 "match": "symbol"|"lexical"|"fused"|missing,
 "rerank_delta": float|missing, "merged_count": int|missing}
```

`identity` is what the hit matched, and it is never an empty string: the
`symbol` when the chunk has one, else the first non-blank line of `snippet`,
else the hit's own `file` path -- the path is always known, so two such hits
never carry the same constant. Only a hit that carries no path at all falls
back to the literal `"file"`. Text and compact output print the same value in
the symbol position, so a scripted caller reads the name an agent reads.

Ordering: file class first (source, examples, tests, docs, generated/artifact),
then the order the ranking mode produced (cosine, adjusted score, fused score,
bm25, or RRF) -- see `skill/references/query.md` (Ordering). `match` appears
only in lexical/hybrid modes; `rerank_delta` only when the reranker is
enabled; `merged_count` on same-file merged hits.

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
attributed to a definition the file holds, self-references excluded. Each
reference credits the definition it RESOLVES to (`refs.target_symbol_id`,
written by the index pass): a definition in the referencing file first; else
the store's only definition of the name; else, among several definitions, the
one the file-class ladder strictly prefers (source above examples, tests,
documents and artifacts -- so a test module that mirrors a source name does
not take that name's references); a remaining tie is left unattributed. It is
a count, never a sample of the reference table, so two runs over one index
agree exactly.

`hotspots.unattributed` is `int`: how many reference rows no rule attributed
to a definition. They are credited to no file and never folded into an
arbitrary definer. The count is split by WHY, additively: `unresolved` is the
rows whose name the store defines nowhere, and `ambiguous` the rows whose
name has several definitions and no same-file match, so the two sum to
`unattributed`.

`unresolved` is itself split, because the two halves answer different
questions: `unresolved_language_without_symbols` counts the rows that sit in
files whose language holds no symbol row anywhere in the index — no reader
there can ever match them, so they say nothing about whether the index is
complete — and `unresolved_missing_definition` counts the rest, rows in a
language whose files do define symbols, so the name really is missing a
definition. The two sum to `unresolved`.

The text form prints `unattributed: N references credited to no file (U names
defined nowhere: L sit in files whose language holds no symbol row at all, so
nothing there could ever match them, and M name a definition their language
does define; A tied between definitions)` when the total is non-zero, and the
compact form carries `hotspot-unattributed references=N unresolved=U
unresolved_missing_definition=M unresolved_language_without_symbols=L
ambiguous=A`.

Under `--path-prefix` the count is scoped to the files being reported on:
only target files inside the prefix are counted, and `unattributed` counts
the reference rows that SIT inside it. The prefix filters by the RESOLVED
target: it never re-attributes a reference to a definition outside the
prefix, so a name whose references resolve outside it reports no fan-in
inside the prefix (and a reference from outside still counts toward a
target inside). An index built before per-definition
attribution answers from the old per-name aggregate -- the row reads report
`needs-reindex` instead, until `reindex-project` writes the resolved targets.

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

Each reachability probe (Ollama, Qdrant) carries its own wall bound:
`--probe-timeout` seconds, default 5 (the `DOCTOR_PROBE_TIMEOUT` environment
variable sets the default). Five seconds is a starting point rather than a
measured threshold -- a healthy LAN handshake is milliseconds, and two
blackholed services then cost about ten seconds instead of the client
libraries' own minutes each; a deployment with a slow peer raises it. An
expired wait is reported honestly as
`fail <check>: <url>: timed out after <n>s` and keeps the non-zero exit -- a
timeout is never a pass. Text mode prints one line per check, flushed as each
section returns, so the local checks are visible while a probe is still
dialling; `--json` is one document, so its payload is written at the end.

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
 "stale_reason_count": int,
 "withheld_count": int,
 "withheld_files": [{"file": str,
                     "reason": "sensitive filename|sensitive content"}],
 "withheld_summary": str|null,
 "withheld_unexamined_count": int,
 "unreadable_count": int,
 "unreadable_files": [{"file": str}],
 "unreadable_summary": str|null,
 "index_revision": str|null, "last_indexed": str|null}
```

`stale_known` false means the verdict is `unknown` (project path absent, no
completed pass, or the scan exceeded its budget), so `stale: false` alone
never means "checked and fresh". `stale_reasons` is capped at five while
`stale_reason_count` is the total before the cap. `index_revision` names the
pass the answer was read from and `last_indexed` is that pass's timestamp in
ISO-8601 UTC.

`withheld_count`, `withheld_files` and `withheld_summary` describe files the
sensitive layer withholds: they are scanned and deliberately rowless, so they
are neither stale reasons nor silently absent. `withheld_files` is capped at
five while `withheld_count` is the total; each entry names the path and the
layer (`sensitive filename` | `sensitive content`), never the material. The
fields are present on a `fresh` verdict, so "nothing changed" and "one file
withheld on purpose" never read alike.

`withheld_unexamined_count` is the part of the re-check's budget that ran
out: those rowless paths are reported as `added` and nothing is known about
their content, so a non-zero value means the re-check was truncated. On a
project registered with `--allow-sensitive` the pass withholds nothing, so
nothing is classified and every rowless path is an `added` reason -- the row
the next pass will write.

`unreadable_count`, `unreadable_files` and `unreadable_summary` describe the
rowless paths the check could not OPEN (permissions, a broken symlink target,
an I/O error). The check could not look at them, so they are neither stale
reasons nor withheld files -- an `added` reason would say the path is new and a
reindex would never clear it. They are counted, listed (capped at five) and
phrased like the withheld fields, on a `fresh` verdict too, and the text line
says so even when the verdict is fresh: `1 file could not be read (permission
or I/O error), so part of the tree was not checked`. The state reports what
the diff saw, and no more: the scanner drops a path it cannot open, so the
check only ever sees a path the scan returned -- a file already unreadable
when the tree was walked (a stable `chmod 000`, a dangling symlink) never
reaches the diff and is not counted.

Consumer rule: `stale: false` with a non-zero `withheld_count` is not
"nothing was withheld", and `stale` alone says nothing about withheld files.
Branch on `withheld_count`, not on `stale`. The same rule holds for
`unreadable_count`: a fresh verdict with a non-zero count means part of the
tree the scan returned was never compared -- not that every unreadable path on
disk was reported.

`get-code-context --json` keeps `stale` as its FILE-level field and adds the
rest of this block; the file-level warning line is unchanged.

## The coverage block (every read answer)

Every read command's JSON carries one `coverage` object beside the staleness
block. It is what the answer could see, so a caller never has to run
`index-status` to decide whether to trust it:

```json
{"scope": "project|answer|none",
 "files": int|null, "files_parsed": int|null,
 "files_not_parsed": int|null, "files_withheld": int|null,
 "files_unreadable": int|null,
 "answer_languages_without_ast": [str],
 "freshness": "fresh|stale|unknown"|null,
 "complete": bool|null}
```

`scope` says which files the answer covered: `project` (the whole index)
`answer` (the files this answer actually read -- `outline`,
`get-code-context`, `find-symbol --file` and the `file-outline` alias), or
`none` (the command reads no index: `doctor`, `list-projects`,
the eval reports, `plan-show`, `plan-validate`, and an unregistered
`lookup-project`). A `none` block carries null counts rather than zeros:
zero would claim "nothing to parse" about an answer that never looked.

`skeleton` is not answer-scoped, and neither is its hidden `map` alias: both
walk the whole index and report `project` scope.

`files_not_parsed` is `parse_error` + `parse_unavailable` + rows with no
recorded verdict. `files_withheld` comes from the staleness block's withheld
list, intersected with the answer's scope when the scope names paths.
`files_unreadable` comes from the staleness block's unreadable list the same
way: rowless paths the check could not open, which are neither parsed nor
withheld. A path the scanner itself dropped never reaches that list, so this
count is what the diff saw, not an inventory of the disk.
`answer_languages_without_ast` lists the languages IN SCOPE whose files yield
no AST symbol rows -- the language-capability matrix's own runtime probe -- so
a project whose only shell files parsed still says `["shell"]`. It answers a
different question from `index-status`'s `registry_languages_without_ast`
(every language the REGISTRY knows that has no AST reader), and the two never
share a key: one field name carries one meaning in one document.
`freshness` repeats the staleness verdict (`fresh` | `stale` | `unknown`),
never a second estimate. `complete` is true only when every file the scan
returned in scope parsed and none was withheld or unreadable -- nothing the
scan returned is missing; a path the scanner could not open never entered the
scan and is not counted. It is null when `scope` is `none`. `complete`
is about parsed-versus-unparsed files, not about the index holding anything:
on an index that holds no files the counts are 0 and `complete` is vacuously
true, so read `files: 0` (or `empty_reason: index-empty`) and not
`complete`.

## empty_reason (a name read that found nothing)

The name reads -- `find-symbol`, `find-definition`, `find-references`,
`find-callers`, `find-callees` and `semantic-search` -- add two fields to an
empty answer, and nothing to a non-empty one:

```json
{"empty_reason": "name-absent|files-not-parsed|language-without-ast|index-empty",
 "empty_reason_sentence": str}
```

`name-absent` means the index parsed files whose language yields symbols and
none of them holds the name. `files-not-parsed` means no such file parsed and
at least one did not; the sentence names the count and the languages.
`language-without-ast` means the files parsed but their language has no AST
reader, so no symbol is extracted; the sentence names the language.
`index-empty` means the project is registered and the index holds no files.
The precedence is fixed in that order, so one condition never reports two
values. `empty_reason_sentence` is the same sentence the text form prints,
with the command's own lead (`no symbols matching 'X'`, `no references to
'X'`, `no results`) in front. The four VALUES are shared; the sentence is
worded per surface, because the symbol reads look for a NAME and
`semantic-search` looks for text -- a search with no hits says
`no results: nothing in an index that parsed N of M files matched the query`,
never "the name is absent". Exit codes do not change: an empty answer is
not an error.

Neither field appears on a `semantic-search --all-projects` fan-out: the
vocabulary describes ONE index, and a fan-out has no single index to describe.
That answer keeps `"hits": []`, its `coverage` block is `scope: none` with
null counts, and the text form prints the bare legacy `no results`; per-project
reasons are the honest form and are not fabricated for the fan-out.

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
 "scope"|missing, "not_indexed"|missing, "error_count"|missing,
 "last_errors"|missing,
 "parsed": int, "parse_error": int, "parse_unavailable": int,
 "parse_error_files": [str],
 "registry_languages_without_ast": [str], "scan_accounting"|missing,
 "fingerprint_meaning"|missing, "note"|missing, "coverage": {...},
 <staleness block>}
```

`fingerprint_meaning` is the human half of the `fingerprint`/`reason` pair,
and it follows the verdict printed beside it: one run compares the ROW keys
and the VECTOR identity, and the sentence branches on mode + marker +
identity, in that order. Each row below is one branch of
`fingerprint.meaning` -- the ids are that function's own list
(`MEANING_BRANCHES`), so a branch cannot ship without a row:

| branch | reached when | the sentence says |
|---|---|---|
| `row-keys-differ` | a row key differs: `fingerprint: needs-reindex`, and `reason` names it | the named row key (`chunker_version: 1 -> 2`) makes the row reads refuse until `reindex-project`; it never says every key matched, and with `vectors=on` it names a stale vector side too when one is true |
| `vectors-on-identity-matched` | `fingerprint: unverified`, `vectors: on`, the identity matches (no recorded identity key differs), `vectors_state`: `current`, `absent` or `error` | every fingerprint key a read can compare matched -- embedding model, embedded-text format, chunker and extraction version -- and the recorded embedding model and format are this build's; the DIMENSION is the one key left uncompared, because learning the configured dimension needs the service, so vectors of another dimension recorded under the same model name would not be flagged there |
| `vectors-on-identity-differs` | `fingerprint: unverified`, `vectors: on`, a recorded identity key differs (`vectors_state: stale` -- the read derives that marker from this comparison) | a key did not match, and the sentence names it (`embed_model: a -> b`, `embed_text_version: a -> b`): the vector side is stale and a vector rebuild is what clears it |
| `vectors-on-marker-stale` | `fingerprint: unverified`, `vectors: on`, the identity matches, and the manifest still records `vectors_state: stale` (a marker this build never writes -- see below) | the MANIFEST's record is what is stale: no key differs, so none is named, and a vector rebuild is still the clearing pass |
| `vectors-off-identity-differs` | `fingerprint: unverified`, `vectors: off`, a recorded identity key differs (with vectors off the line prints `vectors_state` as recorded, so it may read `current` here) | there is no vector side -- no vectors are stored and no vector query runs -- so nothing can be stale and no rebuild is prescribed; the difference is an inert fact about the next vectors-on pass, which writes its own collection, and a recorded `stale` marker is inert too |
| `vectors-off-identity-matched` | `fingerprint: unverified`, `vectors: off`, the identity matches | every fingerprint key a read can compare matched, and the dimension is the one key it cannot compare; there is no vector side here, so no rebuild is prescribed |
| `no-verdict-recorded` | no `fingerprint` field: the index records no fingerprint, or an incomplete one | the comparison did not run, which is not a pass; with `vectors: off` it adds that there is no vector side either |

A recorded ROW key that differs is `needs-reindex` even when no COMPLETE
fingerprint was recorded: a vectors-off pass writes only the row keys, so the
full-key comparison has nothing to read, and the recorded chunker/extraction
version is compared on its own (`extraction_version: 5 -> 6`) — the rows are
obsolete either way. A manifest that recorded no row key at all still reaches
`no-verdict-recorded`; the next pass backfills it.

`vectors_state` is the marker the manifest RECORDS, never a second
comparison: on a vectors-on project the read derives `stale` from the
identity comparison, and on a vectors-off project it prints the recorded
marker as it stands. So "`vectors_state` is not `stale`" does not mean every
key matched -- on a project kept `vectors=off` under a different `EMBED_MODEL`
the line prints `vectors=off vectors_state=current` and the sentence says the
recorded identity differs.

`stale` is accepted and documented, and no pass in this build writes it: it
is the value the read derives from the identity comparison, and a manifest
that records it is reported as it stands. An
`error` marker (the last vector pass lost chunks to embed failures) is not a
fingerprint branch, and the sentence does not speak to it -- the sentence
describes the key comparison, so the error verdict is the line's own field to
read. When the manifest records chunk errors the line carries
`error_count=N`, the total recorded, beside `last_errors=`, which shows only
the three most recent; `--json` carries the same `error_count` and the
`last_errors` list. A project with no error rows prints neither field.

The text form prints the sentence as one stderr line beside the one-line
answer, which keeps its single-line shape. The field names of the pair are
unchanged.

The fields the text line prints, under the same names, plus the staleness
block and the coverage object. `sensitive_skipped` is the exception: it is JSON-only, counts what the
LAST pass withheld, and never rides the line -- the line carries this tree's
count, from the staleness block. A missing `fingerprint` field means the
comparison did not run (the index records no fingerprint), and
`fingerprint_meaning` says so. The three
`parse_*` counts are the per-file parse verdicts (text:
`parse=parsed:N,parse_error:N,parse_unavailable:N`) and are always all three,
zero included — a verdict is the parser's own result, never the chunker's
fallback. `parse_error_files` names the files behind the `parse_error` count,
because a count a reader cannot act on is half an answer. `index-status`
answers on a `needs-reindex` project by contract.

`scan_accounting` is the last pass's reconciliation of the tree against the
index: `{walked, indexed, uncommitted, skipped, skipped_total,
skipped_directories, examples, counted_exactly}`. `walked` counts the file
entries the walk considered, including files inside subtrees it pruned as
ignored; every one of them is `indexed`, `uncommitted` or counted in
`skipped` under a named reason — `ignored`,
`ignored-directory`, `oversized`, `binary`, `non-utf8`, `unreadable`,
`out-of-scope`, `sensitive` — so `walked == indexed + uncommitted +
skipped_total` always holds. `indexed` is what the manifest holds after the
pass, and `uncommitted` is a file the pass read and could not store because
every one of its chunks failed to embed (a later pass retries it). A file the
pass cannot re-open after the scan is not uncommitted: it is a skip under
`unreadable`, and the same reason is recorded as the pass's error for it. `examples` names the first few paths of each
kind (capped), which is what lets `outline` answer why a file it cannot find
was left out. `counted_exactly` is false only when a pruned subtree was too
large for the counting budget, which makes `walked` a floor. The field is
missing when no pass has recorded one, and `overview` carries the same object
under the same name.

The three vector fields are read from the manifest alone — a status read never
dials Qdrant. `rows` is `current` only when this build's extraction wrote the
stored rows; `vectors` is the recorded mode; `vectors_state` is `stale` when
the recorded embedding identity is not the one this build would write,
`absent` on a project that was never vectored, `error` when the last embedding
pass lost chunks, and `current` otherwise.

## add-project / index-more / remove-project / reindex-project --json

The four pass commands report their own work rather than a verdict about the
index, so these objects carry no staleness block.

```json
add-project:      {"schema": 1, "command": "add-project", "path": str,
                   "slug": str, "name": str|null, "registered": true,
                   "already_registered": bool, "vectors": "auto"|"on"|"off",
                   "index": pass|null, "notes": [str]}
index-more:       {"schema": 1, "command": "index-more", "path": str,
                   "slug": str, "scope_include": [str], "index": pass}
remove-project:   {"schema": 1, "command": "remove-project", "path": str,
                   "slug": str, "removed": true, "collections": [str],
                   "alias": str, "alias_dropped": bool, "notes": [str]}
reindex-project:  {"schema": 1, "command": "reindex-project", "path": str,
                   "slug": str, "vectors": "auto"|"on"|"off",
                   "index": pass}
pass:             {"slug": str, "state": "idle"|"indexing"|"error",
                   "result": {...} | "detail": str | "error": str}
```

`state: "indexing"` means another process held the project's flock and is
performing the pass (exactly-one-pass rule); `detail` says so and `result` is
absent. `state: "idle"` is a completed pass and carries the pass counters.
`already_registered` is true when the registration was a no-op, and then
`index` is `null`. A failure carries `ErrorType` and the taxonomy's exit code,
like every other `--json` failure. Fields are additive and `schema` stays 1;
the text renderer prints the same fields as prose lines.

## find-symbol --json

```json
{"schema": 1, "symbols": [{"file", "name", "type",
                  "start_line", "end_line", "signature",
                  "qualified_name", "container", "language",
                  "decl_hash", "symbol_id",
                  "evidence": "ast"|"heuristic"|"unknown"}],
 "match": "exact"|"substring"|"browse",
 "match_count": int, "truncated": bool,
 "ambiguity"|"missing": {"candidates": [{"file", "range":
                  {"start_line", "end_line"}, "kind", "container",
                  "signature", "language", "decl_hash",
                  "qualified_name", "evidence"}]},
 "evidence_note"|"missing": str,
 "empty_reason"|"missing": "name-absent|files-not-parsed|
                  language-without-ast|index-empty",
 "empty_reason_sentence"|"missing": str,
 "coverage": {...}, <staleness block>}
```

`schema` stays 1: every field here is additive. `container` is `null` at
top level; `decl_hash` is `sha256:`-prefixed; `symbol_id` is the
revision-scoped surrogate and belongs to the `index_revision` in the staleness
block — it is not a durable identifier. No rows is `symbols: []` with exit 0.

`evidence` is a property of the file the row came from, not of the query.
Every symbol row is `ast`: a row exists only for a declaration parsed from
source with an AST, and a file with no AST reader contributes no symbol rows.
`unknown` therefore describes no symbol row any more (it stays in the
vocabulary for older readers and for non-symbol surfaces), and `heuristic` is
reserved with no producer. An unparsed file is reached by semantic search
over its chunks, and `find-definition` reports `files-not-parsed` rather than
an `unknown` row.

`match_count` is the uncapped number of rows the same filters match, and
`truncated` is true when `--limit` showed fewer. `ambiguity` appears in
exact-name mode when `match_count` exceeds one, and lists every candidate;
the answer never resolves the name to one row.

`evidence_note` appears when `--inherits` or `--has-call` came back empty: it
names how many files holding symbols have no AST extractor, so an empty
answer reads as "not attributed", not "no".

### structural filters

`--kind` (with `--type`/`--symbol-type` as aliases), `--name-pattern` (a
case-sensitive GLOB over the name), `--file`, `--container`, `--visibility`
(NULL reads as public), `--language`, `--inherits NAME` and `--has-call NAME`
compose as AND in one read. `--inherits`/`--has-call` answer from the
`refs` rows whose `from_symbol` is this symbol's own name in its own file,
attributed syntax only. That join is **name-level by design**: `from_symbol`
carries no container, so two same-named symbols in one file are both credited
with the union of both bodies' attributed references. Narrowing it needs
reference resolution to a container (the staged work the identity model's
section 3 schedules); until then a hit means "this file's `name` does this",
not "this exact symbol does".

## find-definition --json

```json
{"schema": 1, "name": str, "project": str,
 "definitions": [{"file", "name", "type", "start_line", "end_line",
                  "confidence": "exact"|"heuristic",
                  "qualified_name", "container", "language",
                  "decl_hash", "symbol_id"}],
                  "empty_reason"|"missing": str, "empty_reason_sentence"|"missing": str,
                  "coverage": {...}, <staleness block>}
                  ```

                  No definitions is `definitions: []` with exit 0 (an answer, not a failure);
                  the empty object then also names the cause, exactly as `find-symbol` does.

With `--exactly-one`, a name with more than one definition is refused: exit 2,
one `ArgumentError:` line, and — in `--json` — `{"schema": 1,
"ErrorType": "ArgumentError", "error": str, "candidates": [<the ambiguity
candidate records>]}`. A single definition answers as usual.

`--exactly-one` changes the EMPTY answer too, deliberately: with no definition
at all it is `NotFoundError: no indexed definition for '<name>'` — exit 1,
empty stdout, the usual one-line failure shape — where without the flag the
same query is `definitions: []` and exit 0. The flag means "I must act on
exactly one symbol", so a caller that asked for one is told it cannot have
one rather than handed an empty set to inspect. This is the documented choice
(not an alignment with the other refusals, which are `ArgumentError`, exit 2,
because the name is ambiguous rather than missing).

## find-callers / find-callees --json

```json
{"roots": [{"file", "symbol", "line", "relationship", "confidence",
           "receiver", "targets": [str], "children": [...]}],
 "truncated": bool, "omitted": int, "match_count": int,
 "ambiguity"|"missing": {"candidates": [...]}, "notes"|"missing": [str]}
```

`match_count` is the number of definition rows the subject name resolves to;
`ambiguity` appears when that exceeds one, with the same candidate records as
`find-symbol`. The walk unions over every candidate, and each DIRECT hit
carries `targets` — the `file:start-end` candidate label(s) it can belong to.
An import-resolved edge is attributed to the candidate in its file; a
name-level reference cannot be attributed, so it names every candidate. A
root's `confidence` is the evidence of the row it was built from (`ast` or
`unknown`), never a constant — the same label the `ambiguity` candidate for
that row carries. Text mode prints the tree, the `targets` labels and one
line saying the subject name matched several definitions.
`targets` is empty when the subject has one definition (nothing to pick) and
on transitive nodes.

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

## plan-replace-body / plan-insert-symbol / plan-delete-declaration --json

```json
{"schema": 1, "plan_id": "sha256:<hex>", "plan_path": str|null,
 "document": {<the plan document>}}
```

`plan_path` is the file written; it is `null` only when `--print` was used,
and `--print` then emits the document's canonical bytes alone on stdout (no
wrapper, no trailing newline).

## The plan document

Canonical bytes: UTF-8, keys sorted, no insignificant whitespace, no trailing
newline, a newline inside a string always encoded as `\n`. `plan_id` is
`sha256:<hex>` over those bytes with the `plan_id` member absent, then written
in; the file is `<plan-dir>/<hex>.json`.

```json
{"schema_version": 1,
 "plan_id": "sha256:<hex>",
 "operation": "replace_body|insert_symbol|delete_declaration",
 "repository": {"project": str, "index_revision": str},
 "created_at": "YYYY-MM-DDTHH:MM:SSZ",
 "provider": {"name": str, "version": str},
 "target": {"path": str, "language": str, "qualified_name": str,
            "symbol_type": str, "decl_hash": "sha256:<hex>",
            "range": {"start_line": int, "end_line": int,
                      "start_byte": int, "end_byte": int}},
 "files": [{"path": str, "expected_hash": "sha256:<hex>",
            "newline": "lf|crlf", "language": str}],
 "edits": [<edit>],
 "validation": {"syntax_required": bool, "semantic_required": bool}}
```

Everything above is required and nothing else is. `notes`, `diagnostics[]` and
`warnings[]` are optional; a reader ignores unknown members and `plan-show`
preserves them, and no command rewrites a plan.

`provider` names whichever produced the edits. A syntax-only plan names the
structural producer, `{name, version}`. A plan built from provider edits
carries that provider's own member -- `{name, version, transport}` as ADR-0006
section 4 states -- with `conversion_version` beside it, recording which
conversion placed the positions (section 5). The member's key set therefore
depends on the producer, and the reader requires a `provider` object rather
than a fixed set of keys inside it.

An edit record:

```json
{"kind": "replace_body|delete_declaration|insert_symbol",
 "path": str,
 "start_line": int, "end_line": int,
 "start_byte": int, "end_byte": int,
 "expected_old_text_hash": "sha256:<hex>",
 "expected_old_text": str,          // optional, omitted above 8 KiB
 "new_text": str,                   // present for replace_body and insert_symbol
 "position": "before|after|end",    // insert_symbol only
 "note": str}                       // optional
```

`position: end` carries no `start_line`, `start_byte` or `end_byte`: insertion
after the last line is never a line number. For the other insertions the record
carries no `end_line`, and its byte span is empty (`start_byte == end_byte`) at
the line boundary, with `start_line` the first line of the inserted block. Lines
are 1-based and
inclusive, a terminator is not part of its line, and every byte offset indexes
the stored bytes after any BOM.

## plan-validate --json

```json
{"schema": 1, "plan_id": str, "operation": str, "ref": str|null,
 "identity": {"verified": "structural",
              "basis": ["file_hash", "decl_hash", "region_hash"]},
 "syntax": [{"path": str, "verdict": "parsed|parse_error|parse_unavailable"}],
 "checks": [{"check": str, "status": "ok|failed|reported", "detail": str}],
 "passed": bool,
 "caveat": str,
 "ErrorType": str, "error": str, "exit_code": int}
```

The last three appear only when a check refused. `passed` means the structural
checks passed; `caveat` says so in words ("structural checks only ... nothing
here claims the result compiles or type-checks"). Check names in order: plan
hash, schema, requests, paths, file bytes, file hash and newline declaration,
replacement text, node re-derivation, line and byte cross-check, region
hashes, overlap and ordering, syntax verdicts.

## plan-show --json

```json
{"schema": 1, "plan_id": str, "ref": str|null, "plan": {<the document>},
 "diff": [{"path": str, "diff": str}]}
```

`diff` appears only with `--diff`, and each `diff` is a unified diff computed
from the plan plus the current bytes with `--- a/<path>` / `+++ b/<path>`
headers.

## plan-apply --json

```json
{"schema": 1, "plan_id": str,
 "identity": {"verified": "structural",
              "basis": ["file_hash", "decl_hash", "region_hash"]},
 "dry_run": bool, "partial": bool,
 "files": [{"path": str,
            "status": "applied|would_apply|partially_applied|failed|not_attempted",
            "edits": [{"kind": str, "start_line": int|null,
                       "end_line": int|null, "position": str,
                       "operation_id": str|null,
                       "error_code": str, "error_message": str}]}],
 "operations": [str], "undo": [str], "receipt_path": str|null,
 "post_apply": [{"path": str,
                 "verdict": "parsed|parse_error|parse_unavailable",
                 "lines": [int]}],
 "refresh": str, "caveat": str,
 "ErrorType": str, "error": str, "exit_code": int}
```

`files` names every file in the plan, in ascending path order, and `edits`
holds the edits that were attempted, in application order. `operation_id` is
present (and `null` under `--dry-run`) on every attempted edit; `error_code`
and `error_message` appear only on the edit that failed. `position` appears
only for an insertion.

`operations` is the recovery reference in application order; `undo` is the
same list as the commands that reverse it, last operation first. `receipt_path`
is the JSON receipt written for a run that changed something, and `null` under
`--dry-run` or when nothing was written. `post_apply` covers the files that
were actually modified (empty under `--dry-run`), and `lines` is non-empty
only for `parse_error`. `refresh` is `skipped`, `ran`, or `failed (...)` — it
says which happened rather than assuming one.

`partial` is `true` whenever an operation ran -- a failure that left bytes
written, whether in earlier files or earlier edits of the same file. A file
takes `status: partially_applied` when some of its edits landed and a later
one was refused; its `edits` name the ones that landed by `operation_id`, so
the report says which edit was written, not just that the file is `failed`.
The last three members appear only when the run failed, and the exit code is
the taxonomy's: 5 for a stale pre-pass or a keeper 5, 1 for `PartialApply`
(something was written) or `KeeperFailure` (nothing was), 3 for
`KeeperUnavailable` or `KeeperCapabilityMissing`.

## Error contract (non-JSON)

Failures print exactly one line `ErrorType: description` to stderr:
ArgumentError, ConfigError, NotFoundError, BackendError,
RebuildRequiredError, LockedError, InternalError. Exit codes: 0 success
(including zero hits), 1 other runtime failure, 2 usage (argument) error,
3 backend or extension unavailable, 4 the index requires a rebuild, 5 lock
or concurrency conflict.

On success, a read command writes these lines to stderr, each with its own
condition and stream:

* the one-line `Warning: index is stale: ...` -- one per project whose
  freshness verdict is `stale` (or `unknown`, which has its own line), on
  **stderr**, text mode only, since the JSON answer carries the same verdict in
  its staleness block;
* the one `coverage:` line -- on **stderr** when the answer's coverage is
  incomplete and the answer is NON-empty; an empty answer prints it on
  **stdout** beside its cause, and a complete answer prints nothing at all;
* the `fingerprint meaning:` line -- `index-status` only, on **stderr** in text
  mode, whenever this build has a verdict to explain or has to say it has none;
  the same sentence is the `fingerprint_meaning` field in JSON;
* the `note: this index is scoped to ...` line -- `semantic-search` on a
  project indexed with a scope, on **stderr** in text mode, beside a
  successful answer at exit 0 (the note says out-of-scope paths are not
  indexed and how to extend the scope).
* the `note: ...` lines of a graph answer -- `find-callers` and
  `find-callees`, text mode, whenever the walk carries notes (a node or depth
  cap, a subject with no reference rows); on **stdout** when the answer is
  `truncated` OR empty, where the notes are part of what the reader is reading
  (an empty answer carries its explanation where it is read, as the coverage
  bullet says), and on **stderr** beside a complete, non-empty tree.
* the log note -- any WARNING the package's own logging handler emits during a
  read, with no prefix and no `ErrorType`: `get-code-context` on a file edited
  after its last index writes `file changed since last index; line numbers may
  be shifted` at exit 0, from the logger at `core.py`'s stale branch. On
  **stderr**, WARNING and above, both forms. A note is not an answer: stdout
  keeps its shape.

That is an enumeration of the cases a read DOCUMENTS, not a promise that
stderr is otherwise silent. A line reaches stderr from one of three kinds of
writer: a read's own note -- an echo whose stream the case chooses; the one
failure line from `errors.fail()`, which prints the single
`ErrorType: description` line on FAILURE; or a log record from the package's
logging handler, at WARNING by default, which a successful read fires in the
log-note case above. A line with neither a documented prefix nor an
`ErrorType` is a log note.

Lifecycle and maintenance commands are not reads and do not follow that
shape. `add-project`, `index-more`, `reindex-project` and `remove-project`
print their result and their notes on stdout in both forms; `sweep-orphans`,
`watch` and `unwatch` print their result on stdout and their notes on stderr;
and `eval --out` prints `wrote <path>` on stderr beside its report on stdout.
The reference that owns them, `skill/references/indexing.md`, lists the lines
each one writes.

A plan refusal names itself the same way and reuses those numbers: `PlanStale`
(5), `PathEscapesProject` / `OverlappingEdits` / `AmbiguousOrder` /
`AmbiguousNode` / `PlanFormatError` / `PlanHashMismatch` /
`SubLineEditUnsupported` / `CarriageReturnInPlan` (2), `LocatorMismatch` (5),
`NotUtf8` / `NewlineMismatch` / `MixedNewlines` / `PlanFormatError` (1),
`SemanticProviderUnavailable` (3). Applying adds `KeeperUnavailable` and
`KeeperCapabilityMissing` (3), `KeeperFailure` and `PartialApply` (1). No exit
number was added to the five.

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