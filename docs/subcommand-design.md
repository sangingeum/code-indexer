# Token-reduction subcommands — design

Status: design (no implementation). Author: solomon, refining the owner's
proposal in `docs-token-reduction-plan.md`. Target: grace (Python/uv).

## 0. Design principles

1. **Reuse stores first.** The schema-v2 manifest already has `symbols`
   (file, name, symbol_type, start_line, end_line, source) and
   `symbol_refs` (file, line, src_symbol, relationship, target). Anything
   derivable from those two tables costs zero parse time and stays fresh
   with the existing staleness pipeline.
2. **Signatures are the one honest gap.** The manifest stores *boundaries*
   (start/end line) but not the declaration *text*. A signature is the text
   of the declaration up to the body. Two options were weighed:
   - (A) re-parse per query with tree-sitter — correct but pays a full
     parse per file on every call, against the whole point of token
     reduction (latency + CPU, and repeated calls re-pay it);
   - (B) **persist signatures at index time** (schema v3: add a
     `signature TEXT` column to `symbols`, extracted in the same AST walk
     that already emits the row — cost is one substring slice per symbol,
     paid once per file version).
   Decision: **B**. This is the only real AST work in this design, it is
   small, and it lands inside `ts_chunker.py`'s existing walk.
   For regex-chunked files (no AST), signature = first line of the chunk
   (best-effort, `source` stays `regex` so consumers know).
3. **Dense default output.** One symbol per line, no prose headers, no
   blank lines. `--json` for structured consumers (parity with existing
   `--json` on `semantic-search`).
4. **1:1 CLI ↔ core ↔ MCP** (existing house pattern in `cli.py` /
   `core.py` / `server.py`): every new subcommand gets a core op and an
   MCP tool of the same name.
5. **No new subcommand where a flag on an existing one is honest.**
   See the pruned list — this is where this design deliberately
   disagrees with the owner's plan.

## 1. Prioritized subcommand set

### Kept — phase 1 (highest ROI)

| Subcommand | Aliases | Purpose |
|---|---|---|
| `skeleton` | `map` | Whole-project or per-subtree structural map: files with their symbol lines. |
| `outline` | `file-outline` | One file: declarations, signatures, one-line docstrings. |

### Kept — phase 2 (after phase 1 lands)

| Subcommand | Purpose |
|---|---|
| `deps` | File/module-level import/include graph from `symbol_refs` (`includes` edges). |
| `public-api` | Symbols filtered to public visibility (visibility computed at index time, see §3). |

### Pruned — and why (explicit disagreements with the owner's plan)

| Owner's proposal | Verdict | Reason |
|---|---|---|
| `list-symbols` / `symbols` | **Merge into `find-symbol`** | `find-symbol` already queries the same `symbols` table via `Manifest.find_symbols`. The only real gap: it requires a name. Fix by making `name` optional with `--type`/`--file`/`--limit` filters. A new subcommand would duplicate the query path and force MCP to expose two tools for one table. |
| `architecture` / `overview` | **Defer, then compose** | Everything it would print (languages, per-file counts, entry points, hotspots) is derivable from `files` + `symbols` + `symbol_refs`; a dedicated subcommand is a formatting exercise with product decisions (what is an "entry point"?) that shouldn't block phase 1. Once `skeleton` exists, an overview can be added as `skeleton --summary` cheaply. Deferring costs nothing. |
| `snippet` / `signature-only` | **Drop** | `get-code-context` already returns exactly the relevant lines with `context_lines=0` around a symbol. A second tool for "first N lines of the same thing" adds surface, not capability. If callers want only the signature, phase 1's persisted signatures let `find-symbol` print them for free. |
| `callers` / `callees` graph view | **Defer** | `find-references` covers the same `symbol_refs` table with `--relationship calls`. A compact graph is a nice-to-have rendering, not new data. Revisit only if real usage shows agents chaining find-references + get-code-context repeatedly. |
| `tree` / `file-tree` | **Fold into `skeleton`** (`--tree` mode) | A directory tree is a degenerate skeleton (files only, no symbols). Two commands for one projection invites drift; `--tree` costs one flag. |
| `compress-context` | **Drop for now** | Body-stripped re-rendering of retrieved chunks is real tree-sitter work (node-level elision per language) for a gain already mostly delivered by `get-code-context` line ranges + chunk caps (~1000 chars). Largest remaining effort/ROI ratio in the plan. Revisit only with evidence. |

## 2. Command specs

Conventions carried over unchanged: `--project X` / `--name X` both accepted
for project resolution (path, slug, or custom name), `--skip-stale-check` on
every subcommand, `--json` for structured output, exit 1 with
`error: ...` on stderr for resolution failures.

### 2.1 `skeleton` (alias `map`)

```
code-indexer skeleton [--project P | --name P] [PATH_PREFIX]
    [--tree] [--json] [--limit N] [--skip-stale-check]
```

- `PATH_PREFIX` (optional positional, default whole project): restrict to
  files under this relative path. Prefix match on manifest paths.
- Default output — one file per group, one symbol per line:

```
src/session.hpp  (cpp, 412 lines, 18 symbols)
  class FileTransferSession            :18-95
  function start_transfer              :102-140  int start_transfer(const std::string& id)
  ...
```

Symbol line format: `  <TYPE> <name>:<start>-<end>[  <signature>]`.
Signature included when stored (schema v3); omitted with `--no-signatures`
for the absolute densest output. Files with zero symbols print just the
header line — still useful ("exists, is empty of declarations").

- `--tree` mode: directory tree, each dir annotated with symbol count
  and dominant language. No symbols listed. This replaces the owner's
  `tree` proposal.
- Data source: **manifest only** — `files` (path, chunk_count→derive line
  estimates are NOT stored; `size` and chunk_count are) joined with
  `symbols` ordered by `start_line`. Zero re-parsing. If signatures are
  missing (pre-v3 manifest), emit the migration hint already defined in
  `core.MIGRATION_HINT`.
- Ordering: manifest path order (natural, stable). `--limit N` caps
  symbol lines per file (default: unlimited; tree mode ignores it).
- JSON: `{"file": "...", "lang": "...", "symbols": [{"name","type",
  "start_line","end_line","signature"}]}` array.

### 2.2 `outline` (alias `file-outline`)

```
code-indexer outline [--project P | --name P] FILE [--docstrings]
    [--json] [--skip-stale-check]
```

- `FILE` is relative to the project root (same path space as the
  manifest's `files.path`). Absolute paths are normalized by stripping the
  project root prefix.
- Default output, one declaration per line:

```
class FileTransferSession:18-95
  method start_transfer:102-140  int start_transfer(const std::string& id)
  method abort:141-160  void abort() noexcept
```

- `--docstrings` adds one docstring line (first line only, truncated to
  ~100 chars) under each declaration when extractable. Docstrings are NOT
  stored at index time (they'd bloat the manifest for a rare flag);
  extraction for `--docstrings` reads the declaration's first line(s) from
  the source file on disk — one small bounded read, acceptable for a
  single-file command. This is the only on-demand source read in the
  design and it is honest about it.
- Data source: `Manifest.symbols_for_file(file)` + schema-v3 signature
  column. Falls back to decl-line read if signature is NULL (regex rows).
- JSON: array of `{name, type, start_line, end_line, signature, doc?}`.

### 2.3 `find-symbol` extension (not a new command)

```
code-indexer find-symbol [NAME] [--type T] [--file F] [--substring]
    [--project P | --name P] [--json] [--limit 25] [--skip-stale-check]
```

- `NAME` becomes optional. Omitted name + `--type`/`--file` filters =
  browse mode (this replaces `list-symbols`). Output gains the signature
  field once schema v3 lands:

```
function start_transfer  src/session.hpp:102-140  int start_transfer(const std::string& id)
```

- Data source: unchanged (`Manifest.find_symbols`); the method grows
  optional `file` filter and the query drops the name clause when None.
  Cap stays at `--limit` (default 25) — a nameless query on a big repo is
  exactly where an unbounded dump would destroy the token budget.
- MCP note: the MCP server exposes browse mode as a **separate
  `find_symbols` tool** with its own cap (find_symbol keeps its exact/
  substring semantics); the CLI extends `find-symbol` in place. 1:1
  core, two surfaces.

### 2.4 `deps` (phase 2)

```
code-indexer deps [--project P | --name P] [FILE] [--reverse] [--json]
    [--skip-stale-check]
```

- Forward mode: for each file (or one file), the `includes` targets from
  `symbol_refs` (`relationship='includes'` — covers Python imports and
  C/C++ includes today). One file per line:

```
src/session.hpp -> session.h, types.h, <vector>
```

- `--reverse`: who includes this file. Data source: `symbol_refs` only,
  zero parsing.
- **Honest limitation, scoped:** `extract_refs`'s `_IMPORT_RE` only
  matches Python imports and `_INCLUDE_RE` only C preprocessor includes.
  JS/TS (`import`/`export ... from`), Rust (`use`), Go (`import`), Java
  (`import`) are missing. Phase 2 includes a ~20-line extension of
  `_IMPORT_RE` in `ts_chunker.py` to cover those line shapes — regex
  line-shapes, no per-grammar AST work. Unresolved-module-name edges stay
  heuristic, same policy as calls today.

### 2.5 `public-api` (phase 2)

```
code-indexer public-api [--project P | --name P] [PATH_PREFIX]
    [--json] [--skip-stale-check]
```

- Output identical in shape to `skeleton` but filtered to public symbols.
- **Visibility is computed at index time** (schema v3, second new column
  `visibility TEXT` — values `public` / `private`):
  - Python: leading underscore in name → private (leading `__` inside
    classes → private). Module-level `__all__` handling is out of scope.
  - C/C++: class-member access sections are not tracked by the current
    walk — everything is `public`; `static` file-scope functions are not
    distinguishable. Honest: for C/C++ this filter is weak. Do not
    oversell it in the help text.
  - Rust: leading `_` or `pub` absent → private (needs a small node
    check in the walk).
- Data source: `symbols` filtered on the new column; no query-time
  parsing.

## 3. Schema and indexer changes (the only AST work)

**Schema v3** (`manifest.py`): `symbols` gains two columns,
`signature TEXT` and `visibility TEXT`. Migration: `ALTER TABLE ... ADD
COLUMN` guarded by a version check (the existing meta-version bump logic
in `Manifest.__init__` already handles the version compare; add the ALTERs
before the version write). Existing rows keep NULL signature/visibility —
readers must tolerate NULL (print no signature; treat as public).

**Extraction** (`ts_chunker.py`, inside the existing `_walk` /
`extract_symbols` pass — no second walk):

- `signature`: for AST units, the declaration text from `start_byte` up to
  the body-opening byte (`body` field of the node when the grammar
  exposes one: python `block`, C-family compound_statement, rust
  field_declaration_list, etc.), first line only, whitespace-collapsed,
  capped at 120 chars. When no body node is found, first physical line.
  This is a node-field lookup plus one slice — the grammar-specific body
  node names live in one small dict next to `_TYPE_BY_NODE`.
- `visibility`: per §2.5 rules, evaluated on the symbol name plus a
  boolean "has pub/has export" check where the grammar makes it visible
  (rust `pub`, js/ts `export_statement` — the latter already exists as a
  unit type). C/C++/Java default `public` (documented limitation).

`extract_symbols` returns both new fields; `indexer.py` passes them through
to `Manifest.replace_file_symbols` (extend the INSERT). `chunker.py`
regex-fallback path emits signature=first-line, visibility per the Python
underscore rule only (other languages → public).

Nothing else re-parses at query time. The staleness/indexing pipeline,
chunking, and Qdrant side are untouched.

## 4. Implementation notes for grace

Files touched, in order:

1. `manifest.py` — schema v3 (two columns + migration), extend
   `SymbolRow`, `replace_file_symbols`, `find_symbols` (optional name,
   `file` filter, `visibility` filter), `imports_for(file, reverse=False)` helpers.
2. `ts_chunker.py` — signature + visibility extraction in the existing
   walk; body-node-name dict; extend `_IMPORT_RE` family (phase 2).
3. `chunker.py` — regex-fallback `extract_symbols` fills the two new
   fields best-effort.
4. `indexer.py` — thread the new fields through to the manifest.
5. `core.py` — new ops: `skeleton(entry, prefix, tree_mode, ...)` and
   `outline(entry, file, ...)` (+ `deps`, `public-api` in phase 2), each
   with a `format_*` twin following `format_hits`' text/json pattern, and
   `find-symbol` core op gaining the filter params. All run
   `maybe_refresh` first unless skipped (same contract as search).
6. `cli.py` — `skeleton`, `outline` commands (+ aliases via
   `app.command(name=...)`), extend `find_symbol`; `deps`/`public-api`
   phase 2. Shared project-resolution helper `_resolve` already exists.
7. `server.py` — MCP tools `skeleton`, `outline` (+2 later), same
   parameter names as CLI flags per the 1:1 rule.
8. `README.md`, `SKILL-CLI.md` — new subcommand lines + tools table rows.

Test strategy (`tests/`, pytest, following `test_codeintel.py`):

- Unit: manifest v3 migration (v2 db → columns added, old rows NULL,
  version bumped exactly once); signature extraction fixtures for python
  + cpp (decorated defs, C++ qualified names, >120-char signatures
  truncated); visibility rules per language; regex-fallback path.
- Command-level: build a small fixture project, index it, then assert
  dense text output shape (one line per symbol, no blank lines) and JSON
  contract for `skeleton`/`outline`; `find-symbol` with no name +
  filters; `--skip-stale-check` short-circuit still applies.
- Concurrency guard: an existing-test-style check that `skeleton` under a
  concurrent index pass reads WAL-consistently (reads never block, never
  see torn symbol rows — replace_file_symbols is atomic per file).
- Edge cases: empty file, file with no symbols, PATH_PREFIX with no
  matches (empty output, exit 0 — not an error), `outline` on a file not
  in the manifest (error, exit 1), pre-v3 manifest (hint, no crash).

Phase 1 = items 1–8 for skeleton/outline/find-symbol only. Phase 2 adds
deps + public-api + the import-regex extension. Do not mix phases in one
PR.

## 5. Open questions for the owner

- `skeleton` default line count on very large repos: proposal prints
  everything and lets `--limit`/PATH_PREFIX bound it; alternative is a
  default per-file symbol cap. Left as full output — agents invoke with
  PATH_PREFIX, which is the cheaper control.
- Whether MCP parity is required on day one or CLI-first. The 1:1 rule
  suggests same-PR MCP tools; flagged in case the owner wants a thinner
  first cut.