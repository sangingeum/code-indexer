# Issue #21 — verification gate

Verifying `a6f283f` + `4152325` (base `cd2fd26`) against the ten gate bars.
Verdicts below are the QA gate's own runs; nothing here is replayed from the
fix leg's captured output.

- Base: `cd2fd26` — Tip verified: `4152325` (both signed, `%G?` = `G`).
- The branch advanced to `b9ad6b3` (a docs-only review record,
  `runs/issue21/review.md`) while this gate was running. No code changed:
  `git diff --name-only 4152325..HEAD -- src tests` is empty, and
  `git hash-object src/code_indexer/{graph,cli}.py` equals
  `git rev-parse 4152325:src/code_indexer/...`. Every verdict below therefore
  applies to the code at `4152325` and to `b9ad6b3` unchanged.
- Live index used: the already-registered project `vivarium-sim`; also
  `code-indexer` for small, inspectable graphs. All live reads used
  `--skip-stale-check`. No index pass was triggered and no index state was
  mutated (see bar 8 and bar 10).

## Method: how pre-fix and post-fix were compared

The working tree was never edited. Two throwaway copies of the checkout were
made outside the repository:

```
cp -a <repo> <scratch>/tipcopy          # source at 4152325
cp -a <scratch>/tipcopy <scratch>/basecopy
cd <scratch>/basecopy && git restore --source=cd2fd26 --staged --worktree \
    src/code_indexer/graph.py src/code_indexer/cli.py
```

so `basecopy` is *base sources + tip tests*, and `tipcopy` is the tip. Live
queries were run through a two-line runner that inserts `$CI_SRC` at
`sys.path[0]` before importing `code_indexer.cli`, so the only difference
between a pre-fix and a post-fix run of the same command is the source tree.
Byte-exactness of the real tree was proven, not assumed:

```
$ git status --porcelain -- src tests          # (empty)
$ git hash-object src/code_indexer/graph.py src/code_indexer/cli.py
f0476a1aa4e1020106bc8b90daaed728f5389f02
0d9722bb02d1e7f11c8962ad76eafb4de2a330b0
$ git rev-parse 4152325:src/code_indexer/graph.py 4152325:src/code_indexer/cli.py
f0476a1aa4e1020106bc8b90daaed728f5389f02
0d9722bb02d1e7f11c8962ad76eafb4de2a330b0
```

## Bar 1 — the defect is gone, and the fix is the reason: **PASS**

Ground truth first, read-only from the manifest (`refs` table, sqlite opened
`mode=ro`), `vivarium-sim`:

```
PROBE row Vivarium.Sim.Tests/PawnSetRegistrationTests.cs:152 from=The_root_registers_a_pawn_system_exactly_when_the_rule_set_declares_it to=DistanceBetween rel=call conf=ast receiver=inertCore
PROBE row Vivarium.Sim.Tests/PawnSetRegistrationTests.cs:210 from=The_registered_pawn_system_is_the_worlds_live_entity_source to=DistanceBetween rel=call conf=ast receiver=core
PROBE row Vivarium.Sim.Tests/PawnSetRegistrationTests.cs:218 from=The_registered_pawn_system_is_the_worlds_live_entity_source to=DistanceBetween rel=call conf=ast receiver=core
```

Rows 210 and 218 share one enclosing symbol — precisely the shape that the old
`(file, symbol)` identity collapsed. The reported command shape, post-fix,
from the checkout:

```
$ uv run code-indexer find-callers DistanceBetween --project vivarium-sim --skip-stale-check
  Vivarium.Sim.Tests/PawnSetRegistrationTests.cs:152 ... (receiver=inertCore)
  Vivarium.Sim.Tests/PawnSetRegistrationTests.cs:210 ... (receiver=core)
  Vivarium.Sim.Tests/PawnSetRegistrationTests.cs:218 ... (receiver=core)
note: node cap 40 reached — 136 more reference edges not shown (a lower bound: ...)
rc=0
```

The same command against the base sources, same index, same query:

```
  ...PawnSetRegistrationTests.cs:152 ... (receiver=inertCore)
  ...PawnSetRegistrationTests.cs:210 ... (receiver=core)
  ...ProximityReadContractTests.cs:130 / 169 / 234 ...
```

218 is absent and no note of any kind is printed — this is the reported defect,
reproduced. Causality is therefore the source swap alone, not the index, not
the query. The fix leg's round claim is re-derived and holds.

## Bar 2 — revert sensitivity of the new pins: **PASS**, with four pins named as shape pins

Base sources + tip tests (`tests/test_graph.py`, 38 tests):

```
8 failed, 30 passed in 3.48s
```

Every one of the seven new pins fails on pre-fix code. The two that had to
demonstrably fail:

```
test_find_callers_reports_every_call_site_in_one_enclosing_symbol
E   AssertionError: {(3, 'twice')}
E   assert {(3, 'twice')} == {(3, 'twice'), (4, 'twice')}
E     Extra items in the right set:
E     (4, 'twice')

test_find_callers_reports_a_refused_edge_instead_of_dropping_it
E   KeyError: 'truncated'
```

and the extended pre-existing caps test fails behaviourally too, which is the
text-level truncation witness (`'node cap 1'` absent from base output):

```
FAILED tests/test_graph.py::test_graph_caps_respected
E   AssertionError: assert 'node cap 1' in 'pkg/helpers.py:1 refresh_token [definition/ast]\n'
```

Likewise the depth-cap pin fails with an assertion, not a missing key:

```
E   AssertionError: assert 'deeper references exist beyond depth 1' in 'pkg/helpers.py:1 refresh_token [definition/ast]\n  app.py:3 run [call/ast]\n  repeat.py:3 twice [call/ast]\n  app.py:1 [import:import/ast]\n  repeat.py:1 [import:import/ast]\n'
```

Named honestly: four of the seven are **shape pins**, not behaviour pins — they
fail on base only because `truncated`/`omitted` do not exist yet
(`KeyError: 'truncated'`), so they would also pass against an implementation
that emitted the fields with constant values:

- `test_find_callers_generous_cap_is_not_truncated`
- `test_unknown_symbol_is_not_reported_as_truncated`
- `test_find_callees_reports_targets_not_sites_and_its_truncation`
- `test_deps_reports_its_truncation_and_keeps_its_levels`

They are worthwhile as anti-over-reporting guards; they are not evidence that
the refusal is counted. The three behavioural pins above are the evidence.

Tip, same file: `38 passed in 3.50s` (`38 − 31 = 7` new, verified with `calc`).

## Bar 3 — discriminating fixtures: **PASS** for `find-callers`, INFO on the two sibling pins

The `find-callers` truncation pin is genuinely discriminating. Measured from
the fixture itself, not assumed (`refresh_token` fan-in at a generous cap):

```
PROBE callers-generous nodes=8 truncated=False omitted=0
PROBE   site app.py:1   from=None  rel=import:import
PROBE   site app.py:3   from=run   rel=call
PROBE   site chain.py:1 from=None  rel=import:import
PROBE   site chain.py:3 from=outer rel=call
PROBE   site repeat.py:1 from=None rel=import:import
PROBE   site repeat.py:3 from=twice rel=call
PROBE   site repeat.py:4 from=twice rel=call
```

7 candidate edges against a cap of 2 with 1 definition root, so the expected
refusal count is `7 − (2 − 1) = 6`; observed:

```
PROBE callers-cap2 rc=0 roots=1 nodes=2 truncated=True omitted=6
PROBE callers-cap2 keys=['notes', 'omitted', 'roots', 'truncated']
```

The cap boundary is pinned in both directions: at the complete node count
(`--max-nodes 8`) the answer is `truncated=False omitted=0`; one below it
(`--max-nodes 7`) it is `truncated=True omitted=1`.

INFO — the sibling truncation pins are **single-candidate**, and a cap test
with one candidate cannot distinguish "counts every refusal" from "counts the
first":

- `test_find_callees_reports_targets_not_sites_and_its_truncation`: `run` has
  exactly one callee; cap 1 refuses exactly that one (`omitted=1`).
- `test_deps_reports_its_truncation_and_keeps_its_levels`: `app.py` has exactly
  one resolved out-edge; cap 1 refuses exactly that one (`omitted=1`).

Neither is wrong — both observed values are the predictable ones — but neither
exercises multi-edge counting. See the follow-up recommendation.

## Bar 4 — negative control: **PASS**

Unknown symbol keeps the legacy notes path and claims nothing:

```
$ uv run code-indexer find-callers _graph_note_that_does_not_exist --project code-indexer --skip-stale-check
no results
note: the index holds no definition for '_graph_note_that_does_not_exist' (...); seeding from reference rows instead of reporting none
note: no reference rows for '_graph_note_that_does_not_exist' either — nothing in this index mentions it
rc=0
```

A symbol that *has* a definition and *zero* references — a complete empty
answer — does not claim truncation:

```
$ uv run code-indexer find-callers test_add_modify_delete_rename --project code-indexer --skip-stale-check --json
{"roots": [{"file": "tests/test_changed_symbols.py", "symbol": "test_add_modify_delete_rename", "line": 53, "relationship": "definition", "confidence": "ast", "receiver": null, "children": []}], "truncated": false, "omitted": 0}
rc=0
```

and a leaf `find-callees` (targets only a type row) is likewise `truncated:
false`, `omitted: 0`, with no note.

## Bar 5 — additive JSON only: **PASS**

Same queries, pre-fix vs post-fix, top-level key sets:

| command | base | tip |
|---|---|---|
| `find-callers DistanceBetween` | `["roots"]` | `["notes","omitted","roots","truncated"]` |
| `find-callers <unknown symbol>` | `["notes","roots"]` | `["notes","omitted","roots","truncated"]` |
| `find-callees find_callers` | `["roots"]` | `["notes","omitted","roots","truncated"]` |
| `deps src/code_indexer/cli.py --direction out` | `["in","out"]` | `["in","notes","omitted","out","truncated"]` |

`notes` already existed pre-fix when non-empty (`["notes","roots"]` above), so
the only additions are `truncated` and `omitted` — siblings, no renames, no
removals. Node shape is byte-identical across revisions:

```
$ jq -c '.roots[0]|keys'     # base and tip
["children","confidence","file","line","receiver","relationship","symbol"]
```

`deps` keeps its per-level `in`/`out` dicts; a `jq -S` diff of the whole
payload shows additions only (`notes`/`omitted`/`truncated`), the level data
unchanged.

## Bar 6 — the honesty bar: **PASS**

An operator can tell a complete answer from an incomplete one in every state,
for all three commands. Exit codes are `0` in all of them.

`find-callers` (`_site_key` on `code-indexer`; fixture where noted):

| state | JSON | text | stream | rc |
|---|---|---|---|---|
| under the cap (`--max-nodes 500`) | no `truncated` key claim; complete | no note | — | 0 |
| at the cap, nothing refused (fixture `--max-nodes 8`) | `truncated=false`, `omitted=0` | no note | — | 0 |
| over the cap (live `--max-nodes 4`) | `truncated=true`, `omitted=61` | `note: node cap 4 reached — 61 more reference edges not shown (a lower bound: ...)` | **stdout** | 0 |
| over the cap (fixture `--max-nodes 7`) | `truncated=true`, `omitted=1` | note, `node cap 7` | stdout | 0 |
| depth-bounded (`--depth 1`) | `truncated=true`, `omitted=0` | `note: depth cap 1 reached — deeper references exist beyond depth 1; raise --depth to see them` | stdout | 0 |
| depth 0 | `truncated=true` | `note: depth cap 0 reached — ...` | stdout | 0 |
| naturally ended (`--depth 3`, or max-nodes 500) | `truncated=false` | no note | — | 0 |
| unknown symbol | `truncated=false`, `omitted=0` | legacy notes, no cap claim | stdout | 0 |

`find-callees`:

| state | observed |
|---|---|
| at the cap, complete (fixture `--max-nodes 2`) | `truncated=False omitted=0` |
| over the cap (`find_callers --max-nodes 3`) | `truncated=true`, `note: node cap 3 reached — 38 more callee edges not shown (...)`, rc 0 |
| depth-bounded (default `--depth 2`) | `note: depth cap 2 reached — deeper callees exist beyond depth 2; raise --depth to see them`, rc 0 |
| naturally ended (`_site_key --depth 1`) | two type rows, **no** depth note, `truncated=false` |

`deps`:

| state | observed |
|---|---|
| at the cap, complete (fixture `--max-nodes 2`) | `truncated=False omitted=0` |
| over the cap (`cli.py --direction out --max-nodes 2`) | `truncated=true`, `note: node cap 2 reached (out direction) — 11 more import edges not shown (...)`, rc 0 |
| depth-bounded (`--depth 1`) | `note: depth cap 1 reached — deeper out import edges exist beyond depth 1`, rc 0 |
| depth-bounded at the default depth | `note: depth cap 2 reached — deeper out import edges exist beyond depth 2`, rc 0 |
| naturally ended (`graph.py --direction both --max-nodes 3`) | `truncated=false omitted=0`, no note, rc 0 |

Two judgement calls worth recording. A depth-capped answer reports
`truncated=true` with `omitted=0` — correct, since `omitted` is defined as node
cap refusals only, and the accompanying note names the depth. And the depth
note is only emitted when the stopped frontier really has an unvisited edge
(verified against the fixture: `find-callees _site_key --depth 1` is silent
because it genuinely ended).

## Bar 7 — the identity change did not break the sibling commands: **PASS**

`find-callers` fan-in that is import-based still reports. Subject `Manifest`
(import-heavy fan-in), base vs tip, same index, `--max-nodes 500`:

```
base nodes=199 import_rows=45
tip  nodes=373 import_rows=115
base-only rows (would be a LOSS at tip): (none)
tip-only rows: 174
```

Nothing is lost and 70 more import rows report — the old identity collapsed
every import row in a file into one node as well, since those rows carry no
`from_symbol`.

`find-callees` keeps target-set meaning; the only output difference on the same
query (`find-callees find_callers`, `--max-nodes 500`) is one node plus the new
depth note:

```
59a60
>   src/code_indexer/cli.py:259 find_callers [call/ast]
74a76
> note: depth cap 2 reached — deeper callees exist beyond depth 2; raise --depth to see them
```

The extra node is explainable, and is a repair rather than a drift: definition
roots are keyed `("definition", file, symbol)` and callee nodes
`("callee", file, to_name)`, so a *call* to `find_callers` inside `cli.py` is no
longer swallowed by the `find_callers` *definition* in the same file. The
target set grows by exactly that one real edge; no target is lost.

## Bar 8 — no reindex owed: **PASS**

```
$ git show cd2fd26:src/code_indexer/graph_extract.py | rg '^EXTRACTION_VERSION'
95:EXTRACTION_VERSION = 4
$ git show 4152325:src/code_indexer/graph_extract.py | rg '^EXTRACTION_VERSION'
95:EXTRACTION_VERSION = 4
$ git diff --name-only cd2fd26..4152325 | rg 'graph_extract|manifest|embedder|indexer|fingerprint|store'
(no match)
$ uv run code-indexer index-status <vivarium-sim checkout> --skip-stale-check
project=<vivarium-sim checkout> state=idle languages_without_ast=[...]
rc=0
```

No `fingerprint=needs-reindex` anywhere in the status line. `reindex-project`
and `remove-project` were not run.

## Bar 9 — warn-clean and clean: **PASS**

```
$ uv run pytest tests/ -q -W error     # run once, output to file
479 passed, 2 deselected in 68.52s (0:01:08)
rc=0

$ uvx ruff check src tests             # the command the repo's CI runs
All checks passed!
rc=0
```

The dev leg's 479/2 figure is reproduced independently.

## Bar 10 — live-index discipline: **PASS**

No index pass was triggered by this gate. Every live command carried
`--skip-stale-check` except two read-only `deps` probes, and those two did not
move either manifest (`manifest.db` mtimes for `vivarium-sim` and
`code-indexer` were measured before and after those probes and are identical:
`22:10:07` and `22:48:59`; the later stamp predates the probes and matches a
docs-only commit that landed before them). On `find-callers` /
`find-callees` / `deps` the flag is accepted and ignored — the skill documents
that no staleness probe sits on those paths — which is why the manifest mtime,
not the flag, is the check that matters. Both projects report `state=idle`, and
no write of any kind to `<INDEX_ROOT>` was made.

## Final verdict

**PASS** — the reported defect is gone, the fix is the demonstrated cause, the
new pins are revert-sensitive, the JSON change is additive, and all three graph
commands now say when an answer is incomplete.

## Recommendations (not filed — for routing)

1. **Follow-up issue (low):** the `find-callees` and `deps` truncation pins are
   single-candidate fixtures; a fixture with two or more candidates than the cap
   would witness multi-edge refusal counting (bar 3, INFO above).
2. **Follow-up issue (medium, disclosed by the fix leg):**
   `Manifest.callers_of(limit=200)` and `Manifest.import_rows(limit=5000)`
   remain silent caps on the store read — the same honesty defect one layer
   below the graph caps. A graph answer derived from a truncated store read
   would still be short with no trace.
3. **Note for the reviewer:** the default `find-callers` answer for
   `DistanceBetween` is itself cap-truncated at 40 nodes (136 refused edges,
   now declared). That is the pre-existing cap semantics, not a new defect, but
   it means "the default answer for this symbol is incomplete" is expected
   behaviour and should not be read as a regression.

## Evidence files (outside the repository)

Produced by this gate and kept out of git: full-suite output, the base-suite
run, the two-pin run, the fixture probes (candidates and cap boundaries), the
bar-6 stream matrix, the bar-5/bar-7 key-set and diff dumps, the manifest
probes, and the pre/post live payloads.
