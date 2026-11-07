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
| Command | Class | Reference | Reach for it when |
|---|---|---|---|
| `add-project` | write | `indexing.md` | Register a project directory for semantic indexing ... |
| `index-more` | write | `indexing.md` | Extend a project's indexed scope with another ... |
| `reindex-project` | write | `indexing.md` | Force a full rebuild of a project's index ... |
| `remove-project` | write | `indexing.md` | Deregister a project and DELETE its collection + ... |
| `sweep-orphans` | write (with --apply) | `indexing.md` | Remove residue rows for files that are gone from ... |
| `watch` | write | `indexing.md` | Optional long-lived watcher: inotify-based re-index ... |
| `unwatch` | write | `indexing.md` | Stop the watcher started by `watch` — the reverse ... |
| `index-status` | read | `indexing.md` | Report indexing state ... |
| `list-projects` | read | `indexing.md` | List registered projects with per-project index ... |
| `lookup-project` | read | `indexing.md` | Check whether a project is registered ... |
| `doctor` | read | `indexing.md` | Health-check the installation: runtime versions, ... |
| `overview` | read | `query.md` | Project-level map from manifest data: languages, ... |
| `skeleton` | read | `query.md` | Whole-project or per-subtree structural map from ... |
| `map` | read | `query.md` | Whole-project or per-subtree structural map from ... |
| `outline` | read | `query.md` | One file: declarations, signatures, one-line ... |
| `file-outline` | read | `query.md` | One file: declarations, signatures, one-line ... |
| `find-symbol` | read | `query.md` | Look up symbols by name in the manifest symbol ... |
| `find-definition` | read | `query.md` | Find where a symbol is declared ... |
| `find-references` | read | `query.md` | Find references TO a symbol in the single reference ... |
| `find-callers` | read | `query.md` | Who references this symbol: fan-in BFS over the ... |
| `find-callees` | read | `query.md` | What this symbol references: fan-out BFS over the ... |
| `get-code-context` | read | `query.md` | Retrieve ONLY the relevant source lines instead of ... |
| `deps` | read | `query.md` | Import graph around one file (resolved edges only) |
| `changed-symbols` | read | `query.md` | What changed in symbols: parse NEW/OLD contents ... |
| `semantic-search` | read | `semantic.md` | Semantic code search over the index as of ... |
| `eval` | read | `semantic.md` | Run a labeled query set against a project's index ... |
| `eval-compare` | read | `semantic.md` | Compare two eval reports: metric delta table + ... |
| `plan-replace-body` | write | `plans.md` | Prepare a plan that replaces one declaration's body |
| `plan-insert-symbol` | write | `plans.md` | Prepare a plan that inserts a block at a ... |
| `plan-delete-declaration` | write | `plans.md` | Prepare a plan that deletes one declaration's whole ... |
| `plan-validate` | read | `plans.md` | Report each structural check of a plan |
| `plan-show` | read | `plans.md` | Print a plan, and with --diff the change it would ... |
| `plan-apply` | write | `plans.md` | Apply a plan through filekeeper and report what ... |
<!-- generated:roster:end -->

## Flags, and one argument shape for the symbol reads

| Flag | Where | Meaning |
|---|---|---|
| `--version` | top level, no command | the installed package version, one line, exit 0 |
| `--format text\|json` | every read command that takes `--json` | `--json` is the exact alias of `--format json`; the pair is accepted when it agrees (`--json --format json`) and is exit 2 when the two name different formats |
| `--format compact` | `overview`, `index-status`, `skeleton`, `outline`, `find-symbol`, `find-definition`, `semantic-search` | one line per record built from normalized field names (`loc`, `directory`, `share`, `hotspot-fan-in`), not the text form's own labels; refused elsewhere (exit 2) naming text\|json |

`get-code-context`, `find-symbol` and `find-definition` take the symbol as
`--symbol <name>` beside `--project`; the positional NAME is its alias.
`get-code-context --symbol <name>` with no FILE resolves the name project-wide
and refuses an ambiguous one with every candidate (exit 2); a FILE keeps its
meaning, scoping `--symbol` to a range inside it. `deps --format` is unchanged.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success, including zero hits -- "no results" is an answer |
| 1 | any other runtime failure |
| 2 | usage or argument error |
| 3 | backend or extension unavailable: service unreachable, extension or language server not installed |
| 4 | the index requires a rebuild: fingerprint mismatch, vectors absent, needs-reindex |
| 5 | lock or concurrency conflict -- the meaning filekeeper gives 5 too |

`ErrorType: message` on stderr is the semantic discriminator; the number is
the coarse branch, and a failed command prints no answer on stdout.

## Staleness, in three sentences

Every read answer describes the index as of `meta.last_indexed`, never the
working tree, and reports `fresh`, `stale` or `unknown` -- `unknown` is not
`fresh`. `--refresh` is the only repair a read offers, one pass then the
answer; otherwise repair is a pass command or the watcher. Stale prints this:

```
Warning: index is stale: 2 modified files -- <path>; answers describe the index, not the tree; refresh with code-indexer reindex-project <path>
```

## The read-only guarantee

A read command never writes the manifest, the vector store or the sources.

## The references

| Reference | Open it when |
|---|---|
| [indexing.md](skill/references/indexing.md) | the task is to build or repair an index, or to ask what state one is in |
| [query.md](skill/references/query.md) | a query answered the wrong shape and its flags need reading |
| [semantic.md](skill/references/semantic.md) | a question is asked in words rather than as a name, or a ranked answer's order needs reading |
| [plans.md](skill/references/plans.md) | the task is to change code through a plan |
| [machine-output.md](skill/references/machine-output.md) | an agent parses output instead of reading prose |
| [failures.md](skill/references/failures.md) | a command failed and the remedy is not obvious from its line |
