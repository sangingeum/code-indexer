# Rank semantic search by source kind

`semantic-search` was the last relevance surface without the ordering policy
the structural answers already shared. A test file or a docs page could win a
question about the implementation, and a hit with no enclosing symbol
rendered as a bare `-` in the symbol column. Two cases that sentence used to
name are not this order's work: a captured transcript is excluded by
`.codeindexignore`, and a captured `.err` or a file in a top-level
`fixtures/` tree is still `source` to the classifier -- only a fixture under
a `tests/` or `spec/` tree is demoted.

## What changed

One policy, enforced by code, tests and two matching documents:

- `src/code_indexer/ordering.py` stays the ONLY classifier. `--ranking vector`,
  `metadata`, `hybrid` and `--mode dense`, `hybrid`, `lexical` all funnel
  through one `ranking.class_order` call placed after the mode's scoring and
  after the pure-data share cap, before the generic caps (`--per-file`,
  `--max-chars` / `--max-tokens`, `--limit`); it reorders and never removes.
- The module's own path marker is gone. It called `fixtures/`, `vendor/` and
  `testdata/` non-source while the shared classifier calls them source, so one
  hit could be "tests" to search and "source" to `find-definition`.
- `core.py` renders an identity for every hit: the symbol, else the chunk's
  first non-blank code line, else `file`.

## The one order

Key 1 is file class, for every ranking and retrieval mode: `source`, then
`examples`, then `tests`, then `docs`, then `generated`/artifact, from
`ordering.class_weight`.

Key 2 is the order the ranking mode produced:

| ranking / retrieval | second key |
|---|---|
| `--ranking vector` (default), `--mode dense` | cosine score descending |
| `--ranking metadata` | adjusted score: cosine + symbol-presence delta |
| `--ranking hybrid` | fused score: cosine + 0.25 x lexical score |
| `--mode lexical` | bm25 rank |
| `--mode hybrid` | reciprocal-rank fusion of the dense and lexical lists |
| `--all-projects` | reciprocal-rank fusion across the projects' lists |

Python's sort is stable, so hits sharing a rung keep the order the mode gave
them. The order decides position and only position: every hit the store
returned in the window is still returned, the demoted ones at the tail, and
the caps (`--limit`, `--per-file`, `--max-chars`/`--max-tokens`, the pure-data
share cap) keep their current defaults and semantics.

## The two waivers

1. **A test-targeting query** -- any query token in `test`, `tests`, `testing`,
   `spec`, `specs`, `fixture`, `fixtures`, `unittest`, `unittests` -- waives
   the tests rung's demotion: test hits rank with source and the mode's own
   order breaks ties among them. Docs and generated artifacts stay demoted.
2. **A data-format query** -- any query token in `json`, `yaml`, `yml`,
   `toml` -- waives the pure-data down-weight and the pure-data share cap. A
   data format is a chunk shape rather than a path class, so this waiver
   changes no rung: the class order breaks the ties among what remains.

Both are written in `src/code_indexer/ranking.py` (module docstring) and in
`skill/references/query.md` (Ordering), beside the per-mode key list.

## What every hit names

`identity` is the symbol when the chunk has one, else the first non-blank line
of the snippet, else `file`. Text output prints it in parentheses after the
range, compact output in the symbol column, and `--json` carries it as
`hit.identity`; no output path can produce `-` or an empty name.

## Measurement

The bar is `eval` + `eval-compare` over the 42 labelled queries in
`eval/queries.jsonl`.

```bash
uv run code-indexer eval --project code-indexer --queries eval/queries.jsonl \
  --k 1,3,5,10 --mode hybrid --out <home>/dispatches/eval/r2b-base-5d56d24.json
uv run code-indexer eval --project code-indexer --queries eval/queries.jsonl \
  --k 1,3,5,10 --mode dense --out <home>/dispatches/eval/r2b-base-5d56d24-dense.json
# ... implement ...
uv run code-indexer eval --project code-indexer --queries eval/queries.jsonl \
  --k 1,3,5,10 --mode hybrid --out <home>/dispatches/eval/r2b-candidate-8e68976.json
uv run code-indexer eval-compare <base> <candidate>
```

### Baseline at the base tip, against the index as it stood then

```
metric                    a          b         delta
----------------------------------------------------
recall@1                    0.6190    0.6905    +0.0715
recall@3                    0.7619    0.7619    +0.0000
recall@5                    0.8095    0.8095    +0.0000
recall@10                   0.8571    0.8333    -0.0238
mrr                         0.7040    0.7363    +0.0323
ndcg@10                     0.7412    0.7598    +0.0186
mean_latency_s              0.1177    0.0639    -0.0538
p95_latency_s               0.0683    0.0645    -0.0038

improved (6): q-concept-01, q-concept-05, q-file-06, q-how-09, q-how-10, q-how-12
regressed (1): q-concept-11
unchanged (35): q-concept-02, q-concept-03, q-concept-04, q-concept-06, q-concept-07, q-concept-08, q-concept-09, q-concept-10, q-concept-12, q-file-01, q-file-02, q-file-03, q-file-04, q-file-05, q-how-01, q-how-02, q-how-03, q-how-04, q-how-05, q-how-06, q-how-07, q-how-08, q-how-11, q-ident-01, q-ident-02, q-ident-03, q-ident-04, q-ident-05, q-ident-06, q-ident-07, q-ident-08, q-ident-09, q-ident-10, q-ident-11, q-ident-12
```

```
metric                    a          b         delta
----------------------------------------------------
recall@1                    0.4762    0.6190    +0.1428
recall@3                    0.6667    0.7143    +0.0476
recall@5                    0.8095    0.7857    -0.0238
recall@10                   0.8571    0.8333    -0.0238
mrr                         0.6088    0.6857    +0.0769
ndcg@10                     0.6695    0.7212    +0.0517
mean_latency_s              0.0587    0.0586    -0.0001
p95_latency_s               0.0582    0.0607    +0.0025

improved (11): q-concept-01, q-concept-05, q-file-06, q-how-09, q-how-10, q-how-12, q-ident-03, q-ident-06, q-ident-08, q-ident-10, q-ident-12
regressed (2): q-concept-11, q-ident-02
unchanged (29): q-concept-02, q-concept-03, q-concept-04, q-concept-06, q-concept-07, q-concept-08, q-concept-09, q-concept-10, q-concept-12, q-file-01, q-file-02, q-file-03, q-file-04, q-file-05, q-how-01, q-how-02, q-how-03, q-how-04, q-how-05, q-how-06, q-how-07, q-how-08, q-how-11, q-ident-01, q-ident-04, q-ident-05, q-ident-07, q-ident-09, q-ident-11
```

That pair is NOT like-for-like. The live watcher on this checkout reindexed
the project mid-leg, because the leg edits `ranking.py` -- a file the query set
itself asks about -- so the candidate run scored against different chunk text
than the baseline did. The `recall@10` regression is that drift, not the
ordering: q-concept-11 asks where the pure-data down-weight happens, its
answer is a chunk of `ranking.py`, and the base code run against the SAME
(current) index also misses it (verified by running the base tip's own search
path on the same query).

### Like-for-like: base code and candidate code on one index

Both runs used the index as it stood at the tip, the base side executing the
base tip's package against it:

```bash
python <scratch>/run_base_eval.py eval --project code-indexer \
  --queries eval/queries.jsonl --k 1,3,5,10 --mode hybrid \
  --out <home>/dispatches/eval/r2b-basecode-currentindex-hybrid.json
uv run code-indexer eval --project code-indexer --queries eval/queries.jsonl \
  --k 1,3,5,10 --mode hybrid \
  --out <home>/dispatches/eval/r2b-candidate-041b4df-currentindex-hybrid.json
uv run code-indexer eval-compare <base> <candidate>
```

```
metric                    a          b         delta
----------------------------------------------------
recall@1                    0.5952    0.6905    +0.0953
recall@3                    0.7381    0.7619    +0.0238
recall@5                    0.7857    0.8095    +0.0238
recall@10                   0.8333    0.8333    +0.0000
mrr                         0.6802    0.7363    +0.0561
ndcg@10                     0.7174    0.7598    +0.0424
mean_latency_s              0.0631    0.0611    -0.0020
p95_latency_s               0.0620    0.0575    -0.0045

improved (6): q-concept-01, q-concept-05, q-file-06, q-how-09, q-how-10, q-how-12
regressed (0): -
unchanged (36): q-concept-02, q-concept-03, q-concept-04, q-concept-06, q-concept-07, q-concept-08, q-concept-09, q-concept-10, q-concept-11, q-concept-12, q-file-01, q-file-02, q-file-03, q-file-04, q-file-05, q-how-01, q-how-02, q-how-03, q-how-04, q-how-05, q-how-06, q-how-07, q-how-08, q-how-11, q-ident-01, q-ident-02, q-ident-03, q-ident-04, q-ident-05, q-ident-06, q-ident-07, q-ident-08, q-ident-09, q-ident-10, q-ident-11, q-ident-12
```

```
metric                    a          b         delta
----------------------------------------------------
recall@1                    0.4524    0.6190    +0.1666
recall@3                    0.6429    0.7143    +0.0714
recall@5                    0.7619    0.7857    +0.0238
recall@10                   0.8095    0.8333    +0.0238
mrr                         0.5790    0.6857    +0.1067
ndcg@10                     0.6354    0.7212    +0.0858
mean_latency_s              0.0604    0.0588    -0.0016
p95_latency_s               0.0582    0.0633    +0.0051

improved (11): q-concept-01, q-concept-05, q-file-06, q-how-09, q-how-10, q-how-12, q-ident-03, q-ident-06, q-ident-08, q-ident-10, q-ident-12
regressed (0): -
unchanged (31): q-concept-02, q-concept-03, q-concept-04, q-concept-06, q-concept-07, q-concept-08, q-concept-09, q-concept-10, q-concept-11, q-concept-12, q-file-01, q-file-02, q-file-03, q-file-04, q-file-05, q-how-01, q-how-02, q-how-03, q-how-04, q-how-05, q-how-06, q-how-07, q-how-08, q-how-11, q-ident-01, q-ident-02, q-ident-04, q-ident-05, q-ident-07, q-ident-09, q-ident-11
```

The hybrid bar is met on the like-for-like pair: no metric regresses, every
recall figure rises or holds, and six queries changed rank -- the wiring runs.
The dense run is reported, not barred, and it moves the same way.

## Tests

- `tests/test_ranking.py` -- the class key on a hand-built pool (a source hit
  below a test hit on cosine comes out first, every input still present), the
  whole ladder, the mode order inside a rung, one test per waiver, and the
  metadata mode's path-blindness.
- `tests/test_search_class_order.py` -- the policy through `Core.search`: source
  above a higher-cosine test hit, every hit still returned, every ranking mode,
  every retrieval mode, the two waivers, the identity rendering in compact,
  text and JSON, and the same query twice giving the same order.

All the new tests fail at the base tip (the API they exercise does not exist
there); none is a coverage-only test.

## Verification at the tip

```
uv run pytest tests/ -q -W error        -> 985 passed, 1 failed, 2 deselected
uvx ruff check src tests scripts        -> All checks passed!
uv run python scripts/publishing_hygiene_lint.py -> violations=0
```

The single failure is
`tests/test_docs.py::test_installed_copy_digest_matches_the_tracked_copy`: the
installed mirror of the skill under the user's Hermes skills directory still
holds the previous `references/query.md` (every other file matches). That
test's own message names the remedy -- the release step installs after the
test passes -- and the mirror lives outside this checkout, so it was left
untouched. `query.md` is at 399 lines / 20941 bytes against its 400-line /
24 KiB budget.

A green local `-W error` run is not a claim that the suite is warning-free:
five fork warnings fire under the CI command, which runs without `-W error`.

## Folds from the review

The review of this round approved it with notes and handed down seven fold-now
items. All seven are folded, in three commits after the reviewed tip.

| id | commit | what changed |
|---|---|---|
| F1 | `0852762` | The text header reads `search results (file class, then score desc):`; the assertion that pinned the old string now pins the new one. |
| F2 | `db2ca7e` | `ranking.class_order` takes the ladder: one `ClassNames` for a single project, a per-hit resolver for `--all-projects`. Two tests: a declared name demotes a higher-cosine hit through `Core.search` (with a no-table control that leaves the cosine order alone), and a cross-project window resolves the key from each hit's own project. |
| F3 | `da1bcb1` | `query.md` and the "What changed" bullet above state the real placement: after the scoring pass and the pure-data share cap, before the generic caps; it reorders and never removes, the caps remove. |
| F4 | `da1bcb1` | The changelog says "after the scoring pass, before the generic output caps". |
| F5 | `da1bcb1` | The motivation is narrowed here and in the changelog; see below. |
| F6 | `da1bcb1` | The reranker docstring advertises symbol-name and path-token overlap only; the path penalties it named do not exist in the class. |
| F7 | `da1bcb1` | `query.md` (Ordering) and the `_fuse_lexical_dense` docstring state the exact-symbol pin as ordering within its rung. |

### The motivation, narrowed

A test file or a docs page winning a question about the implementation is what
the class key closes. Two cases it does not:

- a captured transcript: `/runs/` and `/eval/` are excluded by
  `.codeindexignore`, so the exclusion closed that case, not the order;
- a fixture: only a fixture under a `tests/` or `spec/` tree is demoted. A file
  in a top-level `fixtures/` tree and a captured `.err` classify as `source`,
  so the order leaves both where they were.

### Verification at the fold tip

```
uv run pytest tests/ -q -W error                 -> 987 passed, 1 failed, 2 deselected
uvx ruff check src tests scripts                 -> All checks passed!
uv run python scripts/publishing_hygiene_lint.py -> violations=0
```

The single failure is
`tests/test_docs.py::test_installed_copy_digest_matches_the_tracked_copy`: the
installed mirror of the skill still holds the previous `query.md` (every other
file matches). The release step installs after that test passes and the mirror
lives outside this checkout. `query.md` is at 399 lines / 20982 bytes against
its 400-line / 24 KiB budget.

F2 threads the project's own ladder, so it could change the class of some paths
and with them the eval. It does not move this project's figures: the checkout
declares no `[tool.code-indexer.ordering]` table, so the ladder resolves to the
built-in names (`load_class_names(root) == DEFAULT_CLASS_NAMES` is `True`) and
every path's class is what it was. The hybrid eval was therefore not re-run, and
no figure above is restated.
