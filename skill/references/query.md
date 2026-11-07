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
- **Flags**: `--project` <value>, `--path-prefix` <value>, `--max-lines` <value> (default `60`), `--json`, `--format` <value>, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `skeleton`

- **Class**: read
- **Arguments**: `path_prefix` (optional)
- **Flags**: `--project` / `--name` <value>, `--tree`, `--no-signatures`, `--limit` <value>, `--json`, `--format` <value>, `--skip-stale-check`, `--refresh`, `--fresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `map`

- **Class**: read
- **Arguments**: `path_prefix` (optional)
- **Flags**: `--project` / `--name` <value>, `--tree`, `--no-signatures`, `--limit` <value>, `--json`, `--format` <value>, `--skip-stale-check`, `--refresh`, `--fresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `outline`

- **Class**: read
- **Arguments**: `file` (required)
- **Flags**: `--project` / `--name` <value>, `--docstrings`, `--json`, `--format` <value>, `--skip-stale-check`, `--refresh`, `--fresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `file-outline`

- **Class**: read
- **Arguments**: `file` (required)
- **Flags**: `--project` / `--name` <value>, `--docstrings`, `--json`, `--format` <value>, `--skip-stale-check`, `--refresh`, `--fresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `find-symbol`

- **Class**: read
- **Arguments**: `name` (optional)
- **Flags**: `--symbol` <value>, `--project` / `--name` <value>, `--kind` / `--type` / `--symbol-type` <value>, `--file` <value>, `--name-pattern` <value>, `--container` <value>, `--visibility` <value>, `--language` <value>, `--inherits` <value>, `--has-call` <value>, `--substring`, `--limit` <value>, `--json`, `--format` <value>, `--skip-stale-check`, `--refresh`, `--fresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `find-definition`

- **Class**: read
- **Arguments**: `name` (optional)
- **Flags**: `--symbol` <value>, `--project` / `--name` <value>, `--json`, `--format` <value>, `--exactly-one`, `--skip-stale-check`, `--refresh`, `--fresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `find-references`

- **Class**: read
- **Arguments**: `name` (required)
- **Flags**: `--project` / `--name` <value>, `--relationship` <value>, `--limit` <value> (default `25`), `--json`, `--format` <value>, `--skip-stale-check`, `--refresh`, `--fresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `find-callers`

- **Class**: read
- **Arguments**: `name` (required)
- **Flags**: `--project` <value>, `--depth` <value> (default `2`), `--max-nodes` <value> (default `40`), `--json`, `--format` <value>, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `find-callees`

- **Class**: read
- **Arguments**: `name` (required)
- **Flags**: `--project` <value>, `--depth` <value> (default `2`), `--max-nodes` <value> (default `40`), `--json`, `--format` <value>, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `get-code-context`

- **Class**: read
- **Arguments**: `file` (optional)
- **Flags**: `--project` / `--name` <value>, `--start-line` <value>, `--end-line` <value>, `--symbol` <value>, `--context-lines` <value>, `--json`, `--format` <value>, `--skip-stale-check`, `--refresh`, `--fresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `deps`

- **Class**: read
- **Arguments**: `path` (required)
- **Flags**: `--project` <value>, `--direction` <value> (default `both`), `--depth` <value> (default `2`), `--max-nodes` <value> (default `60`), `--format` <value> (default `tree`), `--skip-stale-check`, `--refresh`
- **JSON**: text only, no `--json`.

### `changed-symbols`

- **Class**: read
- **Arguments**: none
- **Flags**: `--project` <value>, `--base` <value> (default `HEAD`), `--head` <value>, `--staged`, `--include-untracked`, `--impact`, `--depth` <value> (default `1`), `--max-nodes` <value> (default `40`), `--json`, `--format` <value>, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).
<!-- generated:commands-query:end -->

## One flag for every format, one shape for every symbol read

Every read command here takes `--format text|json`, and `--json` is the EXACT
alias of `--format json`: both spellings render the same bytes. Passing both
is accepted when they agree (`--json --format json` asks the same question
twice) and is `ArgumentError`, exit 2, when they name different formats --
never a silent precedence, one invocation, one answer format.
`--format compact` is a one-line-per-record rendering built only from
fields the text form already prints; it exists on `overview`,
`index-status`, `skeleton`, `outline`, `find-symbol`, `find-definition` and
`semantic-search`, and on any other read it is refused with `ArgumentError`,
exit 2, naming `text|json` (documented refusal, never a silent fallback).
`index-status` prints one line already, so its compact form is that line.
`deps --format` is a different flag with its own vocabulary (`tree|edges|json`)
and keeps its meaning.

The three symbol reads take the symbol the same way: `--symbol <name>` beside
`--project <p>`. `find-symbol <name>` and `find-definition <name>` keep their
positional spelling as the alias of the same argument; `get-code-context`
keeps its FILE positional. With no FILE, `get-code-context --symbol <name>`
resolves the name project-wide through the manifest: one definition answers
that file's span, several are refused with `AmbiguousSymbolError` (an
`ArgumentError`, exit 2) listing every candidate, none is `NotFoundError`
(exit 1). Two spellings of one symbol that disagree are refused too.

## Orientation, then lookup, then reading

```bash
# what is here: languages, directories, entry points, hotspots
code-indexer overview --project /path/to/repo

# the structural map of one subtree, signatures included
code-indexer skeleton --project /path/to/repo src/code_indexer

# a name's every definition
code-indexer find-definition Tracker
```

## One file: declarations and docstrings

```bash
# every declaration in one file, with its signature and first docstring line
code-indexer outline src/code_indexer/core.py --docstrings
```

`outline` answers from the manifest's symbol rows for one file: name, kind,
range, signature, and with `--docstrings` each declaration's first docstring
line beneath it. `file-outline` is the older spelling of the same command.

## Where the order comes from

Every ranked answer is ordered by one policy: the class ladder -- source, then
examples, then tests, then docs, then generated/artifact -- with the surface's
own second key behind it. That policy, its two waivers, the per-surface key
table and the two walks that keep their own order live in
[semantic.md](semantic.md#ordering-source-then-samples-tests-docs-artifacts),
the file that owns them; nothing here restates them.

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
  level is class-ordered before the node cap fills (the two walks are in
  [semantic.md](semantic.md#the-two-walks-that-keep-their-own-order)).
- `find-callees` is the one graph arm outside the policy: call-site order,
  because "what does this symbol reach" reads one body.

Under these walks the store hands over everything it holds: the graph reads
take `limit=None` by default, and a cap belongs to the layer above, which
announces what it refused in `omitted`.

## Reading source

```bash
code-indexer get-code-context src/code_indexer/core.py --start-line 40 --end-line 80

# the symbol alone: the FILE is resolved across the project
code-indexer get-code-context --symbol search --project /path/to/repo

# a symbol's whole span, padded by three lines each side
code-indexer get-code-context src/code_indexer/core.py --symbol search --context-lines 3
```

With no FILE positional the name is resolved project-wide through the
manifest's definitions, and the answer is that definition's own file and span
(the same code path as the file+symbol form, so an ambiguous name lists every
candidate instead of answering one).

`--symbol` matches the name the file's rows carry, which is the bare
declaration name (`search`, `__init__`) even when the symbol's
`qualified_name` is dotted (`Core.search`, as `find-symbol` reports it). The
dotted spelling is not a locator here: `--symbol Core.search` answers
`InternalError: symbol 'Core.search' not indexed in ...` and exits 1. Use
`find-symbol <name> --container Core` to learn the row's name, then pass that.

`get-code-context` checks the live file hash against the manifest. In
`--symbol` mode it re-resolves the symbol on the live file; otherwise it
answers from that file's own symbol rows -- every matching range, with
overlapping or touching ranges of one name merged into one span per
contiguous run, `--context-lines` padding applied to each merged span after.
Line-range mode on a changed file returns the requested lines plus one
`Warning: ...` line on stderr and `stale: true` in JSON, exit 0.

## What changed

```bash
# symbols touched by the working-tree changes
code-indexer changed-symbols --include-untracked

# the same read across two revisions, with the callers and candidate tests
code-indexer changed-symbols --base HEAD~5 --head HEAD --impact
```

`--base` defaults to `HEAD` and `--head` to the working tree, so the two flags
name a revision range the way `git diff` does (`--staged` is the index-only
form). `changed-symbols` parses NEW/OLD contents out of the git diff, so it
needs no index of the changed lines. `--impact` lists callers and candidate
tests under each changed symbol as `impact: <file> <symbol> (<confidence>)`,
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
