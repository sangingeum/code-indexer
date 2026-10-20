# Issue #21 — folds round: documents, strings and test assertions

Scope: the folds required by the two-axis review (`runs/issue21/review.md`) and
the truncation-fixture recommendation from the verification gate
(`runs/issue21/gate.md`, bar 3). Base `ebeb2c8`.

**No behaviour change.** Every edit below is doc prose, a code comment or
docstring, a user-facing string, or a test assertion.

## Per-fold disposition

1. **S-1 — the cap note described a walk that did not happen: done.** The
   parenthetical in `_cap_note` now states the real mechanism — a refused edge
   is counted but never expanded, so its own edges are not walked, and a depth
   cap can cut more — instead of claiming the walk stopped examining at the
   cap. The note's docstring was corrected in the same edit ("what this walk
   examined, not what the graph holds"). The sentence is generated once and
   quoted in three places; all three moved with it: `CHANGELOG.md`,
   `SKILL-CLI.md`, `runs/issue21/round.md` (both quotations in the round
   record).
2. **S-2 — the published JSON contract was stale: done.** `docs/json-schema.md`
   now documents the top-level `truncated` (`bool`), `omitted` (`int`) and
   `notes` (`[str]`, present only when non-empty) for
   `find-callers`/`find-callees` and for `deps`, with the `deps` refusal line
   noting its direction and `omitted` as the sum over both walks. The shapes
   were taken from live payloads, not from the code (see "The contract,
   observed" below).
3. **S-3 — `truncated` vs `omitted` undersold: done.** One sentence in both
   `docs/json-schema.md` and `SKILL-CLI.md`: `truncated` is the completeness
   flag, `omitted` counts node-cap refusals only and is `0` on a depth-capped
   answer, so `truncated: true` with `omitted: 0` is reachable.
4. **S-4 — the stdout/stderr split is pinned: done.** The four assertions now
   read `result.stdout` / `result.stderr` instead of the mixed
   `result.output`: the cap note must be on stdout with the tree and `note:`
   must be absent from stderr. Measured revert sensitivity rather than claimed:
   with `typer.echo(..., err=not result.truncated)` inverted to
   `err=result.truncated` in a throwaway worktree at this commit, the
   `find-callers` and `find-callees` pins fail (the note is found on stderr,
   not stdout). The `deps` pin keeps passing under that inversion because
   `deps` prints its notes to stdout unconditionally — it is a stream pin with
   no ternary behind it.
5. **Gate fold — two truncation pins were single-candidate: done.** The rig
   gained two fixtures: `pkg/shapes.py`, whose `compose` calls four distinct
   targets, and `pkg/aggregate.py`, which has three resolved out-edges (all
   outside the `refresh_token` walk, so the caller graph is unchanged).
   `find-callees compose --max-nodes 2` asserts `omitted == 3` (one callee fits
   beside the definition root, three are refused) and
   `deps pkg/aggregate.py --max-nodes 2` asserts `omitted == 2`. An
   implementation that counted only the first refusal would fail both.
6. **S-5 — node-count consequence: done.** One line in the changelog: every
   import statement is now its own site, so expect more nodes per answer and
   expect the default caps to report truncation more often.
7. **S-6 — `_emit_graph` docstring: done.** It now says the stream is chosen
   once for the whole note block from `truncated`, so an incomplete answer's
   explanatory notes follow the answer on stdout too.
8. **S-7 — evidence hygiene: done.** `runs/issue21/pre-fix-regression.txt`
   line 5 now reads `<tmp>/pytest-3/test_find_callers_reports_ever0`; the
   failure text and the assertion are untouched.
9. **S-9 — scope of the promise: done.** `SKILL-CLI.md` now heads the section
   "no graph-walk cap is silent", scopes the promise to the three graph walks,
   and points at issue #22 for the store reads one layer beneath them.
10. **P-1 — the decisions are on record: done.** `runs/issue21/round.md`
    gained "The issue's other suggestions — decisions of record": suggestion
    (1) rejected with its reason (a second lexical sweep per query, and the
    store is no longer the layer that goes missing), suggestion (3) refuted
    (the rows were in the store at 152, 210 and 218; the query path dropped
    one), the store-level silent caps filed as issue #22 rather than fixed
    here, and the clause that the issue's line 223 is 218 because the audited
    tree moved between the report and the round.

## The contract, observed

`docs/json-schema.md` was written from payloads the commands actually emit.
Three read-only runs (`--skip-stale-check`, project `code-indexer`, no index
pass, no index write), parsed by a script:

```
find-callers   find_callers             --max-nodes 2 --depth 1    rc=0
  keys=['notes', 'omitted', 'roots', 'truncated']
  truncated=True  omitted=68  notes=list(1)
find-callees   find_callers             --max-nodes 3             rc=0
  keys=['notes', 'omitted', 'roots', 'truncated']
  truncated=True  omitted=38  notes=list(1)
deps  src/code_indexer/cli.py --direction out --max-nodes 2        rc=0
  keys=['in', 'notes', 'omitted', 'out', 'truncated']
  truncated=True  omitted=11  notes=list(1)
```

`notes` is a list here because each run was capped; it is absent on a clean
answer (`test_find_callers_generous_cap_is_not_truncated` asserts the complete
direction).

## Nothing under `src/` changed but comments, docstrings and strings

```
$ git diff -U0 ebeb2c8..HEAD -- src/
src/code_indexer/cli.py   — _emit_graph docstring: +3 lines
src/code_indexer/graph.py — _cap_note docstring: 1 line changed
                            _cap_note return literal: 2 lines -> 3 lines
```

No executable line, no signature, no default, no control flow. Unchanged by
this leg: `EXTRACTION_VERSION` and every extraction, schema, embedding and
manifest module, so no reindex is owed.

## Tests

`tests/test_graph.py`: the four stream assertions above, the two truncation
pins rewritten around the new fixtures, and the rig extension that makes them
possible. No test was added or removed — the suite count is unchanged.

## Verification

```
$ uv run pytest tests/ -q -W error          # run once, output to file
479 passed, 2 deselected in 67.82s (0:01:07)
rc=0                                         (runs/issue21/folds-full-suite.txt)

$ uvx ruff check src tests
All checks passed!
rc=0
```

`uv run pytest tests/test_graph.py -q -W error` → `38 passed`.
