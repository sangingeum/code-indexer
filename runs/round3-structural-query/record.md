# Structural query on a stable identity

Round 3 of the overhaul plan, governed by ADR-0002 sections 4 and 5. Query
only: no schema change, no reindex, no new command. Every command below was
run against this repository's own index (`<project>`, schema 7,
`rows=current`).

**Measured at.** Every population in this document comes from ONE frozen
read: the standing watcher was stopped (`unwatch --all`, pid 345973), the
commands below were run at commit `f3d6b1e` against `index_revision=68`, and
the watcher was restarted afterwards. The index was stale by one added test
file at capture, which the answers report on their own warning line and which
changes no symbol row. The raw capture is `runs/round3-folds/measure.txt`.
The earlier revision of this record quoted filter counts measured at
`d27fbcb` beside a whole-index ledger first written at `3d93972` — two
snapshots three commits apart; every number here belongs to the one snapshot.

## Filters on `find-symbol`

`--kind` is the new spelling of `--type`/`--symbol-type`; both old spellings
still answer identically because there is one option with three names. The
rest are new and compose (AND) in a single SQL read:

| filter | answers from |
|---|---|
| `--kind` | `symbols.symbol_type` |
| `--name-pattern` | `symbols.name` GLOB (case-sensitive; `idx_symbols_name`) |
| `--file` | `symbols.file` (`idx_symbols_file_start`) |
| `--container` | the denormalized parent qualified name |
| `--visibility` | the existing column (NULL reads as public) |
| `--language` | the denormalized column (no join through `files`) |
| `--inherits NAME` | `refs.relationship='base_class'`, `from_symbol` = this symbol |
| `--has-call NAME` | `refs.relationship='call'`, `from_symbol` = this symbol |

### each filter, with its command

```
$ code-indexer find-symbol --name-pattern '*Manager' --json | jq '.match_count'
0
```

Suffix globs have no hit in this repository: no symbol name ends in
`Manager`. The pattern is measured against the planner instead
(`tests/test_structural_filters.py`): a prefix glob is
`SEARCH s USING INDEX idx_symbols_name (name>? AND name<?)`, a `--file`
filter is `SEARCH s USING INDEX idx_symbols_file_start (file=?)`, and a
suffix glob is a scan of `symbols` — a leading wildcard cannot be a range.
No index was added for any of this.

```
$ code-indexer find-symbol --kind class --limit 3
- <project>::src/code_indexer/watcher.py:140-157 AlreadyRunning (class, confidence=exact, evidence=ast)  class AlreadyRunning(Exception):
- <project>::src/code_indexer/errors.py:57-75 AmbiguousSymbolError (class, confidence=exact, evidence=ast)  class AmbiguousSymbolError(ArgumentError):
- <project>::src/code_indexer/errors.py:52-54 ArgumentError (class, confidence=exact, evidence=ast)  class ArgumentError(IndexerError):
... 3 of 137 matches shown (--limit 3 reached; raise it to see the rest)

$ code-indexer find-symbol --container Manifest --limit 4
- <project>::src/code_indexer/manifest.py:276-293 __init__ (function, confidence=exact, evidence=ast)  def __init__(self, db_path: str, *, writable: bool = True):
- <project>::src/code_indexer/manifest.py:363-366 _file_select (function, confidence=exact, evidence=ast)  def _file_select(self) -> str:
- <project>::src/code_indexer/manifest.py:611-628 _symbol_filters (function, confidence=exact, evidence=ast)  def _symbol_filters(self, name: str | None = None,
- <project>::src/code_indexer/manifest.py:543-560 _symbol_row_tuple (function, confidence=exact, evidence=ast)  def _symbol_row_tuple(file: str, s: SymbolRow,
... 4 of 49 matches shown (--limit 4 reached; raise it to see the rest)

$ code-indexer find-symbol --visibility private --limit 3
- <project>::tests/test_output_contract.py:145-147 _Capture (class, confidence=exact, evidence=ast)  class _Capture(logging.Handler):
- <project>::tests/test_indexer_embed_context.py:12-24 _FakeEmbedder (class, confidence=exact, evidence=ast)  class _FakeEmbedder:
- <project>::tests/test_embed_format.py:18-30 _FakeEmbedder (class, confidence=exact, evidence=ast)  class _FakeEmbedder:
... 3 of 377 matches shown (--limit 3 reached; raise it to see the rest)

$ code-indexer find-symbol --language markdown --limit 3
- <project>::docs/overhaul-baseline-audit.md:211-236 KEY (function, confidence=heuristic, evidence=unknown)  line INTEGER NOT NULL,
- <project>::docs/subcommand-design.md:211-230 NULL (function, confidence=heuristic, evidence=unknown)  check in the walk).
- <project>::runs/issue27-publishing-hygiene/fold-verification.md:71-93 _is_authored (function, confidence=heuristic, evidence=unknown)
... 3 of 97 matches shown (--limit 3 reached; raise it to see the rest)

$ code-indexer find-symbol --inherits IndexerError --json | jq '{match_count, truncated, names: [.symbols[].name]}'
{
  "match_count": 7,
  "truncated": false,
  "names": [
    "ArgumentError",
    "BackendError",
    "ConfigError",
    "InternalError",
    "LockedError",
    "NotFoundError",
    "RebuildRequiredError"
  ]
}

$ code-indexer find-symbol --has-call find_symbols --limit 5
- <project>::src/code_indexer/core.py:1210-1227 _fuse_lexical_dense (function, confidence=exact, evidence=ast)  def _fuse_lexical_dense(self, entry: ProjectEntry, query: str,
- <project>::tests/test_identity.py:132-138 _rows (function, confidence=exact, evidence=ast)  def _rows(core: Core, entry, name: str | None = None,
- <project>::src/code_indexer/evalharness.py:100-117 _symbol_ranges (function, confidence=exact, evidence=ast)  def _symbol_ranges(manifest: Any, rel_file: str,
- <project>::src/code_indexer/core.py:1636-1653 code_context (function, confidence=exact, evidence=ast)  def code_context(self, entry: ProjectEntry, file: str,
- <project>::src/code_indexer/manifest.py:775-788 definitions_of (function, confidence=exact, evidence=ast)  def definitions_of(self, name: str,
... 5 of 26 matches shown (--limit 5 reached; raise it to see the rest)
```

`--inherits` on this repository has two answers worth keeping apart:
`IndexerError` is a base class that seven symbols name, and `Base` matches
nothing here at all — the fixture's `Base` lives in the tests, not in
`src/`. A zero from either filter is labelled (see the evidence section),
never printed as "no".

### composition

```
$ code-indexer find-symbol --kind function --container Manifest --has-call find_symbols
- <project>::src/code_indexer/manifest.py:775-788 definitions_of (function, confidence=exact, evidence=ast)  def definitions_of(self, name: str,
```

### truncation

`--limit` keeps both its meaning and its default (25 in browse mode, none in
exact-name mode), and a capped answer says it was capped:

```
$ code-indexer find-symbol --language python --limit 5 --json | jq '{match_count, shown: (.symbols|length), truncated}'
{
  "match_count": 2070,
  "shown": 5,
  "truncated": true
}
```

The same answer in text mode ends with
`... 5 of 2070 matches shown (--limit 5 reached; raise it to see the rest)`.
`match_count` is computed from the SAME clause set as the rows, so a capped
answer can never quietly disagree with its own population.

## Evidence labels

Evidence belongs to the FILE a symbol came from, not to the query, so every
`find-symbol` row carries one: `ast` when the file parsed and the row came
from the parser, `heuristic` when the row was a textual scan inside a parsed
file, `unknown` when the file has no AST extractor or its parse failed.

```
$ code-indexer find-symbol --inherits IndexerError --json \
    | jq '[.symbols[].evidence] | group_by(.) | map({(.[0]): length}) | add'
{
  "ast": 7
}

$ code-indexer find-symbol --language markdown --limit 3 --json \
    | jq '[.symbols[].evidence] | group_by(.) | map({(.[0]): length}) | add'
{
  "unknown": 3
}
```

Across the whole index (`index_revision=68`, 2,322 symbol rows) the ledger by
(source, verdict) is exactly `('ast','parsed')` 2,033 and
`('regex','parse_unavailable')` 289, so the `find-symbol` labels read `ast`
2,033 and `unknown` 289, `heuristic` 0. The missing `heuristic` bucket is the
honest shape of this repository: a textual row only stays `heuristic` inside
a file that parsed, and here the textual rows all live in the 794 files with
no grammar — so they are `unknown`, which is the stronger statement.

### absence is not a negative

```
$ code-indexer find-symbol --inherits Base --json | jq '.symbols[].evidence'
(no output: zero candidates)

$ code-indexer find-symbol --inherits Base --json \
    | jq '{match_count, evidence_note}'
{
  "match_count": 0,
  "evidence_note": "no symbol carries an attributed reference to 'Base'; 137 file(s) holding symbols have no AST extractor, so their references are unknown, not absent"
}
```

`Base` is a test-fixture base class, not a `src/` one, so this repository's
index truly has no candidate — and the answer still refuses to read as "no":
137 files could hold the reference and none of them was examined. In text
mode the same answer prints the sentence after `no symbols matching ...`.


## Ambiguity

A name with more than one definition answers with every candidate, never the
one that sorted first. `find_symbol` has two definitions here — the CLI
command and the MCP tool's wrapper:

```
$ code-indexer find-symbol find_symbol --json \
    | jq '{match_count, candidates: [.ambiguity.candidates[] | {file, range, kind, container, language}]}'
{
  "match_count": 2,
  "candidates": [
    {
      "file": "src/code_indexer/server.py",
      "range": {"start_line": 198, "end_line": 213},
      "kind": "function", "container": null, "language": "python"
    },
    {
      "file": "src/code_indexer/cli.py",
      "range": {"start_line": 1781, "end_line": 1801},
      "kind": "function", "container": null, "language": "python"
    }
  ]
}
```

### a command that must act on ONE symbol refuses

`find-definition` reports every definition, which is an answer; `--exactly-one`
is the surface that needs exactly one, and an ambiguous name is refused there
with `ArgumentError` (exit 2) and the candidate list:

```
$ code-indexer find-definition main --exactly-one
ArgumentError: name 'main' resolves to 50 symbols and this command needs
  exactly one — narrow it with --file, --kind or --container, or list them
  with find-symbol: runs/milestone0-gate/decision-gate/scripts/probe_jedi_absent.py:7-12 (function, container=None, decl_hash=sha256:cab0537...); runs/milestone0/dump_mcp_tools.py:9-28 (...); ...
exit=2
```

(The refusal is one physical line; it is wrapped here for reading. In `--json`
mode the same refusal carries `"ErrorType": "ArgumentError"` and a
`candidates` array.) `main` is the extreme case at 50 definitions; the
fixture case in `tests/test_ambiguity.py` pins the two-candidate version.

### the graph commands union and label

```
$ code-indexer find-callers find_symbol --max-nodes 8 --json \
    | jq '{match_count, candidates: (.ambiguity.candidates|length), roots: [.roots[] | {file, line}], targets: [.roots[].children[].targets] | .[0:2], truncated, omitted}'
{
  "match_count": 2,
  "candidates": 2,
  "roots": [
    {"file": "src/code_indexer/server.py", "line": 198},
    {"file": "src/code_indexer/cli.py", "line": 1781}
  ],
  "targets": [
    ["src/code_indexer/server.py:198-213", "src/code_indexer/cli.py:1781-1801"],
    ["src/code_indexer/server.py:198-213", "src/code_indexer/cli.py:1781-1801"]
  ],
  "truncated": true,
  "omitted": 208
}
```

Both definitions are roots of one walk, and each direct hit names the
candidate(s) it can belong to. The hits above are textual mentions in
`CHANGELOG.md`, `README.md` and `docs/`; a name-level reference cannot say
which definition it means, so it names both rather than picking one. Where
the store CAN attribute an edge it names one: in the fixture, an import edge
resolves to the imported file and therefore to exactly one candidate
(`tests/test_ambiguity.py::test_find_callers_unions_over_both_candidates_and_labels_each_hit`).

(Measured at the base commit: on this index two of `Core`'s nine definition
roots live in files with no grammar, and this block would label every root
`confidence: ast` while the ambiguity candidates for the same rows say
`evidence: unknown` — the one-payload-two-answers defect the follow-up leg
fixes and records in `runs/round3-folds/record.md`.)

## What I left

- **Scope.** Query-only: no schema change, no reindex, no new command, no
  index added. The MCP surface is untouched, so the new filters are CLI-only
  (the MCP `find_symbol` tool still forwards the flags it always forwarded).
- **`--name-pattern` is a case-sensitive GLOB.** Shell semantics, and the
  reason a prefix pattern is an index range. A leading wildcard cannot be a
  range, so `*Manager` scans `symbols` — measured, not assumed; the test
  asserts the index-backed cases against `EXPLAIN QUERY PLAN`.
  `*Manager` also has no hit on this repository.
- **`heuristic` has no rows here.** The label exists for a textual row inside
  a parsed file; this index has none, so the whole-index counts are `ast`
  2,033 / `unknown` 289. No synthetic example was invented to make the bucket
  populate.
- **`ambiguity` is emitted in exact-name mode only.** Browse and
  `--name-pattern` answers report `match_count` and `truncated`; they are not
  a request for one symbol, so they carry no candidate block. `find-callers`
  and `find-callees` carry `match_count`/`ambiguity` whenever the subject
  name has several definitions.
- **`--exactly-one` on `find-definition`** is where the single-symbol refusal
  lives. No shipped structural filter needs to pick, which is why the refusal
  is not on every command: `find-symbol` reports, `find-callers`/`-callees`
  union, and only a command that has declared "one" refuses.
- **README untouched.** Its claims are about the MCP tool mapping and the
  read contract, neither of which this round changed.
- **Not started:** the plan commands (next round) and any provider work.

## Verification

```
$ uv run pytest tests/ -q -W error
704 passed, 2 deselected in 96.77s

$ uv run pytest tests/test_structural_filters.py tests/test_ambiguity.py -q
24 passed in 3.36s

$ uvx ruff check src tests scripts
All checks passed!

$ uv run python scripts/publishing_hygiene_lint.py
publishing hygiene: scanned=620 skipped=0 violations=0
```
