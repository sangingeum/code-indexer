# Verification gate — symbol-mode follow-ups (issues #24, #25, #26)

**Verdict: PASS.** All six bars reproduced with fresh command output at tip
`4b5aa16` (base `8254894`, branch `main`). Nothing below rests on a developer
self-report: every number was read from a capture produced in this session.
No reindex is owed — the diff is query-time only.

Round commits under test:

| Commit | Subject |
|---|---|
| `9e621ef` | fix(symbol-mode): match the exact symbol name only on the changed-file path |
| `1029550` | fix(eval): score a labelled symbol against every row of it in the file |
| `7d61da0` | fix(symbol-mode): one decorated definition answers as one range |
| `4b5aa16` | fix(symbol-mode): merge definitions before applying context padding |

## Bar results

| Bar | Claim | Result | Observed |
|---|---|---|---|
| 1 | Suite warn-clean from the tip; linter clean | **PASS** | `512 passed, 2 deselected in 73.63s`, zero warning lines under `-W error`; ruff `All checks passed!` (exit 0) |
| 2 | #24 exact-name only on the changed-file path | **PASS** | no dotted-suffix test left in `Core.code_context`; a live chunk named `pkg.prepare` is refused for `--symbol prepare` (base answers it); extractor comment now states the leaf is what is stored |
| 3 | #25 eval scoring reads every row of the label | **PASS** | disjoint rows `(2,3)` and `(5,6)`: hit in the second row correct, hit in the gap wrong, row past the 25-row cap still resolves |
| 4 | #26 one definition, one span; padding cannot change the count | **PASS** | live `cli.py --symbol get_code_context` → exactly one segment `1435-1452`; six definitions → six spans; two definitions four lines apart at `--context-lines 2` → two spans |
| 5 | Surface discipline; no version/schema change; no reindex owed | **PASS** | diff touches exactly the six expected files; no `EXTRACTION_VERSION` / `CHUNKER_VERSION` / schema mention; answer shape and ordering unchanged; `index-status` reports `state=idle` with no `fingerprint=` line |
| 6 | No collateral damage | **PASS** | line mode, `semantic-search --format compact --limit 8`, and `eval` all answer (exit 0) |

## Bar 1 — suite and linter

```
$ uv run pytest tests/ -q -W error
512 passed, 2 deselected in 73.63s (0:01:13)
```

Full output: `runs/issue24-26/pytest-tip.txt`. Selected tests 512, all passed →
pass rate 512/512 = 100.00% (computed with `calc`). Warning lines in the
capture: 0 (the run would have failed on any warning: `-W error`). The 2
deselected tests are deselected by the project's own configuration, not by
this gate.

```
$ uvx ruff check src tests
All checks passed!
```

Capture: `runs/issue24-26/ruff.txt`. Exit 0.

## Bar 2 — issue #24: the exact-name rule on the changed-file path

The re-resolve branch of `Core.code_context` now reads:

```python
fresh_rows = [c for c in live_chunks if c.symbol == symbol]
```

The `endswith("." + symbol)` disjunct is gone, and the exact-name comparison
is the whole rule (`rg -n 'endswith\("?\."' src/code_indexer/core.py` returns
nothing; capture `runs/issue24-26/resolution-rule-evidence.txt`).

Behaviour, reproduced with a standalone probe that indexes a throwaway project,
edits a file so the changed-file path is taken, and substitutes a live chunk
whose symbol is `pkg.prepare` (`runs/issue24-26/probe-tip.txt`):

| Tree | Answer to `--symbol prepare` |
|---|---|
| tip `4b5aa16` | refused: `error: symbol 'prepare' not indexed in dotted.py` |
| base `8254894` | answered: `segments=1 spans=[(10, 12)] re_resolved=True` |

So the suffix branch was live behaviour on the changed-file path, not merely
dead code: on the base the command answered a symbol that is not the requested
name. Tip refuses it.

Test-level pin, stated plainly: `test_re_resolved_symbol_mode_matches_the_exact_name_only`
in `tests/test_symbol_resolution.py` monkeypatches `ts_chunker.chunk_text` to
return a single synthetic chunk with `symbol = "pkg.prepare"`, edits the file so
the changed-file path runs, and requires `ValueError` matching
`not indexed in dotted.py`. It pins the rule — a live chunk whose symbol only
ends in `"." + name` is not a match — and it is synthetic on purpose: the
extractor cannot produce such a row (see below), so a natural-language repro
does not exist. It is a behaviour pin (the base raises nothing, so the assert
fails there), not a shape pin.

Extractor comment, `src/code_indexer/ts_chunker.py`:

> `# qualified_name node. Only the LEAF is ever stored — this`
> `# returns the last dotted part, and no dotted symbol name`
> `# reaches the store — so 'find_symbol' and every other caller`
> `# match the leaf, and only the leaf.`

The other extractor helper (`_symbol_from_declarator`) documents the same
behaviour for `Foo::bar` → `bar` and makes no claim about dotted names being
stored. No comment in either file now says callers can match "the full name".

## Bar 3 — issue #25: scoring reads every row

`_symbol_ranges` returns every `(start_line, end_line)` row of the label in the
file (`find_symbols(..., limit=None)`), and `_hit_correct` accepts any overlap
over that list. `rg -n 'rows\[0\]' src/code_indexer/evalharness.py` returns
nothing; the scoring path contains no truncation (capture
`runs/issue24-26/resolution-rule-evidence.txt`).

Disjoint-rows fixture, second row resorted independently (probe,
`runs/issue24-26/probe-tip.txt`): rows of the label in one file are
`[(2, 3), (5, 6)]`, genuinely disjoint.

| Observation | tip `4b5aa16` | base `8254894` |
|---|---|---|
| hit inside the first row `2-3` scores correct | True | True |
| hit inside the second row `5-6` scores correct | **True** | **False** |
| hit in the gap between the rows (line 4) scores correct | False | False |
| labels past the 25-row cap | capped read returns 25 rows, the uncapped definition read returns 26; the late file's range `(5, 6)` resolves | same |

The base's False on the second row is the defect the issue describes: safe only
by decorator symmetry. The gap staying False is the control — scoring widened
to every row, not to "the whole file".

## Bar 4 — issue #26: one definition, one span; padding is presentation

Live, against the project's own index (`--skip-stale-check`, read-only):

```
$ uv run code-indexer get-code-context src/code_indexer/cli.py \
    --symbol get_code_context --json --skip-stale-check
file: src/code_indexer/cli.py   stale: false   re_resolved: false
segments: 1   spans: 1435-1452
```

Capture: `runs/issue24-26/live-get-code-context-cli.json` (stderr empty). The
answer is one segment, `1435-1452`, where the base produced `1435-1452,
1436-1452`.

Fixture probes (`runs/issue24-26/probe-tip.txt`, `probe-base.txt`,
`probe-prepad.txt`):

| Case | tip `4b5aa16` | base `8254894` | pre-padding `7d61da0` |
|---|---|---|---|
| one decorated definition (store holds rows `(5,14)` and `(6,14)`) | 1 span `(5, 14)` | 2 spans `(5,14), (6,14)` | 1 span `(5, 14)` |
| six disjoint same-name definitions, no padding | 6 spans `(1,2) (5,6) (9,10) (13,14) (17,18) (21,22)` | 6 spans, same | 6 spans, same |
| six definitions, `--context-lines 6` | 6 spans `(1,8) (1,12) (3,16) (7,20) (11,24) (15,28)` | 6 spans, same | **1 span `(1, 28)`** |
| two definitions four lines apart, `--context-lines 2` | 2 spans `(1, 5) (3, 8)` | 2 spans, same | **1 span `(1, 8)`** |

Two things this settles. First, the merge is what produces one span for one
decorated definition (base: two). Second, the padding-order fix is
load-bearing: on `7d61da0` — merge implemented, but after padding — the same
six definitions collapse to one span under `--context-lines 6` and the two
close definitions collapse to one under `--context-lines 2`, i.e. a display
flag changed the segment count. At the tip the count is invariant and the padded
windows still touch (`(1,8)` then `(1,12)`), so the pin is not vacuous.

## Bar 5 — surface discipline

```
$ git diff --name-only 8254894..4b5aa16
CHANGELOG.md
SKILL-CLI.md
src/code_indexer/core.py
src/code_indexer/evalharness.py
src/code_indexer/ts_chunker.py
tests/test_symbol_resolution.py
```

Exactly the six expected files (capture `runs/issue24-26/surface-diff.txt`).
`git diff 8254894..4b5aa16 | rg 'EXTRACTION_VERSION|CHUNKER_VERSION|schema'`
returns no match: no extraction, schema, chunking or version change, so no
reindex is owed.

Answer shape: the JSON keys `file`, `stale`, `re_resolved`, `segments` are the
same object as before — the `core.py` hunks are `@@ -55`, `@@ -1188`,
`@@ -1202`, `@@ -1231` (range computation and the new merge helper); the return
statement and the segment builder are outside every hunk. Observed live JSON
carries all four keys.

Ordering: segments are ascending by start line before and after; the tip now
sorts explicitly inside the merge where the base relied on the store's row
order. No fixture changed order (see the six-definition and disjoint-row spans
above).

Index state:

```
$ code-indexer index-status <checkout> --skip-stale-check
project=<checkout> state=idle languages_without_ast=[...]
```

No `fingerprint=` line — by the tool's own contract that means the index was
built by the configuration running now, i.e. `fingerprint=ok`, which is exactly
the state a query-time-only diff should leave behind. `list-projects` agrees:
`slug=code-indexer state=idle files=324 chunks=1940`, no `needs-reindex`
reason (captures `runs/issue24-26/index-status.txt`,
`runs/issue24-26/registry-state.txt`). No `reindex`/`reindex-project` was run
at any point in this gate.

## Bar 6 — no collateral damage

- Line mode: `uv run code-indexer get-code-context src/code_indexer/core.py
  --start-line 1203 --end-line 1212 --skip-stale-check` → exit 0, lines
  `1203-1212` returned, one expected `Warning: file changed since last index`
  on stderr (`stale: true` semantics, unchanged). Capture
  `runs/issue24-26/line-mode.txt`.
- `uv run code-indexer semantic-search "how are symbol ranges merged before
  context padding" --project code-indexer --format compact --limit 8
  --skip-stale-check` → exit 0, 8 compact hits, top hit
  `src/code_indexer/core.py:58-75  _merge_symbol_ranges`. Capture
  `runs/issue24-26/semantic-search.txt`.
- `uv run code-indexer eval --project code-indexer --queries eval/queries.jsonl
  --mode lexical --k 1,5 --out <report>` → exit 0, 42 queries scored
  (`recall@1=0.2143 recall@5=0.3571 mrr=0.2798`). The score is not a bar here
  — the point is that the scoring path this round edited still runs end to end.
  Capture `runs/issue24-26/eval.txt`; the report JSON is a scratch artifact and
  is not committed.

## Open question ruled on: overlapping padded spans

Observed at the tip with six definitions and `--context-lines 6`:
`(1,8) (1,12) (3,16) (7,20) (11,24) (15,28)` — adjacent spans overlap, so
concatenating the segment bodies repeats lines: 74 emitted lines over a 28-line
union, 46 repeated (62.16% duplication, `calc`).

**Ruling: acceptable presentation, not a defect. No follow-up issue is owed.**

Reasons:

1. The segment is the definition, and the padding is a display flag whose whole
   stated job is "show me the surroundings of each span". Overlap is the honest
   consequence of two definitions being close together; suppressing it would
   mean either dropping lines or silently redefining what a segment is.
2. The alternative — merging the padded windows — is exactly the defect
   `4b5aa16` removed: it makes the segment count depend on `--context-lines`
   (`(1,28)` for one flag value, six segments for another). Presentation must
   not change identity.
3. The count invariant holds, which is the property callers can rely on:
   six definitions answer as six segments at every `--context-lines` value
   tested (0, 2, 6).

Cost is token duplication for a consumer that concatenates bodies; that cost is
bounded by the padding value the caller chose. A single sentence in
`SKILL-CLI.md` saying that padded spans of adjacent definitions may overlap
(so callers do not assume disjoint windows) would be a doc nicety, not a code
change, and is left to the routing decision — it is not worth a behaviour
change or an issue on its own.

## Other observations (non-blocking)

- `SKILL-CLI.md`, the `get-code-context` bullet: the inserted clause reads
  "and then the ranges of that one name that overlap or touch merged into one
  span each" — a verb is missing ("are merged"). Grammar only; the contract it
  states is correct. Owning surface: `SKILL-CLI.md`.
- `evalharness._symbol_range` (the first-row single-span view) is no longer used
  by any production path — only by one test that pins the past-the-cap read.
  Harmless; a candidate for pruning the next time that file is touched, not a
  finding for this round.

## What a reader must know

- Every number in this record comes from a capture in `runs/issue24-26/`,
  produced in this gate session at tip `4b5aa16`.
- The three fixes are provably load-bearing, not just present: the base tree
  answers the dotted name (#24), scores the second row as a miss (#25), and
  returns two ranges for one decorated definition (#26); the intermediate tree
  that merged after padding collapses six definitions into one span at
  `--context-lines 6`.
- The captures that contain machine paths are published here as redacted
  copies with a declared `paths elided` header; the unmodified captures are
  kept in the local evidence store outside this repository. No ignore entry is
  used.
- Nothing was reindexed, and nothing in the diff changes stored rows, so the
  existing index remains valid: `index-status` shows `state=idle` with no
  `fingerprint=` line.
- The gate found no failing bar. The two items under "other observations" are
  low-severity and belong to the review/authoring side, not to this gate.
