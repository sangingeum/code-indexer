# Round 3 folds — the eight folds from the gate and the two-axis review

- Base: `f3d6b1e` (the round-3 review record; `fc5b26a` its skeleton)
- Source: the gate's four findings and the review's four should-fix items, as
  ruled in `runs/round3-review/record.md` (`## Folds for the fix leg`)
- Deliverable: eight folds, FOLD-A to FOLD-H. Five commits, each self-consistent
  and build-green. No schema change, no extraction-version change, no reindex,
  no new command.

Behaviour changes are exactly two: FOLD-D's evidence label (a graph root now
carries the label its row's verdict supports instead of the constant `ast`) and
FOLD-G's one line of text output on an ambiguous subject. Everything else is
record and documentation.

## Commits

| commit | carries |
|---|---|
| `917b5b0` | FOLD-A, FOLD-B, FOLD-C (round record, capture, SKILL-CLI, CHANGELOG, manifest docstring) |
| `6f3f366` | FOLD-D (one column list, one label function, roots from the row) + its test |
| `044f0cc` | FOLD-E, FOLD-F, FOLD-H (disclosure, reserved value, documented refusal) |
| `e1c3efb` | FOLD-G (graph help text, one line of text output) + its tests |

## FOLD-A — the editorial placeholder

`runs/round3-structural-query/record.md` no longer ends with
`(to be filled before the push)`. The verification block above it was already
filled in; the line was an editorial leftover, not a claim.

Swept the round's records for other placeholders
(`rg 'to be filled|TBD|TODO|FIXME|placeholder'` over
`runs/round3-structural-query/`, `runs/round3-gate/` and `runs/round3-review/`):
the only remaining hits are the gate and review records *quoting* the removed
line while describing the finding, which is an audit trail, not a placeholder.

## FOLD-B — one snapshot, one revision

The round record quoted two index states three commits apart: the filter
counts were authored at `d27fbcb`, and the whole-index ledger first appeared at
`3d93972`, with two test modules landing between them.

Fixed by re-quoting every population from ONE frozen read and stating the leg:

- the standing watcher was stopped (`unwatch --all`, pid 345973),
- every command in the record was re-run at base commit `f3d6b1e` against
  `index_revision=68`,
- the watcher was restarted afterwards (pid 352213),
- the raw capture is `runs/round3-folds/measure.txt` (masked, committed with
  the record).

The record now opens with a **Measured at** paragraph naming the revision, the
commit, the stop/restart and the fact that the index was stale by one added
test file (its own warning line says so). Every number in the document —
filter counts, evidence groups, the absence note, the ledger — belongs to that
one snapshot. The learned sentence, from the review: a record's numbers should
be captured where the record is finished.

Nothing was "corrected" toward 2,029. The dispatcher's older card text carried
that figure; the record's own numbers were 2,026 and are now 2,070 at the new
snapshot, which is what the instrument answers, not a correction of a claim.

## FOLD-C — the name index, in three sites

The claim "the name index answers it, no new index" / "never a pass over the
table" is gone from all three places it lived:

- `SKILL-CLI.md` — the filter table now says: no index was added, a prefix glob
  is a range seek on the existing name index, a leading wildcard is a scan of
  `symbols`, cheap at this size.
- `CHANGELOG.md` (under `## [Unreleased]`, no version bump) — same statement in
  the release prose.
- `src/code_indexer/manifest.py` — the `_symbol_filters` docstring no longer
  claims "never a pass over the table"; it names `SCAN s` for a leading
  wildcard and says the plan is asserted in the tests.

The round record gained a short "the name index, honestly" paragraph with the
same claim, so the document and the capture agree.

## FOLD-D — one row, one label

**The defect.** `graph.py` built every definition root with
`confidence="ast"`, while the same payload's ambiguity candidates labelled the
same rows through `identity.evidence_of`. And `manifest.py` described the
symbols table twice: `_SYMBOL_COLS` (13 columns, no `parse_verdict`) served
the definitions/browse reads, `_SYMBOL_FILTER_SELECT` (the verdict-joining
one) served the filtered read — so a `parse_error` file's AST row could be
`unknown` on one surface and `ast` on another.

**The fix, one edit.** `_SYMBOL_SELECT` is now the only column list (aliased,
verdict-joining) and serves `find_symbols`, `count`-companion reads,
`symbol_query_plan`, `definitions_of`, `symbols_with_files`,
`symbols_for_file` and `all_symbols`. `identity.evidence_of` remains the only
label function any surface calls; the graph roots are built by
`_definition_roots`, which labels each root from its own row. A payload can no
longer answer `unknown` and `ast` about one row.

**Proved by a row, not a constant.** `tests/test_structural_filters.py`
indexes a python file with a syntax error — tree-sitter recovers, so the rows
are AST-shaped (`source='ast'`) while the file's verdict is `parse_error`.
The test asserts the two `twin` rows carry `parse_error` through three read
paths (`definitions_of`, `symbols_for_file`, `all_symbols`), and that
`find-symbol`, its `ambiguity` candidates and the `find-callers` roots all say
`unknown` for those rows.

### before / after, against one unchanged index

Both captures were taken against the same index (`index_revision=68`), so the
rows are identical and only the code differs. Each capture file names the
command it ran; the entries below are quoted verbatim from those files.

Before, with the base code. `find-symbol` labels the two rows that live in
files with no grammar `unknown`, while `find-callers` — reading a different
column list and a constant — calls the same rows `ast`:

```
# runs/round3-folds/measure.txt, from `find-symbol Core --json`
"rows": [... {"file": "runs/round1-gate/read-write-intent-batch/skeleton.out",
 "start_line": 1, "evidence": "unknown"} ...]

# runs/round3-folds/measure.txt, from `find-callers Core --json`
"roots": [... {"file": "runs/round1-gate/read-write-intent-batch/skeleton.out",
 "line": 1, "confidence": "ast"} ...]
"candidate_evidence": [... {"file": "runs/round1-gate/read-write-intent-batch/skeleton.out",
 "range": {"start_line": 1, "end_line": 13}, "evidence": "unknown"} ...]
```

One payload, two answers about one row: `confidence: ast` on the root and
`evidence: unknown` on the candidate for the same definition.

After, with this leg's code, against the same `index_revision=68`. The root
now carries the label the row's verdict supports, matching both the candidate
beside it and `find-symbol`:

```
# runs/round3-folds/fold-d-after.txt, from `find-callers Core --json`
"roots": [... {"file": "runs/round1-gate/read-write-intent-batch/skeleton.out",
 "line": 1, "confidence": "unknown"} ...]
"candidate_evidence": [... {"file": "runs/round1-gate/read-write-intent-batch/skeleton.out",
 "range": {"start_line": 1, "end_line": 13}, "evidence": "unknown"} ...]
```

`Core` has nine definitions on this index; seven are `ast` on both sides, and
the two `.out` roots are now `unknown` on both sides. The second `.out` file
(`service-dial-batch/skeleton.out`) behaves identically. The contradiction the
review measured is gone, in the same payload that reported it.

## FOLD-E — the name-level join, disclosed by design

Not code-fixable in this round and not attempted: `refs.from_symbol` carries no
container (0 of 12,636 non-null rows carry a dot), and the `(file, bare name)`
join is what the identity model's section 4 defines. So two same-named symbols
in one file are both credited with the union of both bodies' attributed
references (37 of 1,488 call-filter hits on the review's frozen read; 0 of 20
`base_class` hits).

Disclosure landed in three docs plus the source: the round record's filter
section, `SKILL-CLI.md` and `docs/json-schema.md` (both describing
`--inherits`/`--has-call`), and the `_symbol_filters` docstring. Each says what
a hit means today ("this file's `name` makes this call") and that the
structural fix — resolving a reference to one container — is the staged work
the identity model's section 3 schedules. **No new card**, per the ruling.

## FOLD-F — `heuristic` reserved, and the dead tuple removed

**Choice: reserve, not remove.** `heuristic` stays in the vocabulary as a value
for a future extractor that supplements an AST with textual rows. Removing it
would have touched the identity module, the two reference-confidence
vocabularies and their docs for no behavioural gain, and a reserved value that
is honestly described is cheaper than a re-introduction later.

Every description now matches that choice: `identity.py`'s module docstring and
`evidence_of` docstring, `SKILL-CLI.md`'s evidence paragraph,
`docs/json-schema.md`'s `find-symbol` section, the CHANGELOG bullet and the
round record's evidence section. All say: produced by nothing today — a parsed
file's rows are `ast`, the regex fallback's are `unknown` — so no row is in it.

`EVIDENCE_VALUES` (the dead tuple, review N1) is gone with this fold; it had no
reader.

## FOLD-G — the graph commands' help and one line of text

`find-callers` and `find-callees` help text now name `match_count`, the
`ambiguity` block and the per-hit `targets` list. `_emit_graph` prints one line
in text mode when the subject name resolved to several definitions
(`2 definitions match 'step' and this name is ambiguous: every one is a root
above, and each direct hit names the definition(s) it can belong to`) — the
sentence `find-symbol` text mode already printed. JSON is unchanged, so the
only behaviour change is that one line.

## FOLD-H — `--exactly-one`'s empty answer: documented, not aligned

**Choice: document.** With no definition at all, `--exactly-one` raises
`NotFoundError` (exit 1, `no indexed definition for '<name>'`), where the same
query without the flag answers `definitions: []` and exit 0. That asymmetry is
right for the flag's meaning — a caller that asked for exactly one is told it
cannot have one, rather than handed an empty set — and aligning it with the
ambiguous refusal would conflate "missing" with "ambiguous" (exit 2).
`docs/json-schema.md` now states the empty answer, the exit code, and that the
divergence is the documented choice.

## Measured

- `index_revision=68`, `last_indexed=2026-11-06T22:56:35Z`, captured with the
  watcher stopped (pid 345973, restarted at pid 352213).
- Symbol ledger by `(source, verdict)`: `('ast','parsed')` 2,033 and
  `('regex','parse_unavailable')` 289 — 2,322 rows; the `find-symbol` labels
  read `ast` 2,033 / `unknown` 289 / `heuristic` 0.
- Parse verdicts: `parsed:170, parse_error:0, parse_unavailable:794`.
- `over-attribution` numbers quoted in FOLD-E are the review's frozen read at
  `index_revision=62` and are labelled as such, not re-measured here.

## What this leg did not do

- No schema change, no `EXTRACTION_VERSION` change, no reindex, no new command.
- The index was stale by one added test file at capture time and was not
  refreshed; the record's own warning line says so.
- N2's four uncovered branches (the leading-wildcard plan assertion, the
  ambiguity re-read when the page was capped, the callee second-target
  accumulation, the invalid `--visibility` refusal) are untouched: none is a
  defect and the ruling did not fold them.
- N5 (an under-describing commit message in the round's history), N6
  (`symbol_query_plan` is test-only) and N7 (two spellings for one evidence
  condition across `find-references` and `find-symbol`) are recorded in the
  review and not acted on here.
- FOLD-E's structural fix is not attempted, by ruling. FOLD-F's `heuristic` is
  reserved, not removed, by choice. FOLD-H is documented, not aligned, by
  choice.

## Verification at the tip

```
$ uv run pytest tests/ -q -W error
707 passed, 2 deselected in 92.74s

$ uvx ruff check src tests scripts
All checks passed!

$ uv run python scripts/publishing_hygiene_lint.py
publishing hygiene: scanned=657 skipped=0 violations=0 (untracked files are out of scope)
```

The suite was 704 passed at the review tip; the three added tests are FOLD-D's
`parse_error` proof and FOLD-G's two text-mode assertions.
