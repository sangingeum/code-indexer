# Gate record — structural query on a stable identity

Verification of the round that landed at `3d93972` (round base `84fd194`):
the structural filters on `find-symbol`, the evidence label, the ambiguity
envelope, and the graph union over candidates. Ruled against this
repository's own live index, not fixtures.

**Verdict: PASS-with-findings.** No bar fails. Four findings, all low, and two
observations; none of them changes a delivered behaviour, two are
documentation truth and one is a pre-existing label defect on a surface this
round rewrote.

## What was measured, and against what

The index the gate ruled against: `schema=7`, `rows=current`,
`index_revision=59`, `parse=parsed:170,parse_unavailable:784`, opened read-only
for the snapshot (`index-snapshot.txt`). Populations, repository-only (this
gate's own five indexed files excluded): **2,312 symbol rows**, 130 files
holding symbols with no AST extractor, 2,067 `python` rows, 137 `class` rows,
377 private rows, 94 `markdown` rows.

Two environment facts, because they change how the numbers below should be
read:

- A standing `code-indexer watch --all` daemon was live when the gate opened.
  It indexed the round's new test files (and, later, this gate's own capture
  files) between the leg's record and the gate. It was paused with
  `code-indexer unwatch --all` for the measurement so the rows would not move
  under the probes, and restarted with `code-indexer watch --all --background`
  at the end. No `reindex-project` was run: the round's code performs no
  reindex (see B8).
- The live index therefore holds 5 symbol rows belonging to four files this
gate created (`runs/round3-gate/*`). Every population above is quoted with
  those excluded; the as-answered counts are one to two higher and the
evidence note's file count is 133 rather than 130 for the same reason.

The `Warning: index is stale (N files changed …)` line in most captures is
about the tree, not the rows: this gate writes capture files, so the stale
count grows from one capture to the next while the index stands still. It is
left in the captures unmasked because it is the tool's own honest answer.

## Bars

| bar | what it asks | command (capture) | exit | observed |
|---|---|---|---|---|
| B1 | suite green and warn-clean at the tip; ruff clean; hygiene zero | `uv run pytest tests/ -q -W error` (`pytest-full.txt`); `uvx ruff check src tests scripts` and hygiene (`b1-toolchain.txt`) | 0 / 0 | `704 passed, 2 deselected in 92.44s`; `All checks passed!`; `publishing hygiene: … violations=0` |
| B2 | each filter answers alone, composes, and has a correct empty case | `b2-*.txt` | 0 (2 where a refusal is the answer) | table below |
| B3 | `--name-pattern` is a glob, and a many-row pattern is not a table scan | `b3-query-plan.txt`, `b3-timing.txt` | 0 | planner: `SEARCH s USING INDEX idx_symbols_name (name>? AND name<?)` for a prefix glob; `SCAN s` only for a leading wildcard |
| B4 | evidence on every candidate; `unknown` never rendered as a negative | `b4-evidence.txt` | 0 | every row carries `ast`/`heuristic`/`unknown`; a docs/file row reports `unknown`; a zero from an attributed filter carries `evidence_note` |
| B5 | ambiguity envelope, and a single-symbol command refuses | `b5-ambiguity.txt` | 0 / 2 | `match_count` 2 with `ambiguity.candidates[]` carrying all seven named fields; `find-definition --exactly-one` refuses with `ArgumentError` exit 2 listing 50 candidates |
| B6 | the graph commands union over candidates and label each hit | `b6-graph.txt`, `b6-graph-roots.txt`, `graph-*.json` | 0 | `Core`: 9 roots for 9 candidates; 51 direct callers each labelled with all 9 candidate targets; a store-attributed callee edge carries one target |
| B7 | a filtered answer that hits `--limit` says so | `b7-truncated.txt` | 0 | `{match_count: 2069, shown: 5, truncated: true}` and the text line `... 5 of 2069 matches shown (--limit 5 reached; …)`; an uncapped answer says `false` |
| B8 | nothing else moved | `b8-read-contract.txt`, `b8-refusals.txt`, `index-snapshot.txt`; `git diff 84fd194..3d93972` | 0 | schema 7 unchanged and no schema statement touched; 27 commands before and after; `server.py` diff empty; manifest byte-identical across a read; refusal spot-checks hold |
| B9 | the docs describe what ships, including the declared scope draws | `b9-docs.txt`; `SKILL-CLI.md`, `docs/json-schema.md`, `CHANGELOG.md` | 0 | all three scope draws documented; JSON keys match the documented object; two documentation defects (F1, F3) |

## B2 — every filter, alone and composed

All commands are `code-indexer find-symbol …` against this checkout.

| filter | hit | empty | argument is honoured (the same filter, another value) |
|---|---|---|---|
| `--kind class` | 137 rows, `3 of 137 matches shown` | `--kind interface` → `{"match_count":0,"shown":0}` | `--visibility`/`--type`/`--symbol-type class` return the identical `[file,start_line,name]` list |
| `--name-pattern 'Core*'` | 1 row (`Core`, class) | `'*Manager'` → 0 (no name ends in `Manager`) | `'core*'` → 8 (lower-case names), so the argument is not ignored and the match is case-sensitive |
| `--container Manifest` | 49 rows, `4 of 49 …` | `NoSuchContainer` → 0 | `--container Core` → 58 |
| `--visibility private` | 377 rows | `--visibility private --container NoSuchContainer` → 0 | `--visibility public` → 1,940; `--visibility internal` refused `ArgumentError` exit 2 |
| `--language markdown` | 94 rows, rows labelled `evidence=unknown` | `--language cobol` → 0 | `--language python` → 2,069 (2,067 repository-only) |
| `--inherits IndexerError` | 7 rows, evidence `ast` | `--inherits Base` → 0 + `evidence_note` | `--inherits ArgumentError` → 1 (`AmbiguousSymbolError`) |
| `--has-call find_symbols` | 26 rows, evidence `ast` | `--has-call no_such_function` → 0 + `evidence_note` | `--has-call count_symbols` → 1 |

Composition (AND in one read, `b2-compose.txt`):

```
$ code-indexer find-symbol --kind function --container Manifest --has-call find_symbols
- <project-root>::src/code_indexer/manifest.py:775-788 definitions_of (function, … evidence=ast)

$ … --name-pattern 'test_*' --kind function --language python --json | jq …
{"match_count":596,"truncated":true}
$ … --name-pattern 'test_*' --kind function --language markdown --json | jq …
{"match_count":0}
$ … --container Manifest --visibility private --language python --kind function --limit 5 --json | jq …
{"match_count":4,"shown":4,"truncated":false}
```

## B3 — a glob, and where it is answered from

Case and glob semantics are proven by the answers, not by a comment:
`'Core*'` is 1, `'core*'` is 8 (a case-insensitive glob would return the same
9); `'Core|Store'` is 0 and `'.*Core.*'` is 0 (a regex would match).

The planner is the evidence for the scan question (`b3-query-plan.txt`, via
`Manifest.symbol_query_plan`, opened `writable=False`):

```
prefix GLOB (Core*)
    SEARCH s USING INDEX idx_symbols_name (name>? AND name<?)
    SEARCH f USING INDEX sqlite_autoindex_files_1 (path=?) LEFT-JOIN
suffix GLOB (*Manager)
    SCAN s
```

A prefix glob that matches 596 rows (`test_*`) still plans as the same range
seek. Timing was tried and cannot discriminate here: every call, filtered or
not, finishes in 0.15 s wall (`b3-timing.txt`), because CLI start-up dominates
on a 2.3 k-row index — that measurement is reported as inconclusive rather
than as support. The code path agrees with the planner: `_symbol_filters` emits
`s.name GLOB ?` and nothing wraps it in `%…%`.

## B5 — what the envelope carries, and where a single symbol is refused

`find-symbol find_symbol` answers `match_count: 2`, no `truncated`, and
`ambiguity.candidates[]` with, per candidate, `file`, `range`, `kind`,
`container`, `signature`, `language`, `decl_hash` (plus `qualified_name` and
`evidence`). Text mode prints both rows and then `2 definitions match … and
this name is ambiguous`.

The single-symbol surface is `find-definition --exactly-one`:

```
$ code-indexer find-definition main --exactly-one
ArgumentError: name 'main' resolves to 50 symbols and this command needs exactly one — narrow it …
[exit=2]
$ code-indexer find-definition main --exactly-one --json
{"schema": 1, "ErrorType": "ArgumentError", "error": …, "candidates": [ … 50 records … ]}
$ code-indexer find-definition resolve_one --exactly-one
- <project-root>::src/code_indexer/identity.py:94-106 resolve_one (function, confidence=exact)
```

## B6 — the union over nine candidates

`Core` has 9 definitions in this index (a class, seven test fixtures, and two
rows read out of captured skeleton dumps).

- `find-symbol Core` → `match_count: 9` and nine candidates.
- `find-callers Core --max-nodes 8 --depth 1 --json` → `match_count: 9`,
  `ambiguity.candidates` length 9, eight roots shown with the note
  `9 definition roots exist for this subject, node cap 8 — 1 root not shown`,
  `truncated: true`, `omitted: 484`.
- At `--max-nodes 60 --depth 1` all nine roots are present and the `Core` class
  root carries 51 direct hits; **every one of the 51 carries `targets` with all
  nine candidate labels** — no hit is attributed to the first row, and none is
  empty.
- `find-callees Core --max-nodes 60 --depth 1 --json`: 29 direct callee nodes
  carry **one** target each — the definition they were reached from, which is
  the store-attributable case — and one node (`Config`, reached from five
  roots) carries five. That asymmetry is the documented behaviour, not a
  defect: a callee reached from several candidates says so.

## B8 — what did not move

- `SCHEMA_VERSION = "7"` at both `84fd194` and `3d93972`; `git diff` across the
  round touches no `SCHEMA_VERSION`/`CREATE TABLE`/`ALTER TABLE` statement.
- 27 `@app.command` registrations before and after: no new command.
- `src/code_indexer/server.py` diff is empty: the MCP surface is untouched, so
  the new filters stay CLI-only (as the record declares).
- Read contract: `sha256sum` of the manifest before and after `find-symbol` and
  `find-callers` reads is identical (`b8-read-contract.txt`).
- Refusal spot-checks (`b8-refusals.txt`): an unknown project →
  `NotFoundError` JSON, exit 1; an invalid `--visibility` → `ArgumentError`,
  exit 2; a name with no definition → `no exact-match symbols named …`, exit 0.

## Findings

**F1 — the round's own record is committed with an editorial placeholder.**
*Severity: low (documentation).* The last line of
`runs/round3-structural-query/record.md` at the tip is `(to be filled before
the push)` — directly under a verification block that was in fact filled in.
Reproduce: `git show 3d93972:runs/round3-structural-query/record.md | tail -1`.
Fix: delete the line.

**F2 — the round record's counts cannot describe one index state.**
*Severity: low (reproducibility of evidence).* Against the live index the
behavioural answers reproduce, the populations do not: the record's
`--language python` `match_count` is 2,026 where the gate measures 2,067
repository-only, while its total is 2,311 where the gate measures 2,312. A
+41 language count and a +1 total cannot both come from a single snapshot; the
same drift shows in `--kind class` 133→137, `--visibility private` 372→377 and
`--language markdown` 93→94, while `--container Manifest` 49 and
`--inherits IndexerError` 7 still match exactly. The record discloses that the
index was stale when it was written and that the standing watcher was
indexing the round's new test files; it does not pin a revision, so its
numbers are not replayable. Reproduce:
`code-indexer find-symbol --language python --json | jq .match_count`.
Fix: quote `index_revision` beside the counts, or quote them from one snapshot
(what `index-snapshot.txt` does here).

**F3 — `SKILL-CLI.md` overstates the name index.** *Severity: low
(documentation).* The filter table says `--name-pattern GLOB` … "the name
index answers it, no new index". For a leading wildcard the planner answers
`SCAN s`, as the round's own record says two lines later. Reproduce:
`runs/round3-gate/b3-query-plan.txt` (the `*Manager` block). Fix: qualify the
claim the way the record does ("a prefix glob is an index range; a leading
wildcard cannot be one").

**F4 — a graph definition root is labelled `ast` even when its file has no
AST grammar.** *Severity: low; pre-existing at the round base, not introduced
by this round.* `find-callers`/`find-callees` build every root with
`confidence="ast"` unconditionally (`src/code_indexer/graph.py:288`, `:420`).
Two of `Core`'s nine roots are rows read out of `.out` capture files, whose
evidence is `unknown`; the text walk prints them `[definition/ast]` and the
JSON reports `"confidence": "ast"`. That is the round's own evidence
vocabulary rendered as a positive claim on a surface this round rewrote.
Reproduce:

```
$ code-indexer find-symbol Core --json | jq -c '.symbols[] | {file, evidence}'
{"file":"runs/round1-gate/read-write-intent-batch/skeleton.out","evidence":"unknown"}
{"file":"runs/round1-gate/service-dial-batch/skeleton.out","evidence":"unknown"}
$ code-indexer find-callers Core --max-nodes 9 --depth 1
runs/round1-gate/read-write-intent-batch/skeleton.out:1 core [definition/ast]
runs/round1-gate/service-dial-batch/skeleton.out:1 core [definition/ast]
```

Fix: derive the root label from the row's evidence (`unknown` for a file with
no grammar) instead of the constant. Not a bar failure: the bar's rule is that
`unknown` is never rendered as *no*, and this renders it as *yes*; it is
reported because a QA gate that stayed silent about an overstated label would
be doing the same thing the round fixed elsewhere.

## Observations (not findings)

- `find-symbol NAME` (exact-name mode) plans as `SCAN s USING INDEX
  idx_symbols_name`, not a seek, because the clause is `s.name = ? COLLATE
  NOCASE` while the index is binary-collated. Pre-existing (round 3 did not
  change exact-name semantics) and cheap at this size; recorded so the next
  round that touches the read path sees it.
- `heuristic` has no rows on this index: the whole-index split is `ast` 2,033 /
  `unknown` 284, because every textual row here lives in a file with no
  grammar. The label exists and is exercised by fixtures; the record says the
  same and invents no example, which is the honest shape.

## What I did not verify

- Rounds 4 and 5 of the plan (the plan format, validate/show/apply, the
  per-file failure report, and any provider work): not started in this round,
  not touched here.
- The full round-2 gate was not re-run. B8's read contract and refusal matrix
  were spot-checked (manifest hash, three refusals, schema/command/MCP diffs),
  not re-established end to end.
- The MCP tools were not exercised: `server.py` is byte-unchanged and the
  record declares the new filters CLI-only, so there is nothing new to drive
  through it.
- Coverage was not measured; this round adds query branches, and the project
  carries no per-project threshold that this change would move.

## Captures in this directory

`probes.sh` (the whole matrix, re-runnable), `query_plan.py`,
`index_snapshot.py`, `mask.py` (host paths replaced with `<project-root>` /
`<home>` before commit; the raw captures were never committed). Result files:
`pytest-full.txt`, `b1-toolchain.txt`, `b2-*.txt`, `b3-query-plan.txt`,
`b3-timing.txt`, `b4-evidence.txt`, `b5-ambiguity.txt`, `b6-graph.txt`,
`b6-graph-roots.txt`, `graph-callers*.json`, `graph-callees*.json`,
`b7-truncated.txt`, `b8-read-contract.txt`, `b8-refusals.txt`,
`b9-docs.txt`, `index-snapshot.txt`.
