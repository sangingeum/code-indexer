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

## The staleness block

Every read answer that comes from the index carries these fields in JSON:

| field | meaning |
|---|---|
| `stale` | boolean, unchanged meaning; on `get-code-context --json` it keeps its file-level meaning |
| `stale_known` | boolean; false means the verdict is `unknown`, so `stale: false` alone never means "checked and fresh" |
| `stale_reasons` | `[{kind: modified\|added\|deleted, file}]`, capped at five |
| `stale_reason_count` | total count before the cap |
| `index_revision` | the revision the answer was read from |
| `last_indexed` | timestamp of the pass that wrote the index, ISO-8601 UTC |

Text mode prints exactly one stderr line and nothing extra on stdout:

```
Warning: index is stale (3 files changed since 2026-11-06T22:10:00Z: 2 modified, 1 deleted); answers describe the index, not the tree. Refresh: code-indexer reindex-project <path>
```

A fresh answer prints nothing extra; an `unknown` answer prints one line
saying the check did not run.

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
