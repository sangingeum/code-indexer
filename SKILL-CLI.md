# code-indexer -- the agent entry document

One CLI over one index. This page is the whole surface an agent loads before
it acts: which command answers which question, which commands write, what an
exit code means, and which reference to open next. Flags, examples and
remedies live in the references, one read away.

## Decision tree

| Your question | Command |
|---|---|
| What is this project made of? | `overview`, then `skeleton` |
| Where is a symbol declared? | `find-definition <name>` |
| Who references a symbol? | `find-references <name>` |
| What does a symbol call, or who calls it? | `find-callees <name>` / `find-callers <name>` |
| What does this file import, and who imports it? | `deps <file>` |
| What is declared in this file? | `outline <file>` |
| Show the source of a symbol or a line range | `get-code-context <file>` |
| Find code by meaning | `semantic-search "<query>"` |
| Find symbols by shape (kind, container, language, visibility) | `find-symbol` |
| What changed in the tree or between two revisions? | `changed-symbols` |
| Is the index current, and in what state? | `index-status`, `list-projects` |
| Register, extend, rebuild or drop an index | `add-project`, `index-more`, `reindex-project`, `remove-project` |
| Keep the index following the tree | `watch` / `unwatch` |
| Change code through a reviewed plan | `plan-replace-body`, `plan-insert-symbol`, `plan-delete-declaration`, then `plan-validate`, `plan-show`, `plan-apply` |
| Is the installation healthy, or how good is retrieval? | `doctor`, `eval`, `eval-compare` |

## The command roster

<!-- generated:roster:begin -->
**indexing.md** -- [flags and examples](skill/references/indexing.md)

| Command | Class | Reach for it when |
|---|---|---|
| `add-project` | write | Register a project directory for semantic indexing ... |
| `index-more` | write | Extend a project's indexed scope with another ... |
| `reindex-project` | write | Force a full rebuild of a project's index ... |
| `remove-project` | write | Deregister a project and DELETE its collection + ... |
| `sweep-orphans` | write (with --apply) | Remove residue rows for files that are gone from ... |
| `watch` | write | Optional long-lived watcher: inotify-based re-index ... |
| `unwatch` | write | Stop the watcher started by `watch` — the reverse ... |
| `index-status` | read | Report indexing state ... |
| `list-projects` | read | List registered projects with per-project index ... |
| `lookup-project` | read | Check whether a project is registered ... |
| `doctor` | read | Health-check the installation: runtime versions, ... |

**query.md** -- [flags and examples](skill/references/query.md)

| Command | Class | Reach for it when |
|---|---|---|
| `overview` | read | Project-level map from manifest data: languages, ... |
| `skeleton` | read | Whole-project or per-subtree structural map from ... |
| `map` | read | Whole-project or per-subtree structural map from ... |
| `outline` | read | One file: declarations, signatures, one-line ... |
| `file-outline` | read | One file: declarations, signatures, one-line ... |
| `find-symbol` | read | Look up symbols by name in the manifest symbol ... |
| `find-definition` | read | Find where a symbol is declared ... |
| `find-references` | read | Find references TO a symbol in the single reference ... |
| `find-callers` | read | Who references this symbol: fan-in BFS over the ... |
| `find-callees` | read | What this symbol references: fan-out BFS over the ... |
| `get-code-context` | read | Retrieve ONLY the relevant source lines instead of ... |
| `deps` | read | Import graph around one file (resolved edges only) |
| `changed-symbols` | read | What changed in symbols: parse NEW/OLD contents ... |
| `semantic-search` | read | Semantic code search over the index as of ... |
| `eval` | read | Run a labeled query set against a project's index ... |
| `eval-compare` | read | Compare two eval reports: metric delta table + ... |

**plans.md** -- [flags and examples](skill/references/plans.md)

| Command | Class | Reach for it when |
|---|---|---|
| `plan-replace-body` | write | Prepare a plan that replaces one declaration's body |
| `plan-insert-symbol` | write | Prepare a plan that inserts a block at a ... |
| `plan-delete-declaration` | write | Prepare a plan that deletes one declaration's whole ... |
| `plan-validate` | read | Report each structural check of a plan |
| `plan-show` | read | Print a plan, and with --diff the change it would ... |
| `plan-apply` | write | Apply a plan through filekeeper and report what ... |
<!-- generated:roster:end -->

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success, including zero hits -- "no results" is an answer |
| 1 | any other runtime failure |
| 2 | usage or argument error |
| 3 | backend or extension unavailable: service unreachable, extension or language server not installed |
| 4 | the index requires a rebuild: fingerprint mismatch, vectors absent, needs-reindex |
| 5 | lock or concurrency conflict -- the meaning filekeeper gives 5 too |

`ErrorType: message` on a stderr line is the semantic discriminator; the
number is the coarse branch. `0` with an empty answer is not a failure, and
a failed command prints no answer on stdout.

## Staleness, in three sentences

Every read answer describes the index as of `meta.last_indexed`, never the
working tree, and reports `fresh`, `stale` (with reasons) or `unknown` -- and
`unknown` is not `fresh`. `--refresh` is the only repair a read offers: it
takes the project's flock and runs one incremental pass before answering.
Otherwise repair is explicit -- a pass command or the watcher.

A file the sensitive layer withholds is not a staleness reason: it is reported
as `1 file skipped (sensitive content)` (JSON: `skipped_count`) on a fresh verdict.

## The read-only guarantee

A read command never writes the manifest, the vector store or the sources: it
opens no write transaction, runs no index pass, and contacts no service to
reach a staleness verdict.

## The references

| Reference | Open it when |
|---|---|
| [indexing.md](skill/references/indexing.md) | the task is to build or repair an index, or to ask what state one is in |
| [query.md](skill/references/query.md) | a query answered the wrong shape and its flags need reading |
| [plans.md](skill/references/plans.md) | the task is to change code through a plan |
| [machine-output.md](skill/references/machine-output.md) | an agent parses output instead of reading prose |
| [failures.md](skill/references/failures.md) | a command failed and the remedy is not obvious from its line |
