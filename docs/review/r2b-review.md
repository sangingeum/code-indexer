# Review -- `semantic-search` ranked by source kind

Two-axis review of the round landed on the round card, base `5d56d24`, tip
`8e49f4e` (matches `origin/main` as published). Eleven files, 824+/132-, inside
the leg's allowlist. The QA gate's verdict stands as of record (all bars pass on
fresh output with the index frozen); its bars were not re-run here. This review
adds the standards and spec axes: every claim in the record is traced to the
line that makes it true, and the round is judged against the governing set
(`docs/adr/0001`-`0009`, `docs/overhaul-plan.md`, the survey synthesis).

## Verdict

**Approve with notes.** The policy is right and it is wired through every
ranking and retrieval mode; the measured bar is met and every figure in the
record was re-derived from the eval reports rather than believed. Two defects
are blocking if left, and both are small fixes that belong in the fold card for
this round: the text renderer still labels a class-ordered list with a
score-descending header (`core.py:1337`), and the new sort silently ignores a
project's own extended ladder (`core.py:1193`) while its docstring promises the
same classifier the structural surfaces use. Three of the round's prose claims
misdescribe its own pipeline, and one of them is the header the gate already
found -- that is not one typo but one class: **a surface that describes its own
order must describe the order it actually produced**, and every label of the
new order needs to be checked against the code, not against the other labels.

No code, ADR or gate record was edited. Nothing below is a re-run of a gate bar.

## Findings, ranked

| id | file:line | finding | exact change | disposition |
|---|---|---|---|---|
| F1 | `src/code_indexer/core.py:1337` | Text output prints `search results (score desc):` above a list that is class-ordered, so the header names an order the renderer no longer produced. Confirmed on a produced list: `0.4100 src/a.py` above `0.7200 tests/test_x.py`. | Replace with a header that names the real order, e.g. `search results (file class, then score desc):`. Also change `tests/test_result_shaping.py:237`, which asserts the old string -- that assertion is why the suite stayed green over a false label. | fold now (the gate's finding 1; visible half of F3-F4) |
| F2 | `src/code_indexer/core.py:1193` | `ranking.class_order` is called with `(hits, tokens)` only, so it sorts with `ordering.DEFAULT_CLASS_NAMES`. Every other surface loads the project's table (`core.py:257`, `graph.py:313`, `graph.py:603`, `overview.py:63`). A project that adds a name under `[tool.code-indexer.ordering]` gets that rung from `find-definition` and no rung from `semantic-search`. Probe: with `tests = ["spec_e2e"]`, `ordering.file_class("spec_e2e/a.py")` is `tests` when the names are passed and `source` when they are not, and the sort follows. | Pass the ladder: the single-project call passes `self.class_names(entry.slug)`; `--all-projects` needs the key per hit (`ordering.class_weight(hit["file"], names_for(hit["project"]))`), so `class_order` takes a `names` resolver rather than one value. Add a test that a project-extended name is demoted through `Core.search`. | fold now -- blocking if left |
| F3 | `skill/references/query.md:190-193` | "The class key fires in every one of those modes and before the shaping caps (`--limit`, `--per-file`, `--max-chars` / `--max-tokens`, the pure-data share cap), so the policy decides which hits fill a capped answer and never which hits are returned." False three ways: the share cap runs at `core.py:1176`/`1183` **before** `class_order` at `1193` (and must -- it consumes score order, `ranking.py:285`); `cap_per_file` drops same-file overflow outright (`ranking.py:379-380`); and the answer is sliced by `--limit` at `core.py:1202`. The round record repeats the placement at `runs/r2b-rank-by-source-kind.md:14-15`. | State the real order: class order runs after the score pass and after the pure-data share cap, before the generic caps (`--per-file`, `--max-chars`/`--max-tokens`, `--limit`); it reorders and never removes, and the caps remove as they always did. | fold now |
| F4 | `CHANGELOG.md:10` | "... every retrieval mode (`dense`, `hybrid`, `lexical`), before the output caps" -- the same false placement as F3, one line. | Change to "after the scoring pass, before the generic output caps". | fold now |
| F5 | `runs/r2b-rank-by-source-kind.md:4-5`, `CHANGELOG.md:11` | The motivation names a captured transcript and a fixture as things that "could win a question about the implementation" and now cannot. Class ordering is not what fixes either: `/runs/` and `/eval/` are excluded by `.codeindexignore` (the previous round), and `ordering.file_class("runs/round1-gate/x.err")` is `source` -- a captured `.err` in a project with no ignore file is still source and still wins. A top-level `fixtures/` tree is also `source` (`ordering.py:181-196`); only a fixture under `tests/`/`spec/` is demoted, and `ranking.py:41-44` says so deliberately. | Narrow both sentences: the transcript case is closed by the exclusion, not by the order; the fixture case is closed only where the fixture sits under a tests or spec tree. | fold now |
| F6 | `src/code_indexer/reranker.py:13` | The reranker docstring still advertises "test/vendor/generated path penalties" as part of the heuristic. No such penalty exists in the class (`reranker.py:29-33`, `82-91`): the only path term is `PATH_TOKEN_BOOST`, which rewards a query token in the path. The round's headline "one classifier, no second path rule" is contradicted inside its own package. | Delete the phrase; say `symbol-name and path-token overlap`. | fold now |
| F7 | `src/code_indexer/core.py:1260`, `1280-1294` and `skill/references/query.md` (Ordering) | Under `--mode hybrid` a query equal to an identifier is pinned first by `_fuse_lexical_dense` (score `2.0`), then re-sorted by class -- so an exact match defined in a test or docs file is no longer returned first, while the docstring still says it is pinned first. Not a regression (the policy is deliberate), but it is an unstated exception to a documented promise. | One clause in `query.md` (Ordering): the exact-symbol pin orders within its rung; the class key can still place a test-defined exact match below source. | fold now (docs only) |
| F8 | `src/code_indexer/core.py:59` | `_hit_identity` returns the literal `"file"` for a symbol-less chunk with a blank snippet even when `hit["file"]` is known, so every such hit carries the same `identity` in JSON -- a field added to let a consumer tell hits apart, returning a constant. Documented and asserted (`tests/test_search_class_order.py:214-216`), so this is design, not a slip. | `return (hit.get("file") or "file")`, keeping the test's no-file case. | file an issue (cosmetic today; text output already prints the path) |
| F9 | `skill/references/query.md` (deletions in this round) | Unrelated to ordering, the round removed the only worked examples of `outline --docstrings`, `get-code-context --symbol Core.search`, `eval`/`eval-compare` and `changed-symbols --base/--head --impact`, and reworded ~40 lines of the graph and browse sections. `query.md` is at 399 lines / 20,941 bytes against a 400-line budget, so the cut was forced -- but the reference is the only home those examples have under ADR-0009, and this round's own measurement used `eval`. | Record the trade in the round record (one line: which examples the budget cost), or let the owner raise the reference budget. Do not silently re-cut. | owner call |

## Axis 1 -- standings: does the record match the code?

Every claim below was read in the code or produced by the code.

| claim (source) | line that makes it true | verdict |
|---|---|---|
| `--ranking vector` / `--mode dense`: cosine descending (record key table, `query.md:185-188`) | `core.py:1174` sorts the single project's pool by score desc; `core.py:1182` fuses with RRF for `--all-projects` | true |
| `--ranking metadata`: adjusted score, cosine + symbol-presence delta | `ranking.py:124-137` (symbol boost only; the path penalty is gone) | true |
| `--ranking hybrid`: fused `cosine + 0.25 * lexical` | `ranking.py:138-178` (`LEXICAL_WEIGHT = 0.25`, `ranking.py:71`), sorted by fused score at `ranking.py:178` | true |
| `--mode lexical`: bm25 rank | `core.py:1237` maps FTS row order to `score = 1/(60+rank)`; the ordering is the FTS order | true |
| `--mode hybrid`: RRF of dense and lexical | `core.py:1263` (`rrf_fuse`, k=60) | true |
| `--all-projects`: RRF across projects' lists | `core.py:1182` (`rrf_fuse(per_project, limit * 3)`) | true |
| one classifier; `TEST_PATH_MARKERS` / `TEST_PATH_PENALTY` gone | neither exists in `ranking.py` (grep over `src/`: the only hits are prose at `ranking.py:42-43`); `metadata_adjustment` judges symbol presence only (`ranking.py:124-137`) | true in code, **false in prose**: `reranker.py:13` (F6) |
| waiver 1: token set, case, tests rung only, docs/generated stay demoted | set at `ranking.py:62-66`; both sides lowercased at `ranking.py:184-189`; `weight()` maps the tests position to the source position only (`ranking.py:221-224`); docs=3, generated=4 untouched (`ordering.py:73-75`) | true |
| waiver 2: a data-format query waives the down-weight and the share cap, changes no rung | `ranking.py:283-285` (`_targets_data` early return), `ranking.py:253-275` (down-weight); `class_order` never reads the data waiver | true |
| "demote, never drop" at every cap | class order only reorders (`ranking.py:227`); but `cap_per_file` drops (`ranking.py:379-380`), `--limit` slices (`core.py:1202`), and the share cap relocates rather than drops (`ranking.py:286-308`) | the record's own wording survives (it says the caps keep their semantics); `query.md:193` does not (F3) |
| `_hit_identity` never returns an empty string, and all three formats print it | probe: blank snippet with a file -> `'file'`; no file and no snippet -> `'file'`; symbol -> the symbol; a 200-char line -> 80 chars. On one class-ordered list: text `(x = 1)`, compact `x = 1`, JSON `identity = "x = 1"` | true |
| eval figures | all 21 deltas re-derived with `calc check` from the four report files the record names, not from its tables: hybrid like-for-like recall@1 0.5952->0.6905 (+0.0953), recall@3 +0.0238, recall@5 +0.0238, recall@10 0.0000, MRR +0.0561, nDCG@10 +0.0424; dense 0.4524->0.6190 (+0.1666), MRR +0.1067, nDCG@10 +0.0858; the drifted pairs' +0.0715 / -0.0238 / +0.0323 / +0.0186 and +0.1428 / +0.0476 / -0.0238 / -0.0238 / +0.0769 / +0.0517; the four latency deltas | all true |
| per-query counts | id-keyed join over `results` (a positional join misaligns the arrays and reports the wrong queries): 6/0/36, 11/0/31, 6/1/35, 11/2/29 improved/regressed/unchanged, matching the record's lists exactly | true |
| 42 labelled queries, `--k 1,3,5,10` | `eval/queries.jsonl` parses to 42 objects (`wc -l` reports 41 -- the last line has no newline; the record's count is the right one) | true |

## Axis 2 -- spec: against the records, not vibes

### 1. A second ladder, or the same one?

Same policy, substituted second key. The ladder is file class first everywhere;
the second key is whatever carries the hit's evidence of relevance. For the
structural surfaces that evidence is identity -- the hit matched a name, so a
hit that can name what it matched sorts above one that merely shares a name.
For `semantic-search` the evidence is the ranking, so the mode's own order is
the second key. `query.md` now says exactly this ("the surface's own second
key"); the record's key table says it per mode. What the record does not say,
and should, is that this is a *substitution* and why:

> One ladder, one second key per surface: file class orders every ranked answer;
> where a hit's evidence is its identity the structural surfaces order by
> identity quality, and where a hit's evidence is its rank `semantic-search`
> keeps the mode's own score order inside the rung. Same policy, not a second
> ladder.

Without that sentence a later reader sees two ladders that happen to agree on
the first key, and the next round will invent a third.

### 2. Class as a primary key on a relevance surface

It is the right reading, and it is the survey's, not the round's invention: the
synthesis asks to "rank by source kind (demote artifacts, test-only and fixture
hits)" and reports `semantic-search` ranking "a near-duplicate test above the
implementation it tests". A source chunk at 0.41 outranking a test chunk at
0.72 is the intended consequence of that request, and the gate's 42 queries show
no regressions (6 improved, 0 regressed, 36 unchanged on the like-for-like
hybrid pair).

Two boundaries the principle needs stated, both cheap:

- **The waiver is the whole safety valve.** A query that does not say *test* but
  is about test-only infrastructure (a conftest, a test double) loses to any
  source chunk in the window. That is acceptable only because the waiver exists
  and is documented; it should be named as the valve, not as a courtesy.
- **Class is a path property, so the order is blind to how the tree excludes
  its captures.** A `fixtures/` tree outside `tests/` and a captured `.err` are
  source; that is why F5 matters and why the transcript case was actually closed
  by the exclusion in the previous round.

### 3. ADR-0009 surface ownership

No amendment needed. ADR-0009 decides where a command's documentation lives and
what it costs (one reference per concern, published budgets, generated command
sections); it does not decide ranking, and editing the Ordering section of
`query.md` is inside the grant it gives that file. The finding is the opposite
of an overreach: **no record in the governing set grants ordering at all.**
Neither the ADRs nor `docs/overhaul-plan.md` (rounds 1-5, none about ranking)
mention an ordering policy, yet two rounds of code and every surface's
terminology now rest on it. That is an owner call and I am not writing the ADR:
either the plan gains a register for survey-driven rounds, or an ADR owns "one
class ladder, one surface-specific second key" the way ADR-0002 owns identity.

### 4. Provenance

The round's provenance is adequately recorded: the record names the base and the
candidate, the two like-for-like reports, why the first pair is not
like-for-like (a live watcher reindexed `ranking.py` mid-leg), and the gate's
verdict is on the board. The gate report itself stays untracked because its
header carries a board card id and an authored record may not. That is the right
call, not a gap -- but it leaves the strongest evidence for this round outside
the tree, which is a convention smell rather than a defect.

The rule I would write:

> A gate report cites its round by record path and commit, never by board card
> id; the card id stays in the gate's own ledger line, and a report copied into
> `runs/` is stripped of it rather than withheld from the tree.

## What I did not do

- Did not re-run the gate's nine bars, the suite, ruff or the hygiene lint; the
  gate is of record and a reviewer who reruns it only repeats it.
- Did not edit code, the ADRs, the gate's record or the round's record.
- Did not rule on the deep-limit reproducibility finding: it is carded and not
  this round's (it reproduces on both tips).
