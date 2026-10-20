# Issue #21 — call-site identity in `find-callers`, and no silent graph cap

Scope: one leg, issue #21. Baseline HEAD: `cd2fd26`.

## What was wrong (confirmed before the change, not re-derived)

Two independent defects produced one symptom — an answer that looks complete
and is not.

1. **Node identity was the enclosing symbol, not the call site.**
   `find-callers` keyed its visited set on `(file, symbol)`, so two calls to the
   same target on two lines of ONE function collapsed into the first and the
   second was skipped by the cycle guard. Direct manifest read on the
   `vivarium-sim` index proved the row exists (`PawnSetRegistrationTests.cs`
   carries `DistanceBetween` call rows at 152, 210 and 218; the query reported
   152 and 210 only), so this was the query path, not extraction.
2. **Every graph cap was a silent `continue`.** `count >= max_nodes` in
   `find-callers`/`find-callees` and `len(seen) < max_nodes` in
   `deps_for_file` dropped an edge without a trace, and `graph_to_json`
   emitted only `{"roots": ...}` — no truncation field, no note line.

## Disposition

- **A — call-site identity in `find_callers`: done.** A caller node is
  `(file, line, from_symbol, relationship)` at every BFS level, so two sites in
  one enclosing symbol are two nodes with their own line and their own
  receiver, and a transitive site is not swallowed either. Definition roots
  keep `(file, symbol)`. Identity is centralised in `_site_key` /
  `_root_key` / `_callee_key` so the rule is one place, not three.
- **B — truncation is reported, never silent (all three commands): done.**
  `GraphResult` carries `truncated` and `omitted`; a refused edge is counted
  and also recorded as seen so a later level cannot recount it. JSON keeps
  `roots` byte-identical in shape and gains top-level `truncated` / `omitted`
  (plus `notes` when non-empty); `deps` keeps its per-level `in`/`out` dicts
  and gains the same two fields. Text gains one `note:` line naming the cap
  and the count, phrased as a lower bound ("a lower bound: the walk stopped
  examining at the cap") so it cannot be read as the whole population. A depth
  cap that stops a frontier with unvisited edges adds one line
  (`deeper references exist beyond depth N` / `deeper callees exist beyond
  depth N` / `deeper out import edges exist beyond depth N`) — verified from
  the store rather than assumed, so a natural end of the graph produces no
  line. Truncation lines print on stdout with the answer; the explanatory
  notes of a complete non-empty answer stay on stderr, unchanged.
- **C — `find_callees` keeps target-set semantics: done, deliberately.**
  Identity stays `(file, to_name)`. Written down in the `graph.py` module
  docstring, both command docstrings and `SKILL-CLI.md`. No deviation argued:
  the asymmetry is right — "who calls this" is a question about sites,
  "what does this reach" is a question about targets.
- **D — docs: done.** `CHANGELOG.md` gained one
  `## [Unreleased] — an incomplete graph answer says so (issue #21)` section;
  `SKILL-CLI.md` documents the new fields, the note lines and the
  sites-vs-targets asymmetry; the `graph.py` module docstring no longer claims
  "visited set keyed on (file, symbol)" and states the real per-command
  identity rule.
- **E — tests: done.** `tests/test_graph.py` 31 → 38 passing (7 new; the
  existing `test_graph_caps_respected` kept and extended). `_rig` was extended
  (one function calling one target twice; a caller of that callee so a depth
  cap has something real to stop) rather than duplicated. New pins: both sites
  reported (text and JSON); node cap → `truncated: true`, `omitted > 0`, note
  on stdout naming the cap; depth cap → its own line and `truncated: true`;
  generous cap → `truncated: false`, `omitted == 0`; unknown symbol → notes
  path kept, `truncated: false`; `find-callees` target-set identity plus its
  truncation; `deps` keeps per-level dicts plus its truncation.
  `tests/test_reference_surfaces.py` was updated for the new return type only
  (four `graph.find_callers` call sites now read `.nodes` / `.notes`).

## Pre-fix failure of the regression test (the proof)

`src/code_indexer/graph.py` and `src/code_indexer/cli.py` were restored to
`HEAD` (`git restore --source=HEAD --staged --worktree`), the new test was run,
and both files were then restored byte-exact (sha256 verified against copies
taken before the checkout). Raw output: `runs/issue21/pre-fix-regression.txt`.

```
>       assert sites == {(3, "twice"), (4, "twice")}, sites
E       AssertionError: {(3, 'twice')}
E       assert {(3, 'twice')} == {(3, 'twice'), (4, 'twice')}
E         Extra items in the right set:
E         (4, 'twice')
1 failed in 0.99s
```

Same test after the change: `1 passed` (`runs/issue21/post-fix-regression.txt`).

## Live verification (query-time only — no reindex, no index mutation)

Against the existing `vivarium-sim` index, source checkout:

```
# before (installed pre-fix build)
$ code-indexer find-callers DistanceBetween --project vivarium-sim
  ...PawnSetRegistrationTests.cs:152 ... receiver=inertCore
  ...PawnSetRegistrationTests.cs:210 ... receiver=core
  ...ProximityReadContractTests.cs:130 / 169 / 234 ...
  (218 absent, no note, exit 0)

# after
$ uv run code-indexer find-callers DistanceBetween --project vivarium-sim
  ...PawnSetRegistrationTests.cs:152 ... receiver=inertCore
  ...PawnSetRegistrationTests.cs:210 ... receiver=core
  ...PawnSetRegistrationTests.cs:218 ... receiver=core
  ...ProximityReadContractTests.cs:130 / 169 / 234 ...
```

```
$ uv run code-indexer find-callers DistanceBetween --project vivarium-sim --max-nodes 4
Vivarium.Sim/SimCore.cs:85 DistanceBetween [definition/ast]
  ...:152 ...  ...:210 ...  ...:218 ...
note: node cap 4 reached — 61 more reference edges not shown (a lower bound:
the walk stopped examining at the cap); raise --max-nodes to see more
```

`--json` for the same walk (`runs/issue21/live-max4.json`) has keys
`["notes", "omitted", "roots", "truncated"]` with `truncated: true`,
`omitted: 61`.

```
$ uv run code-indexer find-callees find_callers --project code-indexer --max-nodes 3
... note: node cap 3 reached — 31 more callee edges not shown (...)
$ uv run code-indexer deps src/code_indexer/cli.py --project code-indexer --direction out --max-nodes 2
out L1: src/code_indexer/__init__.py
note: node cap 2 reached (out direction) — 11 more import edges not shown (...)
$ uv run code-indexer deps src/code_indexer/cli.py --project code-indexer --direction out
... note: depth cap 2 reached — deeper out import edges exist beyond depth 2; raise --depth to see them
$ uv run code-indexer find-callees find_callers --project code-indexer --max-nodes 500
... note: depth cap 2 reached — deeper callees exist beyond depth 2; raise --depth to see them
```

Exit codes unchanged (0 on success) in every command above. The MCP server
needed no logic change — `_run_cli` relays the CLI's own stdout, so
`truncated` / `omitted` reach the graph tools unchanged.

## Verification

- `uv run pytest tests/test_graph.py -q -W error` → `38 passed`
  (baseline `31 passed`; 38 − 31 = 7 new tests, checked with `calc`).
- `uv run pytest tests/ -q -W error` → `479 passed, 2 deselected`
  (`runs/issue21/full-suite.txt`).
- No extraction, schema or embed change: `EXTRACTION_VERSION` untouched, no
  fingerprint flip, so no reindex is owed and an existing index answers with
  the fix immediately.

## Deliberately not done (recommendations, not started)

- **Store-level row key `(file, line, to_name, relationship)`** — two same-name
  member reads on ONE line still collapse to the first row. Out of scope: an
  extraction change with a reindex consequence, and a separate decision.
- **`Manifest.callers_of(limit=200)` and `Manifest.import_rows(limit=5000)`
  are still silent caps** on the store read itself. They are the same
  honesty defect one layer below the graph caps and were not in this leg's
  scope; if a manifest ever exceeds them the graph answer would be short with
  no trace. Worth its own issue.
- **What `omitted` counts** (what the walk examined, not what the store holds)
  is stated in the note itself rather than quantified; counting the whole
  population would cost a second full scan per refusal and was judged not
  worth it.
- No `reindex-project`, no `remove-project`, no live-index mutation, no
  extraction query touched. Issue #21 left open (verification gate and review
  come first).
