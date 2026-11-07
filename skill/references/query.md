# query -- the read commands and what each answers

Governed by: ADR-0002 (symbol identity and structural search); ADR-0003
section 2 (what `stale` means) and section 5 (exit codes); ADR-0008 (product
naming); ADR-0009 section 2 (this file's contents and budget). Open this
reference when a query answered the wrong shape and its flags need reading.

Every command here is a **read**: it opens no write transaction, runs no
index pass, and contacts no service to reach a staleness verdict. The one
exception is an invocation carrying `--refresh`, which runs one incremental
pass first and therefore writes.

## Commands

<!-- generated:commands-query:begin -->
### `overview`

- **Class**: read
- **Arguments**: none
- **Flags**: `--project` <value>, `--path-prefix` <value>, `--max-lines` <value> (default `60`), `--json`, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `skeleton`

- **Class**: read
- **Arguments**: `path_prefix` (optional)
- **Flags**: `--project` / `--name` <value>, `--tree`, `--no-signatures`, `--limit` <value>, `--json`, `--skip-stale-check`, `--refresh`, `--fresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `map`

- **Class**: read
- **Arguments**: `path_prefix` (optional)
- **Flags**: `--project` / `--name` <value>, `--tree`, `--no-signatures`, `--limit` <value>, `--json`, `--skip-stale-check`, `--refresh`, `--fresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `outline`

- **Class**: read
- **Arguments**: `file` (required)
- **Flags**: `--project` / `--name` <value>, `--docstrings`, `--json`, `--skip-stale-check`, `--refresh`, `--fresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `file-outline`

- **Class**: read
- **Arguments**: `file` (required)
- **Flags**: `--project` / `--name` <value>, `--docstrings`, `--json`, `--skip-stale-check`, `--refresh`, `--fresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `find-symbol`

- **Class**: read
- **Arguments**: `name` (optional)
- **Flags**: `--project` / `--name` <value>, `--kind` / `--type` / `--symbol-type` <value>, `--file` <value>, `--name-pattern` <value>, `--container` <value>, `--visibility` <value>, `--language` <value>, `--inherits` <value>, `--has-call` <value>, `--substring`, `--limit` <value>, `--json`, `--skip-stale-check`, `--refresh`, `--fresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `find-definition`

- **Class**: read
- **Arguments**: `name` (required)
- **Flags**: `--project` / `--name` <value>, `--json`, `--exactly-one`, `--skip-stale-check`, `--refresh`, `--fresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `find-references`

- **Class**: read
- **Arguments**: `name` (required)
- **Flags**: `--project` / `--name` <value>, `--relationship` <value>, `--limit` <value> (default `25`), `--json`, `--skip-stale-check`, `--refresh`, `--fresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `find-callers`

- **Class**: read
- **Arguments**: `name` (required)
- **Flags**: `--project` <value>, `--depth` <value> (default `2`), `--max-nodes` <value> (default `40`), `--json`, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `find-callees`

- **Class**: read
- **Arguments**: `name` (required)
- **Flags**: `--project` <value>, `--depth` <value> (default `2`), `--max-nodes` <value> (default `40`), `--json`, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `get-code-context`

- **Class**: read
- **Arguments**: `file` (required)
- **Flags**: `--project` / `--name` <value>, `--start-line` <value>, `--end-line` <value>, `--symbol` <value>, `--context-lines` <value>, `--json`, `--skip-stale-check`, `--refresh`, `--fresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `deps`

- **Class**: read
- **Arguments**: `path` (required)
- **Flags**: `--project` <value>, `--direction` <value> (default `both`), `--depth` <value> (default `2`), `--max-nodes` <value> (default `60`), `--format` <value> (default `tree`), `--skip-stale-check`, `--refresh`
- **JSON**: text only, no `--json`.

### `changed-symbols`

- **Class**: read
- **Arguments**: none
- **Flags**: `--project` <value>, `--base` <value> (default `HEAD`), `--head` <value>, `--staged`, `--include-untracked`, `--impact`, `--depth` <value> (default `1`), `--max-nodes` <value> (default `40`), `--json`, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `semantic-search`

- **Class**: read
- **Arguments**: `query` (required)
- **Flags**: `--project` <value>, `--limit` <value> (default `8`), `--file-filter` <value>, `--symbol-type` <value>, `--language` <value>, `--ranking` <value> (default `vector`), `--json`, `--per-file` <value>, `--max-chars` <value>, `--max-tokens` <value>, `--context-lines` <value>, `--format` <value> (default `text`), `--rerank` <value>, `--no-rerank`, `--mode` <value> (default `hybrid`), `--skip-stale-check`, `--refresh`, `--fresh`, `--all-projects`
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
<!-- generated:commands-query:end -->

## Orientation, then lookup, then reading

```bash
# what is here: languages, directories, entry points, hotspots
code-indexer overview --project /path/to/repo

# the structural map of one subtree, signatures included
code-indexer skeleton --project /path/to/repo src/code_indexer

# a name's every definition
code-indexer find-definition Tracker
```

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

**This is an ordering, not a filter.** Every hit is still returned, the
demoted ones after the promoted ones. Each surface keeps its cap and default
(`--limit`, `--max-nodes`, the ten-row hotspot lists); the policy decides the
fill of a capped answer and never drops one from an uncapped answer.

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
(`--all-projects`). The class key fires in every one of those modes and
before the shaping caps (`--limit`, `--per-file`, `--max-chars` /
`--max-tokens`, the pure-data share cap), so the policy decides which hits
fill a capped answer and never which hits are returned.

Two waivers, and only two. A **test-targeting query** (`test`, `tests`,
`testing`, `spec`, `specs`, `fixture`, `fixtures`, `unittest`, `unittests`)
sorts the tests rung with source -- the mode's order breaks ties among them,
and docs or generated artifacts stay demoted. A **data-format query** (`json`,
`yaml`, `yml`, `toml`) waives the pure-data down-weight and share cap; it
changes no rung, because a data format is a chunk shape rather than a path
class, so the class order breaks the ties among what remains. Every hit names
what it matched: the enclosing symbol, else the chunk's first non-blank code
line, else `file` -- in parentheses in text output, in the symbol column in
compact, as `hit.identity` in `--json`. No bare `-`.

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
property of the path and does not move with hit distribution. Caveat: fan-in
counts references AS OWNED BY the first defining file, so a name defined in
several files carries an attribution artefact -- class decides which files
fill the ten slots, not what the numbers mean.

The workflow that keeps context small: `semantic-search` to locate,
`find-symbol`/`find-definition` to confirm identity, `get-code-context
--symbol` to read. Never read whole files.

`map` and `file-outline` are hidden spellings of `skeleton` and `outline`:
the same command under a second name, kept so older scripts keep working.

## find-symbol: the structural filter surface

Every filter is an AND clause in one read, so they compose without a second
command.

| filter | answer |
|---|---|
| `--kind` (`--type`, `--symbol-type` are aliases) | function / method / class / struct / enum / namespace |
| `--name-pattern GLOB` | a case-sensitive glob over the name (`*Manager`, `get_*`); a prefix glob is a range seek, a leading wildcard is a scan |
| `--file F` | one project-relative file |
| `--container X` | the parent qualified name (`Manifest` for its methods) |
| `--visibility public\|private` | the stored column; a NULL visibility reads as public |
| `--language python` | the denormalized file language |
| `--inherits Base` | an attributed `base_class` reference to `Base` |
| `--has-call name` | an attributed `call` reference to `name` |

```bash
code-indexer find-symbol --kind class --name-pattern '*Manager'
code-indexer find-symbol Resolver --file src/core.py --container Core
```

**The `--inherits`/`--has-call` join is name-level, and that is a known
limit.** `refs.from_symbol` carries no container, so those two filters match
a reference to the symbol's own name in its own file; two same-named symbols
in one file are both credited with the union of both bodies' references.
Read such a hit as "this file's `name` makes this call", not "this exact
symbol does".

**Evidence.** Every symbol row carries `evidence`: `ast` (attributed syntax)
or `unknown` (no AST extractor for the file, or its parse failed).
`heuristic` is reserved and produced by nothing today. `--inherits` and
`--has-call` answer from attributed syntax only, so a symbol in a docs or
data file reports `unknown` -- never "no". Most repositories are mostly
files with no grammar, so an empty answer from those two filters carries an
`evidence_note` (JSON) or one explanatory line (text). Do not read such a
zero as a negative.

**Browse-mode order.** With no name there is no name clause, so the answer is
class, `name`, `file`, `start_line`, `qualified_name`, `decl_hash`: one key
list serves both tiers. A `--limit` below the population keeps the
alphabetically first names OF THE PROMOTED CLASS, not of the tree.

**Limits and ambiguity.** Exact-name `find-symbol` and `find-definition`
return every definition; `--limit` bounds only when given. Browse mode (no
name) and the `--substring` fallback default to 25. A capped answer says so:
`match_count` is the uncapped population, `truncated` is true when fewer rows
were shown. An exact name with more than one definition reports
`match_count` and `ambiguity.candidates[]` (file, range, kind, container,
signature, language, `decl_hash`) instead of picking a row;
`find-definition --exactly-one` refuses such a name with `ArgumentError`,
exit 2, listing every candidate.

## Graphs and references

```bash
# references TO a symbol, each labelled with its confidence
code-indexer find-references Tracker --relationship calls

# who calls it, and what it calls
code-indexer find-callers Tracker --depth 2
code-indexer find-callees Tracker --depth 2

# imports around a file, resolved edges only
code-indexer deps src/code_indexer/cli.py --direction both --format edges
```

- `find-references` reads the single reference store; every row carries
  `confidence=` (`ast` or `heuristic`) and `receiver=`, `--limit` defaults
  to 25.
- `find-callers` walks fan-in over `refs`; every **call site** is a node,
  while `find-callees` walks fan-out where a **target** is a node, so one
  target reached twice is one callee.
- Both accept a dotted `Type.Member`, union over an ambiguous name, label
  each direct hit with `targets`, and report `truncated`/`omitted` when
  `--max-nodes` cut the walk.
- `deps` resolves imports to files; unresolved edges are not drawn, and each
  level is class-ordered before the node cap fills (see the two walks above).
- `find-callees` is the one graph arm outside the policy: call-site order,
  because "what does this symbol reach" reads one body (see the two walks).

Under these walks the store hands over everything it holds: the graph reads
take `limit=None` by default, and a cap belongs to the layer above, which
announces what it refused in `omitted`.

## Reading source

```bash
code-indexer get-code-context src/code_indexer/core.py --start-line 40 --end-line 80
code-indexer get-code-context src/code_indexer/core.py --symbol Core.search --context-lines 3
```

`get-code-context` checks the live file hash against the manifest. In
`--symbol` mode it re-resolves the symbol on the live file; otherwise it
answers from that file's own symbol rows -- every matching range, with
overlapping or touching ranges of one name merged into one span per
contiguous run, `--context-lines` padding applied to each merged span after.
Line-range mode on a changed file returns the requested lines plus one
`Warning: ...` line on stderr and `stale: true` in JSON, exit 0.

## Semantic search and retrieval quality

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
- `eval` writes its report to `--out` (else stdout); `eval-compare` reads two
  report files. Neither has `--json`: the report is already machine-readable.

## What changed

```bash
# symbols touched by the working-tree changes
code-indexer changed-symbols --include-untracked
```

`changed-symbols` parses NEW/OLD contents out of the git diff, so it needs no
index of the changed lines. `--impact` lists callers and candidate tests,
prints the first six as `(+N more)`, and names the bound its list hit; JSON
reports `truncated`/`omitted`/`bounds` per file. The impact read is bounded
by a shared node budget, so it is a named sample, not the whole store.

## Symbol identity, as the answers use it

A row is identified by `(file, qualified_name, symbol_type, decl_hash)` -- a
key with no line number in it, so a declaration shifted by a line keeps its
identity.

| field | meaning |
|---|---|
| `qualified_name` | the container chain joined to the name: `Core.search` |
| `container` | the enclosing declaration's qualified name, `null` at top level |
| `language` | copied from the file row |
| `decl_hash` | `sha256:` of the normalized declaration signature |
| `symbol_id` | an integer surrogate, regenerated by any reparse |

- `decl_hash` hashes the **signature**, not the body: editing a body keeps
  it, a reformat that changes the signature changes it.
- Two overloads differing only by signature are two rows, told apart by
  `decl_hash`; two byte-identical declarations in one file are ONE symbol.
- `symbol_id` is revision-scoped and must not be persisted: any reparse
  moves it. JSON reports `symbol_id` beside `index_revision` so the pairing
  is explicit. The durable locator is the five-field key.
