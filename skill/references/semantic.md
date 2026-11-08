# semantic -- searching by meaning, and the order every ranked answer shares

Governed by: ADR-0002 (symbol identity, and the provenance of the second
ordering key the structural surfaces share); ADR-0003 section 2 (what `stale`
means) and section 5 (exit codes); ADR-0009 section 2 (this file's contents and
budget). Open this reference when a question is asked in words rather than as a
name, or when a ranked answer's order needs reading.

The structural commands keep their flags and examples in [query.md](query.md):
`overview`, `skeleton`, `outline`, `find-*`, `get-code-context`, `deps`,
`changed-symbols`. This file owns the three retrieval-quality surfaces and the
ordering policy every ranked answer follows, the structural ones included.

## Commands

<!-- generated:commands-semantic:begin -->
### `semantic-search`

- **Class**: read
- **Arguments**: `query` (required)
- **Flags**: `--project` <value>, `--limit` <value> (default `8`), `--file-filter` <value>, `--symbol-type` <value>, `--language` <value>, `--ranking` <value> (default `vector`), `--json`, `--per-file` <value>, `--max-chars` <value>, `--max-tokens` <value>, `--context-lines` <value>, `--format` <value>, `--rerank` <value>, `--no-rerank`, `--mode` <value> (default `hybrid`), `--skip-stale-check`, `--refresh`, `--fresh`, `--all-projects`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `eval`

- **Class**: read
- **Arguments**: none
- **Flags**: `--project` <value>, `--queries` <value>, `--k` <value> (default `1,3,5,10`), `--mode` <value> (default `dense`), `--rerank` <value>, `--out` <value>, `--skip-stale-check`, `--refresh`
- **JSON**: text only, no `--json`.

### `eval-compare`

- **Class**: read
- **Arguments**: `a` (required), `b` (required)
- **Flags**: `--skip-stale-check`, `--refresh`
- **JSON**: text only, no `--json`.
<!-- generated:commands-semantic:end -->

## Searching by meaning

```bash
# meaning-shaped question, hybrid mode, eight hits
code-indexer semantic-search "where does the token refresh happen" --project /path/to/repo
```

- `--mode` is `hybrid` (default), `dense` or `lexical`; `--ranking` is
  `vector` (default), `metadata` or `hybrid`; `--mode lexical` answers from
  the FTS table with no vector and no service. `--max-chars`/`--max-tokens`
  cap the returned context, `--per-file` spreads hits across files, and
  `--context-lines` widens each snippet.
- One embedding attempt per query, with a short timeout. A search resolves
  ONE project by default and fans out only with `--all-projects`, where a
  30 s wall stops the walk (`skipped` with `reason=budget`, `partial: true`).
- On a `vectors=off` project, `semantic-search` refuses with `ConfigError`,
  exit 4, before the query is embedded -- never a silent lexical answer.
- No hits is `no results` plus its cause: the answer carries `empty_reason` /
  `empty_reason_sentence` (text prints `no results: <cause>`, worded for this
  surface -- "nothing ... matched the query", not "the name is absent"), so
  an empty search does not read as "nothing here matches" when the index
  never parsed the files. A `--all-projects` fan-out is the exception: it has
  no single index to describe, so a no-hit fan-out prints the bare
  `no results` with `coverage.scope: none` and no `empty_reason`; read the
  per-project blocks for detail. Every answer also carries the `coverage`
  block (see [machine-output.md](machine-output.md)).
- `--format text|compact|json` is the shared read flag of this command too;
  `--json` is its exact alias; passing both is accepted when they agree and
  is `ArgumentError`, exit 2, when they name different formats (see
  [machine-output.md](machine-output.md)).
- The hit's own line names what it matched: the enclosing symbol, else the
  chunk's first non-blank code line, else the hit's own file path -- in
  parentheses in text output, in the symbol column in compact, as
  `hit.identity` in `--json`.

## The eval harness

```bash
# score one labeled query set against a project's index, report written to a file
code-indexer eval --project /path/to/repo --queries eval/queries.jsonl \
  --k 1,3,5,10 --out /tmp/eval-dense.json

# the same set with the heuristic reranker, as the candidate report
code-indexer eval --project /path/to/repo --queries eval/queries.jsonl \
  --rerank heuristic --out /tmp/eval-rerank.json

# compare two reports: the baseline first, the candidate second
code-indexer eval-compare /tmp/eval-dense.json /tmp/eval-rerank.json
```

- `eval` needs both required arguments: `--project` (path, slug or name) and
  `--queries` (a `queries.jsonl`, one `{id, query, relevant[]}` object per
  line). `--k` takes a comma-separated list (default `1,3,5,10`), `--mode`
  takes `dense` (default), `lexical` or `hybrid`, and `--rerank` takes
  `heuristic` or `none` (default off).
- Without `--out` the report is printed; with it the JSON report is written
  and stdout carries the progress lines. The report has `project`, `mode`,
  `queries`, `metrics` (`recall@k` per requested k, `mrr`, `ndcg@10`,
  `mean_latency_s`, `p95_latency_s`) and `results[]` (`id`, `rank` -- `null`
  for a miss -- `latency_s`, `output_chars`, `approx_tokens`).
- `eval-compare` reads two of those reports and prints a metric delta table
  (`a`, `b`, `delta`) plus the per-query `improved` / `regressed` / `unchanged`
  lists. Neither command has `--json`: the report is already machine-readable,
  and `--skip-stale-check` / `--refresh` are accepted but inert here because
  both only read files.

## Ordering: source, then samples, tests, docs, artifacts

Every RANKED answer -- `find-definition`, `find-symbol`, `find-references`,
`find-callers`, both `overview` hotspot lists and `semantic-search` -- is
ordered by one policy, in this order:

1. **File class** -- `source`, then `examples`, then `tests`, then `docs`, then
   `generated`/artifact. Build output, lock files, minified assets and
   dependency caches are artifacts; a `docs` directory, a Markdown,
   reStructuredText, AsciiDoc or plain-text file, or an extensionless document
   named `LICENSE`, `LICENCE`, `COPYING`, `NOTICE`, `AUTHORS`, `README`,
   `CHANGELOG` or `CONTRIBUTING` is a document; `tests`, `test`, `spec` and
   `__tests__` directories, a `Foo.Tests` project, `conftest.py`, `test_*`,
   `*_test.py`, `*.spec.ts` and `*Tests.cs` names are tests; `examples`,
   `example`, `sample`, `samples`, `demo` and `demos` directories are shipped
   samples; everything else -- unknown shapes and vendored trees included -- is
   source.
2. **The surface's own second key** -- identity quality for the structural
   surfaces (a hit that can name what it matched sorts above one that merely
   shares a name: a recorded kind, a named enclosing symbol, attributed `ast`
   syntax, then a deterministic tie-break over the row's identity fields --
   never insertion order); for `semantic-search`, the order its ranking
   produced.

**This is an ordering, not a filter.** Every hit is still returned, the demoted
ones after the promoted ones; each surface keeps its cap and default (`--limit`,
`--max-nodes`, the ten-row hotspot lists), and the policy decides the fill.

### Which keys fire on which surface

A key that cannot VARY across the rows of one answer is not a key.

| surface | keys that can fire, in order | what cannot fire, and why |
|---|---|---|
| `find-definition`, exact-name `find-symbol` | file class, recorded kind, attribution, then `name`, `file`, `start_line`, `qualified_name`, `decl_hash` | no "exact name before substring" key: the two tiers are two queries, and the substring tier runs only when the exact tier is empty, so one answer never holds both. The name is compared with SQLite `COLLATE NOCASE` (ASCII). |
| browse-mode `find-symbol` (no name) | the same list: file class, a recorded kind, attributed syntax, then `name`, `file`, `start_line`, `qualified_name`, `decl_hash` | no name-match key at all: there is no name to match, so `name` is a tie-break rather than a key. On a tree whose first 25 rows are all named and attributed, the difference from a class-then-name order is not observable through the browse cap |
| `find-references` | file class, then whether the reference sits in a NAMED enclosing symbol, then attribution, then `file`, `line`, `relationship`, `from_symbol`, `receiver` | no name key: `to_name = ?` already pins the target, so every row compares equal on it and no case policy exists here |
| `find-callers` | the same keys as `find-references`, expressed in Python (`ordering.edge_key`): class, named caller, attribution, then `file`, `line`, `symbol`, `relationship`, `receiver` | no count key: the walk's node is a call site, not an aggregate |
| `overview` hotspots (fan-in and size) | file class, then the count descending, then `file` | no identity key: a file is not a hit, so there is nothing to attribute |

### `semantic-search`: the ladder, then the mode's own order

Key 2 is the order the ranking produced: cosine descending (`--ranking
vector`, `--mode dense`); adjusted score, cosine plus symbol-presence deltas
(`--ranking metadata`); fused score, cosine + 0.25 x lexical (`--ranking
hybrid`); bm25 rank (`--mode lexical`); reciprocal-rank fusion of the dense
and lexical lists (`--mode hybrid`); RRF across the projects' lists
(`--all-projects`). Every one of those modes runs the class key after the scoring
pass and the pure-data share cap, before the generic caps (`--per-file`,
`--max-chars`/`--max-tokens`, `--limit`): it reorders and never removes, while
they remove. The exact-symbol pin of `--mode hybrid` orders within its rung, so
a test-defined exact match can still sit below source.

**Ties are broken by the hit's place in the index, never by arrival
order.** Score ties inside a mode fall back to `project`, `file`, `start_line`,
`end_line`, `symbol`, so the ordering is total: the same read against the same
index returns byte-identical output at any `--limit`, including the first call
after the embedding service reloads a model, because the vector a read
searches is the stored byte form -- the first read embeds the query once,
rounds it to float16, writes it to the embedding cache and searches the stored
bytes, so every later read of that text searches identical bytes. That row is
written even when the dimension probe fails (the dimension is taken from the
vector received), so a failed probe is not a hole in the guarantee.

The limit, measured rather than assumed: the service's vector for one text is
not a function of the text and the model. After a reload the first call for a
text differs from an immediately repeated call, and the size of that delta
depends on the text form the read path sends: on the measured query text the
instructed form (the one `semantic-search` embeds) moved by 6.237e-3 per
component and the bare text by 3.317e-3. Across the texts measured on this
service the figure runs from 1.575e-3 to 8.438e-3, so no single figure is a
constant of the tool; this page quotes the range 1.5e-3 to 8.5e-3, on the
texts measured. Whether a text's earlier form comes back after another
request is not dependable either: the bare text returned its first form 10
times out of 10, while the instructed form returned it 0 times out of 6 in
one measurement and 10 times out of 10 in a later one. The turn therefore
belongs to the request sequence, not to the load state: no warm-up settles
it, and a probe call immediately before the query does not change which form
the query receives. What that leaves,
stated as the limit: a text keeps the form of its FIRST embedding until that
cache row is evicted or the model, embed format or dimension changes;
`EMBED_CACHE=0` leaves nowhere to store the bytes, so two reads can search two
different vectors and at a deep `--limit` report a different member set
(measured: 2 in and 2 out of 400 hits); and two machines with different caches
can hold different forms of the same text. Two reads taken across a reindex
are two indexes, not one. The same-file merge is the line-ordered union of the
chunk ranges, so which ranges an answer reports is a function of the chunks the
store returned and not of their scores.

Two waivers, and only two. A **test-targeting query** (`test`, `tests`,
`testing`, `spec`, `specs`, `fixture`, `fixtures`, `unittest`, `unittests`)
sorts the tests rung with source -- the mode's order breaks ties among them,
and docs or generated artifacts stay demoted. A **data-format query** (`json`,
`yaml`, `yml`, `toml`) waives the pure-data down-weight and share cap; it
changes no rung, because a data format is a chunk shape rather than a path
class, so the class order breaks the ties among what remains. Every hit names
what it matched: the enclosing symbol, else the chunk's first non-blank code
line, else the hit's own file path -- in parentheses in text output, in the
symbol column in compact, as `hit.identity` in `--json`. No bare `-`.

### The two walks that keep their own order

`find-callees` does not use the policy: it returns call sites in store order
(by line), because "what does this symbol reach, and where" reads one body
rather than ranking the tree; `--max-nodes` counts that walk's nodes.

`deps` DOES order each level by class before the node cap fills, so a test,
sample or docs importer cannot take a budget slot ahead of a production one;
the levels stay levels: "what imports this, and how distantly" is a walk.

### One classifier, one case policy

The classifier lives in `src/code_indexer/ordering.py`; a capped SQL read
calls it as `file_class_weight` rather than restating the rules. Case is
compared in one place only -- the symbol read's `COLLATE NOCASE` (ASCII) --
and no other surface compares names, so a non-ASCII identifier compares
exactly everywhere.

### The ladder is the default; a project may extend it

`[tool.code-indexer.ordering]` in the project root's `pyproject.toml` adds
DIRECTORY names to a rung -- `docs`, `examples`, `tests`, each a list. The
additions are additive: a project can name a rung member but cannot declare
`tests/` or `docs/` away. Both layers honour the same table: the SQL function
is registered closed over it.

Why class outranks count: a fixture, sample or audit document accumulates
identifier mentions because it exercises or quotes the code beneath it, so a
fan-in list reports the fixture as the centre of the codebase; class is a
property of the path and does not move with hit distribution.

The fan-in number is an aggregate per DEFINITION. The index pass attributes
every reference to the definition it resolves to (`refs.target_symbol_id`):
the referencing file's own definition first, then the store's only definition
of the name, then the single definition the class ladder strictly prefers
among several -- so a test module that mirrors a source name keeps none of
that name's references. A name defined nowhere, or whose definitions tie, is
UNATTRIBUTED: that reference is credited to no file and counted in the
overview's `unattributed` line instead of being folded into a representative
owner. That total is split by reason -- `unresolved` for a name defined
nowhere, `ambiguous` for a name with several definitions and no same-file
match -- because the two are different facts about the corpus. An index built before per-definition attribution answers from the old
per-name aggregate; its row reads report `needs-reindex` until
`reindex-project` reruns the pass that resolves the references.

The workflow that keeps context small: `semantic-search` to locate,
`find-symbol`/`find-definition` to confirm identity, `get-code-context
--symbol` to read. Never read whole files.
