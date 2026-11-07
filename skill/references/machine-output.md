# machine-output -- reading the answers as data

Governed by: ADR-0003 sections 3 and 7 (the staleness block, `schema` 1, the
commands that have `--json`); ADR-0004 (the plan commands' shapes); ADR-0009
section 2 (this file's contents and budget). Open this reference when an
agent parses output instead of reading prose.

Every `--json` answer is one object on stdout, additive, `schema` stays `1`.
A failure prints nothing on stdout and one `ErrorType: message` line on
stderr (see [failures.md](failures.md)).

## Global flags

Every command accepts `--skip-stale-check`. On every command but one it is
**inert**: reads never index, so there is nothing to skip, and reporting
staleness without repairing it is the default. `plan-apply` is the
exception -- it refuses the flag (`ArgumentError`, exit 2), because a plan's
staleness is not skippable when a source file is about to be written.

Exactly seven commands also accept `--fresh`: `semantic-search`, `skeleton`,
`outline`, `find-symbol`, `find-definition`, `get-code-context` and
`find-references`. It is absent on `find-callers`, `find-callees`, `deps`,
`overview` and `changed-symbols`. Only on `get-code-context` does it still
mean "read the live file", and it never writes.

`--refresh` is accepted by every read command and is the **only flag that
turns a read into a write**: it takes the per-project flock, runs one
incremental pass and answers from the result. A held lock is `LockedError:
indexing in progress`, exit 5, no answer on stdout. It is also accepted by
`doctor` (refreshes each registered project before the checks), by `eval`
(refreshes the evaluated project), and is accepted-and-inert on
`eval-compare`, which reads report files. The plan reads (`plan-validate`,
`plan-show`) accept `--skip-stale-check` and **not** `--refresh`: they answer
from a plan file and the live sources.

`--format text|json` is on every read command that took `--json`, and
`--json` is its exact alias: both spellings print the same bytes. Giving both
is accepted when the two spellings agree -- `--json --format json` is the same
answer asked twice -- and is a usage error (`ArgumentError`, exit 2) naming the
contradiction when they name different formats: never a silent precedence.
`--version` on the top level prints the installed package version on one line
and exits 0; it needs no command and no project, and no subcommand's own
options change. `--format compact` is described below.

## Compact output

`--format compact` answers one line per record, built only from fields the
text form already prints, so a caller parses a record without tracking a
header above it:

| Command | One compact line is |
|---|---|
| `overview --format compact` | `<tag> <fields>` -- `project`, `language`, `directory`, `entry-point`, `tests`, `test-file`, `config`, `hotspot-fan-in`, `hotspot-size` |
| `index-status --format compact` | the same single `key=value` line the text form prints |
| `skeleton --format compact` | `file:start-end name (kind)`; in `--tree` mode `dir/ symbols=N dominant=LANG` |
| `outline --format compact` | `file:start-end name (kind)`, one per declaration |
| `find-symbol --format compact` | `file:start-end name (kind)`, one per row; a capping or ambiguity note keeps its own line |
| `find-definition --format compact` | `file:start-end name (kind)`, one per definition |
| `semantic-search --format compact` | `path:start-end  symbol  score` |

A compact symbol record carries the file, the range, the name and the kind;
the text form's `confidence`, `evidence` and signature are not part of it --
read `--format json` when those are the question.

Every other read command REFUSES `compact` with `ArgumentError`, exit 2, and
a message naming `text|json`: the refusal is part of the contract, not a
missing feature. `deps --format` is a separate flag (`tree|edges|json`) and
keeps its meaning.

Compact labels are normalized field names, not the text form's own spellings:
`loc` prints as `LOC`, `directory` as `directories:`, `share` as `(100.0%)`,
`hotspot-fan-in`/`hotspot-size` as `hotspots (fan-in):`/`hotspots (size):`,
`hotspots.unattributed` as `hotspot-unattributed references=N` (emitted only
when non-zero), and `fan_in` has no separate text label. Same field, one
spelling per form;
no emitted label changes.

## The staleness block

Every read answer that comes from the index carries these fields in JSON:

| field | meaning |
|---|---|
| `stale` | boolean, unchanged meaning; on `get-code-context --json` it keeps its file-level meaning |
| `stale_known` | boolean; false means the verdict is `unknown`, so `stale: false` alone never means "checked and fresh" |
| `stale_reasons` | `[{kind: modified\|added\|deleted, file}]`, capped at five |
| `stale_reason_count` | total count before the cap |
| `withheld_count` | files the sensitive layer skipped; present on a `fresh` verdict, so a withheld file is a skip, not a stale reason |
| `withheld_files` | `[{file, reason: sensitive filename\|sensitive content}]`, capped at five while `withheld_count` is the total |
| `withheld_summary` | the same phrase the text line prints -- `1 file skipped (sensitive content)` -- or null |
| `withheld_unexamined_count` | rowless paths the re-check's budget never reached; they ride the `added` reasons, so a non-zero value means the re-check was truncated |
| `unreadable_count` | rowless paths the check could NOT open (permission, a broken symlink target, an I/O error); present on a `fresh` verdict, and never a stale reason -- no reindex clears a condition that stops the read. The scanner drops a path it cannot open, so only a path the scan returned reaches this state |
| `unreadable_files` | `[{file}]`, capped at five while `unreadable_count` is the total |
| `unreadable_summary` | `1 file could not be read (permission or I/O error)`, or null |
| `index_revision` | the revision the answer was read from |
| `last_indexed` | timestamp of the pass that wrote the index, ISO-8601 UTC |

Text mode prints exactly one stderr line and nothing extra on stdout:

```
Warning: index is stale: 2 modified files, 1 deleted file -- <path>; answers describe the index, not the tree; refresh with code-indexer reindex-project <path>
```

That line is a STATELESS COUNT with its meaning and its repair, identical on
every read while the tree stays stale: the kinds of change, the project, the
sentence saying the answer describes the index rather than the tree, and the
command that refreshes it -- and nothing else. The sentence is part of the
line, not a courtesy: a bare count reads as a fact about the tree, which is
exactly the misreading the verdict exists to prevent. The per-file reasons,
`index_revision` and `last_indexed` stay in the block above on every call,
and `index-status` prints them on request. There is no session token and no
state file -- a CLI cannot know it is the same reader, so it never suppresses
a warning it cannot verify was already seen, and it never lets a stale read
look fresh. The line is short because a repeated paragraph becomes wallpaper;
it is not short enough to hide the verdict.

A file the sensitive layer withholds is a skip, not a stale reason, and its
phrase rides beside the count in the same line (`1 file skipped (sensitive
content)`) unless the command already prints that state itself --
`index-status` does, so its warning omits it and the fact appears once.

A path the check could not OPEN is the same kind of fact, named the same way:
`1 file could not be read (permission or I/O error)`. It is not "withheld"
(nothing sensitive was seen) and not "added" (nothing was compared), and
reporting it as `added` would leave a permanent stale banner that no reindex
can clear. The scanner drops a path it cannot open before the diff runs, so
this state reports what the diff saw, and no more: a path the scan never
returned -- a stable `chmod 000`, a dangling symlink -- is not counted.

A fresh answer prints nothing extra -- unless a path was unreadable, in which
case the one line states it (`Warning: 1 file could not be read (permission or
I/O error), so part of the tree was not checked -- <path>; answers describe
the index, not the tree.`), because silence there would read as "everything
was compared". An `unknown` answer prints one line saying the check did not
run. Either way the answer describes the index as of
`last_indexed`, never the tree.

## The coverage block (every read answer)

Every read command's JSON carries one `coverage` object beside the staleness
block: what the answer could actually see. A caller reads it instead of
running `index-status` to decide whether to trust the answer.

| field | meaning |
|---|---|
| `coverage` | `{scope, files, files_parsed, files_not_parsed, files_withheld, files_unreadable, languages_without_ast, freshness, complete}` -- `scope` is `project` (the whole index), `answer` (the files this answer read: `outline`, `get-code-context`, `find-symbol --file`) or `none` (the command reads no index: `doctor`, `list-projects`, the eval reports, the plan readers, an unregistered `lookup-project`). A `none` block has null counts, never zeros; `complete` is null there too. `complete` is true only when nothing the scan returned is missing -- a path the scanner could not open never entered the scan, so it is not counted. It is about parsed-versus-unparsed files, not about the index holding anything: on an index that holds no files it is vacuously true, so read `files: 0` (or `empty_reason: index-empty`), not `complete`. |
| `empty_reason` | one value for a read that found nothing -- `name-absent`, `files-not-parsed` (count and languages in the sentence), `language-without-ast` (language named) or `index-empty` -- in that fixed precedence, with `empty_reason_sentence` carrying the same sentence the text form prints. The VALUES are shared by the name reads and `semantic-search`; the sentence is worded per surface (a search says its query matched nothing, never that "the name is absent"). Absent on a `semantic-search --all-projects` fan-out, which has no single index. |

`files_not_parsed` counts `parse_error` + `parse_unavailable` + rows with no
verdict. `files_withheld` intersects the staleness block's withheld list with
the answer's scope. `files_unreadable` does the same with the unreadable list:
rowless paths the check could not open (the scanner already dropped the ones it
could not open, so only a path the scan returned is here), which are neither
parsed nor withheld, and count against `complete`. `languages_without_ast` lists the languages in scope whose
files yield no AST symbol rows -- a shell-only project whose files parsed
still says `["shell"]` -- and `freshness` repeats the staleness verdict, never
a second estimate.

The block does not narrow with a prefix. `overview --path-prefix` and a
`skeleton` subtree argument narrow the FILES an answer reports; `scope` stays
`project` and the counts stay the whole index's, because the question the block
answers is whether an absence in THIS answer can be trusted, and a file
outside the prefix can only make a prefix answer look less complete, never
more. The counts therefore stay comparable with `index-status`; a subtree's
own totals are not what this block reports, and a caller that wants them
narrows its own reading of the answer instead.

The text form adds ONE short line when coverage is incomplete. On a normal
answer it goes to **stderr** (stdout stays the answer, so a cap such as
`overview --max-lines` counts stdout alone); on an empty answer there is no
answer to keep clean, so the cause and this line both go to **stdout**:

```
coverage: 124 of 184 files parsed, 60 not parsed; 1 file withheld for privacy
```

On a complete answer neither stream carries the line, so it is a signal rather
than wallpaper. An empty answer prints its cause after the legacy lead, worded
per surface: a symbol read says "the name is absent ..." while
`semantic-search` says what its query found:

```
no symbols matching 'X': the name is absent from an index that parsed 124 of 184 files
no results: nothing in an index that parsed 124 of 184 files matched the query
```

A `semantic-search --all-projects` fan-out is the exception: with no hits it
prints the bare `no results` and carries no `empty_reason`, because the
vocabulary describes one index and a fan-out has none (`coverage.scope` is
`none` with null counts; the per-project split is the caller's source of
detail). Exit codes do not move: an empty answer is still not an error.

## Which commands answer as data

<!-- generated:json-commands:begin -->
| Command | Class | JSON |
|---|---|---|
| `add-project` | write | `--json` |
| `index-more` | write | `--json` |
| `reindex-project` | write | `--json` |
| `remove-project` | write | `--json` |
| `sweep-orphans` | write (with --apply) | `--json` |
| `watch` | write | text only |
| `unwatch` | write | text only |
| `index-status` | read | `--json` |
| `list-projects` | read | `--json` |
| `lookup-project` | read | `--json` |
| `doctor` | read | `--json` |
| `overview` | read | `--json` |
| `skeleton` | read | `--json` |
| `map` | read | `--json` |
| `outline` | read | `--json` |
| `file-outline` | read | `--json` |
| `find-symbol` | read | `--json` |
| `find-definition` | read | `--json` |
| `find-references` | read | `--json` |
| `find-callers` | read | `--json` |
| `find-callees` | read | `--json` |
| `get-code-context` | read | `--json` |
| `deps` | read | text only |
| `changed-symbols` | read | `--json` |
| `semantic-search` | read | `--json` |
| `eval` | read | text only |
| `eval-compare` | read | text only |
| `plan-replace-body` | write | `--json` |
| `plan-insert-symbol` | write | `--json` |
| `plan-delete-declaration` | write | `--json` |
| `plan-validate` | read | `--json` |
| `plan-show` | read | `--json` |
| `plan-apply` | write | `--json` |
<!-- generated:json-commands:end -->

The commands without `--json` are text only because their answer is already a
file (`eval`, `eval-compare`) or a daemon's state, read through `index-status`
(`watch`, `unwatch`).

## Recipes

The envelope of a read answer is the fields the text renderer prints plus the
staleness block; the per-command shape is in
[docs/json-schema.md](../../docs/json-schema.md). One recipe per shape:

```bash
# a project summary: state and the two verdicts that decide a next move
code-indexer index-status --project P --json \
  | jq '{state, fingerprint, stale, stale_known, files, chunks}'

# registered projects and which of them need a rebuild
code-indexer list-projects --json \
  | jq '.projects[] | select(.fingerprint == "needs-reindex") | .path'

# one symbol's definitions: the five-field key, never the surrogate id
code-indexer find-definition Foo --json \
  | jq '.results[] | {file, qualified_name, symbol_type, decl_hash, range}'

# references, with the confidence each row actually carries
code-indexer find-references Foo --json \
  | jq '.references[] | {file, line, relationship, confidence}'

# a search answer: which projects answered, and whether the wall cut it short
code-indexer semantic-search "token refresh" --all-projects --json \
  | jq '{partial, skipped: [.skipped[]? | {project, reason}], hits: [.results[]? | {file, symbol}]}'

# a pass result: what the pass did, and why it failed if it did
code-indexer add-project /path/to/repo --json \
  | jq '{registered: .project, files, chunks, vectors, error: .ErrorType}'

# a validation verdict per check, without reading prose
code-indexer plan-validate sha256:<hex> --project P --json \
  | jq '{checks: [.checks[] | {name, ok, detail}]}'
```

A capped answer always says so: `match_count` is the uncapped population,
`truncated` is true when fewer rows were shown, and a walk reports
`omitted`. Treat `truncated: false` plus `stale_known: true` as the only
state in which an empty `results` is a real negative.
