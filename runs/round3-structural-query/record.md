# Structural query on a stable identity

Round 3 of the overhaul plan, governed by ADR-0002 sections 4 and 5. Query
only: no schema change, no reindex, no new command. Every command below was
run against this repository's own index (`<project>`, schema 7,
`rows=current`); the index was stale by one added test file at the time, which
the answers report on their own warning line and which changes no symbol row.

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
- <project>::src/code_indexer/watcher.py:140-157 AlreadyRunning (class, confidence=exact)  class AlreadyRunning(Exception):
- <project>::src/code_indexer/errors.py:52-54 ArgumentError (class, confidence=exact)  class ArgumentError(IndexerError):
- <project>::src/code_indexer/errors.py:67-71 BackendError (class, confidence=exact)  class BackendError(IndexerError):
... 3 of 133 matches shown (--limit 3 reached; raise it to see the rest)

$ code-indexer find-symbol --container Manifest --limit 4
- <project>::src/code_indexer/manifest.py:276-293 __init__ (function, confidence=exact)  def __init__(self, db_path: str, *, writable: bool = True):
- <project>::src/code_indexer/manifest.py:363-366 _file_select (function, confidence=exact)  def _file_select(self) -> str:
- <project>::src/code_indexer/manifest.py:611-628 _symbol_filters (function, confidence=exact)  def _symbol_filters(self, name: str | None = None,
- <project>::src/code_indexer/manifest.py:543-560 _symbol_row_tuple (function, confidence=exact)  def _symbol_row_tuple(file: str, s: SymbolRow,
... 4 of 49 matches shown (--limit 4 reached; raise it to see the rest)

$ code-indexer find-symbol --visibility private --limit 3
- <project>::tests/test_output_contract.py:145-147 _Capture (class, confidence=exact)  class _Capture(logging.Handler):
- <project>::tests/test_indexer_embed_context.py:12-24 _FakeEmbedder (class, confidence=exact)  class _FakeEmbedder:
- <project>::tests/test_embed_format.py:18-30 _FakeEmbedder (class, confidence=exact)  class _FakeEmbedder:
... 3 of 372 matches shown (--limit 3 reached; raise it to see the rest)

$ code-indexer find-symbol --language markdown --limit 3
- <project>::CHANGELOG.md:71-85 JSON (function, confidence=heuristic)  - **Per-side staleness.** `index-status` reports `rows=current|absent`,
- <project>::docs/overhaul-baseline-audit.md:211-236 KEY (function, confidence=heuristic)  LINE INTEGER NOT NULL,
- <project>::docs/subcommand-design.md:211-230 NULL (function, confidence=heuristic)  CHECK in the walk).
... 3 of 93 matches shown (--limit 3 reached; raise it to see the rest)

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
- <project>::src/code_indexer/core.py:1210-1227 _fuse_lexical_dense (function, confidence=exact)  def _fuse_lexical_dense(self, entry: ProjectEntry, query: str,
- <project>::tests/test_identity.py:132-138 _rows (function, confidence=exact)  def _rows(core: Core, entry, name: str | None = None,
- <project>::src/code_indexer/evalharness.py:100-117 _symbol_ranges (manifest: Any, rel_file: str,
- <project>::src/code_indexer/core.py:1636-1653 code_context (function, confidence=exact)  def code_context(self, entry: ProjectEntry, file: str,
- <project>::src/code_indexer/manifest.py:775-788 definitions_of (function, confidence=exact)  def definitions_of(self, name: str,
... 5 of 25 matches shown (--limit 5 reached; raise it to see the rest)
```

`--inherits` on this repository has two answers worth keeping apart:
`IndexerError` is a base class that seven symbols name, and `Base` matches
nothing here at all — the fixture's `Base` lives in the tests, not in
`src/`. A zero from either filter is labelled (see the evidence section),
never printed as "no".

### composition

```
$ code-indexer find-symbol --kind function --container Manifest --has-call find_symbols
- <project>::src/code_indexer/manifest.py:775-788 definitions_of (function, confidence=exact)  def definitions_of(self, name: str,
```

### truncation

`--limit` keeps both its meaning and its default (25 in browse mode, none in
exact-name mode), and a capped answer says it was capped:

```
$ code-indexer find-symbol --language python --limit 5 --json | jq '{match_count, shown: (.symbols|length), truncated}'
{
  "match_count": 2026,
  "shown": 5,
  "truncated": true
}
```

The same answer in text mode ends with
`... 5 of 2026 matches shown (--limit 5 reached; raise it to see the rest)`.
`match_count` is computed from the SAME clause set as the rows, so a capped
answer can never quietly disagree with its own population.

## Evidence labels

(to be filled by step 2)

## Ambiguity

(to be filled by step 3)

## What I left

(to be filled before the push)
