# Issue #21 — narrow re-verification of the fold commit (`ebeb2c8..7b7e2bb`)

Verifier: QA gate (test-engineering role). Date: 2026-10-20. Card: T-000005
(read-only read).
Scope: the fold leg only — `56b0954` (docs/graph fold) and `7b7e2bb` (revert
sensitivity record). The full gate on the repair (`runs/issue21/gate.md`,
base `ebeb2c8`) is of record and was **not** re-run. Nothing here re-authors or
fixes anything; a bar that failed would be reported with its repro.

Everything below is fresh output from this session, run against this checkout
(tip `7b7e2bb`), except the before/after comparisons, which used a saved copy of
the file plus `sha256sum` (recipe from the previous leg: copy, mutate, restore,
verify).

---

## Bar 1 — no executable-code change in `src/` — **PASS**

`git diff --numstat ebeb2c8..7b7e2bb -- src/` → `3 0 cli.py`, `5 3 graph.py`;
eleven changed lines in total, two hunks, classified one by one:

| file | lines | class |
|---|---|---|
| `src/code_indexer/cli.py` | +3 (296–298) | docstring of `_emit_graph` |
| `src/code_indexer/graph.py` | −1/+2 (92–93) | docstring of `_cap_note` |
| `src/code_indexer/graph.py` | −3/+3 (95–97) | the returned **string literal** of `_cap_note` |

No control-flow line, no signature, no default, no expression. `_cap_note`'s
signature is still `(cap: int, omitted: int, what: str, scope: str = "")`; the
f-string's shape (`f"node cap {cap} reached{scope} — {omitted} more {what}
{noun} "` + literal) is unchanged in structure. `code-indexer changed-symbols
--base ebeb2c8` agrees on which symbols moved: `_emit_graph:289-305`,
`_cap_note:89-98`, and the three test functions.

The one user-facing string now reads (live payload, `find-callees
find_callers --max-nodes 2`):

```
node cap 2 reached — 38 more callee edges not shown (a lower bound: a refused
edge is counted but never expanded, so its own edges are not walked, and a
depth cap on the same answer can cut more); raise --max-nodes to see more
```

## Bar 2 — the new note text is true of the code — **PASS**

Mechanism in `graph.py`, read at both fan-in and fan-out refusal branches and in
`deps`: on `count >= max_nodes` (or `len(seen) >= max_nodes` for `deps`) the
code does `seen.add(key)` → `omitted += 1` → `continue`. The refused edge is
keyed into `seen` (so no later level recounts it) and is never appended to
`node.children` or `next_frontier`, so no node exists to expand.

Measured, not inferred. Fixture `alpha() <- beta() <- gamma()` (store rows:
`callers_of('alpha') = [('pkg/chain.py', 4, 'beta')]`,
`callers_of('beta') = [('pkg/chain.py', 7, 'gamma')]`), `find-callers alpha
--depth 2`:

```
--max-nodes 4  truncated=False omitted=0  nodes=[('alpha',1),('beta',4),('gamma',7)]
--max-nodes 2  truncated=True  omitted=1  nodes=[('alpha',1),('beta',4)]      <- gamma ABSENT
--max-nodes 1  truncated=True  omitted=1  nodes=[('alpha',1)]                <- beta refused, gamma absent
```

So a refused edge's own edges really are not walked: at cap 2 the only refused
edge is `beta -> alpha`, and `gamma` (which calls `beta`) never enters the
walk; at a generous cap it does. The lower-bound claim is therefore true of
this code, and the note no longer justifies itself with a mechanism that does
not happen.

Nuance recorded, not a failure: in `find-callers` identity is the call *site*,
so if the same symbol is also reached through a second, accepted site, that
symbol's out-edges are walked through the sibling node. The refused edge itself
is still not expanded, and the count still cannot exceed the population, so the
sentence is not over-claiming; it is a per-edge statement.

## Bar 3 — the three quotations moved together — **PASS** (with two historical hits noted)

The sentence is present, identically worded, in all three required places:
`CHANGELOG.md:27`, `SKILL-CLI.md:339`, `runs/issue21/round.md:111`.

`rg "stopped examining"` (repo-wide) returns exactly three hits, none of them a
current quotation of the note:

1. `runs/issue21/review.md:36` — the review quoting the old code it criticises.
2. `runs/issue21/folds.md:15` — this fold's own record saying what was replaced
   ("instead of claiming the walk stopped examining at the cap").
3. `runs/issue21/live-max4.json` — a **pre-fold** capture (created in
   `4152325`, untouched by the fold) whose `notes` string is still the old text.

**Observation, not a fold failure:** `round.md:116` points at that capture as
"`--json` for the same walk", and the text block just above it now quotes the
new wording, so the document and the artifact it cites disagree on the note
string. The artifact is a record of what the program printed before the fold,
so it is not a stale quotation of current behaviour, but the pointer would be
clearer with one clause ("capture predates the wording fold"). Routing left to
the orchestrator; it does not re-open the fold.

## Bar 4 — the JSON contract matches the emitted payload — **PASS**

Capped and uncapped runs of all three commands, `--skip-stale-check`, parsed by
a scratch script outside the repo; keys and types compared line by line with
`docs/json-schema.md:59-100`:

```
find-callers capped    keys=['notes','omitted','roots','truncated']
                       truncated=True  omitted=79 (int)  notes=list   roots=list
                       node keys=['children','confidence','file','line','receiver','relationship','symbol']
find-callers uncapped  keys=['omitted','roots','truncated']        <- notes ABSENT
                       truncated=False omitted=0 roots=3
find-callees capped    keys=['notes','omitted','roots','truncated']  truncated=True omitted=38
find-callees uncapped  keys=['omitted','roots','truncated']        <- notes ABSENT
deps capped (out)      keys=['in','notes','omitted','out','truncated']
                       in={} (unrequested direction keeps its empty dict)  out={'1': [file...]}
                       omitted=11 int, notes=list
deps uncapped (both)   keys=['in','omitted','out','truncated']     <- notes ABSENT
                       in={'1','2'} out={'1','2'}  omitted=0
```

Every documented key is emitted, no emitted key is undocumented: `roots` and
its node shape (`file`, `symbol`|null, `line`|null, `relationship`,
`confidence`, `receiver`|null, `children`) are byte-for-byte the pre-fold shape
(`graph_to_json` is untouched in the range); `truncated` is `bool`, `omitted`
`int`, `notes` `[str]`; `deps` keeps per-level `in`/`out` dicts keyed from `1`.
`notes` is absent — not empty — whenever there is nothing to say (three of the
four uncapped cases above). The schema block shows `notes` in the shape and the
prose immediately after it states the conditional presence correctly.

## Bar 5 — `truncated: true` with `omitted: 0` documented and reachable — **PASS**

`docs/json-schema.md:105`: "It is `0` on a depth-capped answer, so `truncated:
true` with `omitted: 0` is a normal, reachable combination: decide completeness
from `truncated`, never from `omitted`." `SKILL-CLI.md:331-333` carries the same
rule ("`truncated` is the completeness flag; `omitted` counts node-cap refusals
only and is `0` on a depth-capped answer, so read `truncated`, never `omitted`").

Reachable in fact — three depth-capped answers observed this session:

```
find-callers run --depth 1 --max-nodes 500        truncated=True omitted=0  notes=['depth cap 1 reached — deeper references exist beyond depth 1; ...']
find-callees index_status --depth 1 --max-nodes 500 truncated=True omitted=0
deps src/code_indexer/core.py --depth 1 (both)     truncated=True omitted=0  notes=[out..., in...]
```

## Bar 6 — the stream pins detect the regression they exist for — **PASS**

The four assertion sites the review named (:485, :487, :548, :567 at the time;
now `tests/test_graph.py:515, 518, 582, 606` — two of them are the two
assertions inside the `find-callers` test) all assert `note:` in `stdout` and
`note:` not in `stderr`; none reads `result.output` any more.

Flip 1 — `typer.echo(f"note: {note}", err=not result.truncated)` → `err=result.truncated`:

```
2 failed, 1 passed
FAILED tests/test_graph.py::test_find_callers_reports_a_refused_edge_instead_of_dropping_it
FAILED tests/test_graph.py::test_find_callees_reports_targets_not_sites_and_its_truncation
   E  assert ('note:' in 'pkg/helpers.py:1 refresh_token [definition/ast]\n  app.py:3 run [call/ast]\n')
```

Restored with `git restore --source=HEAD --staged --worktree
src/code_indexer/cli.py`; `sha256sum` against the copy taken before the edit:
`64acc46793b566300d94bf70717b7c2bb06ccdb0ff4f5f75d12a68ffa99a87b0` both, and
`git status --short src/` empty. Re-run: `3 passed`.

The `deps` pin stays green under that flip — and that is correct, not a hole:
`deps` never routes through `_emit_graph`; it echoes its notes with a plain
`typer.echo(f"note: {note}")` (cli.py:348-350), so there is no ternary for the
inversion to touch. (The fold record said this up front in `runs/issue21/folds.md`
item 4.) To show that pin is not vacuous, the regression that *does* exist for
`deps` was injected instead — `err=True` on that echo:

```
E  assert ('note:' in 'out L1: pkg/__init__.py\n')
FAILED tests/test_graph.py::test_deps_reports_its_truncation_and_keeps_its_levels
1 failed, 37 deselected
```

Restored the same way, sha256 identical, `1 passed`.

Adjudication: all four pins detect a stream regression on their own code path
(three under the predicate flip, the fourth under its own inline echo
mutation). The dispatch's premise "one predicate, four failing pins" is
factually one predicate and three; the fourth pin is insensitive to it by
construction, not by weakness. Ruled PASS on the substance, with the premise
discrepancy on record.

## Bar 7 — the LOW follow-up is closed — **PASS**

Fixtures re-derived from the rig, then measured through the CLI:

```
compose expands to 4 distinct callee edges:
  [('pkg/shapes.py','square'), ('pkg/shapes.py','circle'),
   ('pkg/shapes.py','triangle'), ('pkg/shapes.py','hexagon')]
definition roots for 'compose' = [('pkg/shapes.py','compose')]
find-callees compose --max-nodes 2 -> roots=1 truncated=True omitted=3

aggregate.py out edges = ['pkg/__init__.py','pkg/leaves.py','pkg/shapes.py']
deps pkg/aggregate.py --max-nodes 2 -> out={'1': ['pkg/__init__.py']} truncated=True omitted=2
```

Derivation (`calc`): callees 4 edges − 1 accepted = 3; deps 3 edges − 1
accepted = 2. Observed equals expected in both, and both are `> 1` as required.

First-refusal-only implementation, **measured** rather than argued: each
refusal counter was changed to `+= 1 if ... == 0 else 0` (three sites: fan-in,
fan-out, deps walk) and the two pins run —

```
E  assert 1 == 3   (find-callees compose, cap 2)
E  assert 1 == 2   (deps pkg/aggregate.py, cap 2)
2 failed, 36 deselected
```

then restored, sha256 `0d6fe7ed543a70df3be280cfeef6b9d6365613dce13a86fba9706bcb21db9dec`
before and after, `git status --short src/` empty. Both pins would fail against
that implementation; they no longer pass on a single-candidate fixture.

## Bar 8 — the scoped promise is honest — **PASS**

`SKILL-CLI.md:325` reads "**An incomplete graph answer says so — no graph-walk
cap is silent.**"; `:334-336` scopes it explicitly: "The promise is scoped to
these three graph walks: the store reads one layer beneath them still cap
silently (issue #22)". `gh issue view 22 -R sangingeum/code-indexer` →

```
{"number":22,"state":"OPEN",
 "title":"The graph commands announce a node-cap refusal, but the manifest reads under them are still silent caps",
 "url":"https://github.com/sangingeum/code-indexer/issues/22"}
```

## Bar 9 — publication hygiene across the fold diff — **PASS**

Every added line in `ebeb2c8..7b7e2bb` was scanned for three classes: absolute
local (`/home/...`) and temp paths, host user names, and household role names.
No added line in the fold range matches any of them (two `rg` passes over the
added lines, no match; the same scan over this document's own text was applied
before committing it).

`runs/issue21/pre-fix-regression.txt:5` keeps its failure text and loses the
host path: `tmp_path = PosixPath('<tmp>/pytest-3/test_find_callers_reports_ever0')`;
the assertion (`assert sites == {(3, "twice"), (4, "twice")}`) and both diff
lines below it are untouched.

## Bar 10 — green, warn-clean, no reindex — **PASS**

```
$ uv run pytest tests/ -q -W error          # full output redirected to a file outside the repo
rc=0
479 passed, 2 deselected in 67.81s (0:01:07)          (matches the claim)

$ uvx ruff check src tests                            (the CI lint, .github/workflows/ci.yml:23)
All checks passed!  rc=0

$ uv run pytest tests/test_graph.py -q --collect-only
38 tests collected                                    (suite count unchanged)

$ rg -n "EXTRACTION_VERSION =" src/ -> EXTRACTION_VERSION = 4   (graph_extract.py untouched by the range)

$ uv run code-indexer index-status <audited project path> --skip-stale-check
project=<audited project> state=idle languages_without_ast=[...]   (no fingerprint=needs-reindex field)
```

Live-index disclosure (the one command run without `--skip-stale-check`):
`code-indexer skeleton src/code_indexer --project code-indexer`, early in this
session, went through `_resolve` → `maybe_refresh`, which performed the CLI's
automatic staleness refresh on project **code-indexer** — its manifest shows
`last_indexed=2026-10-20 23:04:25 files=197 chunks=1638`. That is a live-index
write on `code-indexer`, caused by me, not on the audited project: `vivarium-sim`
still reads `last_indexed=2026-10-20 22:10:07`, `state=idle`, no reindex owed.
All graph queries used here carried `--skip-stale-check`; the before/after
experiments ran against temp index roots (`Config(index_root=<tmp>)`), and a
control query after the flips confirmed `--skip-stale-check` leaves the
manifest's mtime untouched.

---

## Verdict

All ten bars pass. Nothing to re-open on the fold leg: `src/` changed in
comments, docstrings and one user-facing string only; the new note sentence is
true of the refusal branch and demonstrated on a fixture where the refused
edge's own edges are absent; the three quotations moved together and the
remaining old-string hits are historical records (one of them, the pre-fold
JSON capture cited by `round.md`, is worth a one-line annotation when #21
closes — not a fold error); the JSON contract matches the payload for all three
commands; the depth-cap/`omitted: 0` combination is documented and reachable;
the stream pins fail under both the predicate flip and the `deps` echo
mutation and the tree was restored byte-exact; both truncation pins are
multi-candidate and fail a first-refusal-only implementation; the scoped
promise points at an open issue #22; the fold diff is publication-clean; and
the tip is green and warn-clean with the suite count, `EXTRACTION_VERSION` and
the audited project's fingerprint unchanged.

Card: T-000005.

**PASS**
