# failures -- exit codes, error types and remedies

Governed by: ADR-0003 section 5 (the exit-code taxonomy and the `ErrorType`
rule); ADR-0006 section 3 (a provider refusal is a usage-class failure);
ADR-0005 (keeper's exit codes); ADR-0009 section 2 (this file's contents and
budget). Open this reference when a command failed and the remedy is not
obvious from its line.

## The taxonomy

| Code | Meaning |
|---|---|
| 0 | success, including zero hits -- "no results" is an answer |
| 1 | any other runtime failure |
| 2 | usage or argument error |
| 3 | backend or extension unavailable: service unreachable, extension not installed, no language server installed for the requested operation |
| 4 | the index requires a rebuild: fingerprint mismatch, vectors absent, needs-reindex |
| 5 | lock or concurrency conflict -- the same meaning filekeeper gives code 5 |

Two situations that look alike get different numbers, because the caller's
next move differs. **Nothing installed or reachable** -- no service, no
extension, no language server -- is 3: the remedy is to install or start
something. **A working provider answering that it does not implement the
operation** is 2: the request asked the tool for something it does not offer.

A failed command prints `ErrorType: message` on stderr and leaves stdout
empty; a successful empty answer prints `no results` on stdout and exits 0.
No failure prints a traceback.

## Exit 0 -- an answer, including "no results"

Zero hits is an answer: the command prints `no results` on stdout and exits
0. Do not read an empty answer as a failure, and do not read a failure as an
empty answer -- a failure leaves stdout empty and prints its `ErrorType` on
stderr.

## Exit 2 -- the request was wrong

| `ErrorType` | Cause | Remedy |
|---|---|---|
| `ArgumentError` | a bad flag value, an ambiguous name, `--exactly-one` against several definitions, `plan-apply --skip-stale-check`, or `add-project` on an already registered path | fix the invocation; for an ambiguous name the candidates are listed |
| `UnsupportedOperation` | a working provider or the plan format does not implement the requested operation (`rename_symbol`) | do the work another way; nothing was edited |
| `AmbiguousNode` | two nodes in one file share name, kind and declaration hash | disambiguate by file and kind, or edit by hand |
| `PlanFormatError` | a malformed plan, a duplicated path, or an unknown member that changes meaning | regenerate the plan; reading never rewrites one |
| `PlanHashMismatch` | the document's bytes do not hash to the requested `plan_id` | re-reference the plan by its current hash, or regenerate it |
| `OverlappingEdits` | two edits in one file overlap | one edit per region |
| `AmbiguousOrder` | two insertions at one point | place one of them with `--position end` |

## Exit 3 -- something is not installed or not reachable

| `ErrorType` | Cause | Remedy |
|---|---|---|
| `BackendError` | Ollama or Qdrant unreachable, or the embed model is absent | start or install the service; `doctor` names which leg failed |
| `ProviderUnavailable` | no refactoring provider installed, or it never became ready | install one, or use the CLI's syntax-only commands |
| `KeeperUnavailable` | keeper is not on `PATH` | install keeper; there is no fallback apply path |
| `KeeperCapabilityMissing` | the installed keeper lacks `--expected-hash`, `--expected-content` or `--dry-run` | upgrade keeper; the missing flag is named |
| `ExtensionUnavailable` | a language grammar or extension the request needs is not installed | install the extra that provides it |

A provider that times out is exit 3 and a cancelled request -- never an empty
semantic answer.

## Exit 4 -- the index must be rebuilt

| `ErrorType` | Cause | Remedy |
|---|---|---|
| `RebuildRequiredError` | the index was built by different extraction code, a different embed model or dimension, or a different embed-text format, or its manifest is at an older schema | `code-indexer reindex-project <path>`; report to the owner rather than running it unasked |
| `ConfigError` | `semantic-search` on a `vectors=off` project | `reindex-project <path> --vectors on`, or `--mode lexical` if text is enough |

Every surface that answers from stored rows refuses this way -- `find-symbol`,
`find-definition`, `outline`, `skeleton`, `overview`, `get-code-context`,
`find-references`, `find-callers`, `find-callees`, `deps` and
`changed-symbols` -- with exit 4, empty stdout and one stderr line. The four
that report on the index itself stay answerable: `list-projects`,
`lookup-project`, `index-status` and `doctor`. `--refresh` on a refusing
command runs the pass first, so one refresh repairs the project and the
command then answers.

A refusal is not a staleness warning: a warning above an answer still reads
as a fact about the code, so the tool refuses instead.

## Exit 5 -- a lock or a concurrency conflict

| `ErrorType` | Cause | Remedy |
|---|---|---|
| `LockedError` | another pass holds the per-project flock (`--refresh`, a pass command or the watcher) | wait for the holder; re-running immediately is the same outcome |
| `PlanStale` | a file's hash differs from `files[].expected_hash` | re-read the file, regenerate the plan |
| `PartialApply` | a mid-loop keeper failure after some edits landed | undo the operation ids in reverse with `keeper history undo` |
| `KeeperFailure` | keeper refused an edit and nothing was written | the keeper code and message are in the report |

## Exit 1 -- everything else

| `ErrorType` | Cause | Remedy |
|---|---|---|
| `IndexerError` | a runtime failure with no more specific class | read the message; `doctor` if it repeats |
| `ConfigError` | an invalid or missing configuration value | set the variable or flag the message names |
| `KeeperFailure` | keeper failed when nothing had been written | the report names the keeper code |

## Diagnosing in order

1. Read the `ErrorType` first; the number is the coarse branch.
2. `code-indexer doctor` for anything about services, `INDEX_ROOT` or disk.
3. `code-indexer index-status --project <path>` for anything about the index
   itself -- `state=`, `rows=`, `vectors=`, and the fingerprint line.
4. `code-indexer list-projects` when the question is which project is
   affected.

Remedies are the owner's call where a rebuild costs minutes: report
`needs-reindex` instead of rebuilding unasked.
