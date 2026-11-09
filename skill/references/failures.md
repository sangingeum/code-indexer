# failures -- exit codes, error types and remedies

Governed by: ADR-0003 section 5 (the exit-code taxonomy and the `ErrorType`
rule); ADR-0006 section 3 (a provider refusal is a usage-class failure);
ADR-0005 (keeper's exit codes); ADR-0009 section 2 (this file's contents and
budget). Open this reference when a command failed and the remedy is not
obvious from its line.

Every `ErrorType` the code declares has a row here: this is the whole
taxonomy, not only the types one round added. `tests/test_docs.py` compares
the declarations in `errors.py` with these rows in both directions, so a type
with no row -- or a row naming a type that does not exist -- fails the docs
check.

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
| `ArgumentError` | a bad flag value -- an unknown value of an enumerated flag (`--mode`, `--ranking`, `--rerank`, `--relationship`, `--format`, `--visibility`, `--vectors`), refused at the boundary before anything is read, with the accepted values named in the line -- a `get-code-context` selector that names no range or two at once (a lone `--end-line`, an end below its start, a range past the file's last line, a `--symbol` beside a range), a malformed eval `--k` list (not integers, or not positive), an ambiguous name, `--exactly-one` against several definitions, `plan-apply --skip-stale-check`, `add-project` on an already registered path, `--json` together with a `--format` naming another format, `--format compact` on a command with no compact form, or two disagreeing spellings of one symbol | fix the invocation; for an ambiguous name the candidates are listed, and a refused format names the formats that command accepts |
| `UnsupportedOperation` | a working provider or the plan format does not implement the requested operation (`rename_symbol`) | do the work another way; nothing was edited |
| `AmbiguousNode` | two nodes in one file share name, kind and declaration hash | disambiguate by file and kind, or edit by hand |
| `PlanFormatError` | a malformed plan, a duplicated path, or an unknown member that changes meaning | regenerate the plan; reading never rewrites one |
| `PlanHashMismatch` | the document's bytes do not hash to the requested `plan_id` | re-reference the plan by its current hash, or regenerate it |
| `OverlappingEdits` | two edits in one file overlap | one edit per region |
| `AmbiguousOrder` | two insertions at one point | place one of them with `--position end` |
| `PathEscapesProject` | a plan path is empty, absolute, holds `..`, or resolves outside the project root | name a path relative to the project root |
| `CarriageReturnInPlan` | a plan or a replacement carries a carriage return; a plan speaks LF only | re-express the text with LF; the file's own convention is applied on write |
| `SubLineEditUnsupported` | the edited node does not begin and end on line boundaries, so the range is not line-granular | plan whole lines; a sub-line range is refused rather than expanded silently |

## Exit 3 -- something is not installed or not reachable

| `ErrorType` | Cause | Remedy |
|---|---|---|
| `BackendError` | Ollama or Qdrant unreachable, or the embed model is absent | start or install the service; `doctor` names which leg failed |
| `ProviderUnavailable` | no refactoring provider is installed or selected for the language, or one that is there never became ready | install one, or use the CLI's syntax-only commands |
| `ProviderTimeout` | the provider did not answer a request, or did not become ready, inside its deadline; the request was cancelled | the request was cancelled and nothing was answered -- ask again once the server is healthy; a retired provider is rebuilt for the next request |
| `KeeperUnavailable` | keeper is not on `PATH` | install keeper; there is no fallback apply path |
| `KeeperCapabilityMissing` | the installed keeper lacks `--expected-hash`, `--expected-content` or `--dry-run` | upgrade keeper; the missing flag is named |
| `ConfigError` | a language grammar or extension the request needs is not installed (the extension-unavailable class, which renders as `ConfigError` and exits 3) | install the extra that provides it |
| `SemanticProviderUnavailable` | a plan asked for a semantic promise no provider in phase 1 can keep | plan the structural operation, or wait for the phase that records a provider |

A provider that times out is exit 3 and a cancelled request -- never an empty
semantic answer.

## Exit 4 -- the index must be rebuilt

| `ErrorType` | Cause | Remedy |
|---|---|---|
| `RebuildRequiredError` | the index was built by different extraction code, a different embed model or dimension, or a different embed-text format, or its manifest is at an older schema; or `reindex-project --repair-points` was asked to re-address rows that are not current, a collection that is gone, or an index that records no embedding dimension and holds no vector to read one from | `code-indexer reindex-project <path>`; report to the owner rather than running it unasked |
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

A pass command reports the same class and the same exit code: `add-project`,
`index-more` and `reindex-project` rebuild the failure with the class that
raised it, so a refused pass exits 4 with one `RebuildRequiredError:` line and
a script can tell it from an internal fault (exit 1).

## Exit 5 -- a lock or a concurrency conflict

| `ErrorType` | Cause | Remedy |
|---|---|---|
| `LockedError` | another pass holds the per-project flock (`--refresh`, a pass command or the watcher) | wait for the holder; re-running immediately is the same outcome |
| `PlanStale` | a file's hash differs from `files[].expected_hash` | re-read the file, regenerate the plan |
| `PartialApply` | a mid-loop keeper failure after some edits landed | undo the operation ids in reverse with `keeper history undo` |
| `LocatorMismatch` | the plan's locator no longer names a node in the file as the plan saw it -- the file moved under the plan | re-read the file and regenerate the plan; the edit is refused rather than re-targeted |

## Exit 1 -- everything else

| `ErrorType` | Cause | Remedy |
|---|---|---|
| `InternalError` | a runtime failure with no more specific class | read the message; `doctor` if it repeats |
| `ConfigError` | an invalid or missing configuration value | set the variable or flag the message names |
| `NotFoundError` | a path, a plan file, an indexed definition that is not there, or a file the index does not hold (`outline` on a file the index skipped or never read; `get-code-context` on a file that does not exist is this class too, not a usage error) | check the path; `list-projects` says which projects exist, and `index-status` says what a project's index covers |
| `NotUtf8` | a file the plan touches does not decode as UTF-8 | there is no lossy decode: fix the file's encoding and regenerate |
| `NewlineMismatch` | the file's observed terminator differs from the plan's declaration | the file changed under the plan -- regenerate it |
| `MixedNewlines` | a file mixes terminators, and the convention is refused rather than guessed | normalize the file to one terminator and regenerate |
| `ProviderRefusal` | the provider refused for its own reason; no fallback is attempted | read the provider's reason; nothing was edited |
| `ProviderFailure` | the provider returned no answer, or an answer outside the contract | a result with no provenance is a defect -- report it; nothing was edited |
| `PositionConversionFailed` | a provider position cannot be placed exactly in the file's bytes -- inside a surrogate pair, or outside the file | the position is refused, never rounded: re-read the file and ask again |
| `NonLineAlignedProviderEdit` | a provider edit crosses a line terminator | phase 1 refuses it rather than approximating; ask for the edit inside one line |
| `KeeperFailure` | keeper failed when nothing had been written | the report names the keeper code |

## Two reads of one query that disagree

No error type: both reads succeed, and their answers differ. The drift
belongs to the model load, not to the text: a model that has just been loaded
answers the FIRST call for a text with one vector and its IMMEDIATE repeat
with another. One run of
`uv run python scripts/probe_embed_drift.py --form both --trials 6` -- which
unloads the model before every trial, so every trial crosses a load boundary
-- saw that pair differ in all 6 load-boundary trials of each form: 1.592e-03
per component for the bare form and 3.990e-03 for the instructed one. The text
is the probe's default (`--text` omitted); a different text measures a
different size. The same
command with `--reload once`, where only the first trial of each form crosses
a load, saw every warm immediate repeat return the SAME vector -- delta
0.000e+00 in all 10 warm trials of that run (5 bare, 5 instructed). Those are
one run's observations, not bounds: the size and the shape depend on the text,
on the form, on the model and on the run.

So the answer depended on whether a read happened to be the first call after a
load, which a fresh CLI process can be. What a later request does to that form
is a per-form observation, not a guarantee: in that same run the bare form's
call after a different request returned the load-first digest in all 6
load-boundary trials, while the instructed form returned a third, distinct
digest in all 12 of its trials of both modes.

The measurement of record is that command: it touches no cache, prints per
trial whether the trial crossed a load boundary, the immediate repeat's
largest per-component delta, the digests and what the third call returned,
then one summary per reload mode and per form. Run it on the host in question.

A read
fixes the bytes instead of the service: the first read stores the query vector
in the embedding cache (float16 form, written on a miss and on a failed
dimension probe alike) and every later read searches those bytes, so a second
read of the same text is identical while the row survives.

The residual is stated, not hidden: a text keeps the form of its FIRST
embedding until the row is evicted or the model, embed format or dimension
changes; with `EMBED_CACHE=0` there is nowhere to store the bytes, so at a deep
`--limit` a second read can report a different member set (one run's
measurement with the cache disabled: 2 members in and 2 out of a 400-hit
answer); and two machines with separate
caches can hold different forms of the same text. Remedy: leave the cache on (`EMBED_CACHE` unset), and
treat a cache-disabled deep answer as one sample, not as a fixed list.

## Diagnosing in order

1. Read the `ErrorType` first; the number is the coarse branch.
2. `code-indexer doctor` for anything about services, `INDEX_ROOT` or disk.
3. `code-indexer index-status --project <path>` for anything about the index
   itself -- `state=`, `rows=`, `vectors=`, and the fingerprint line.
4. `code-indexer list-projects` when the question is which project is
   affected.

Remedies are the owner's call where a rebuild costs minutes: report
`needs-reindex` instead of rebuilding unasked.
