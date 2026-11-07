# Round 5 folds — the failure taxonomy, the plan's provider member, a retired session

Base `2f0a833`, tip `0ec69e1`, five commits, tree `<project-root>`. The four folds
are the round-5 two-axis review's ruling, landed as it ruled. No schema version,
`EXTRACTION_VERSION`, index or command changed; behaviour moved only where a
finding named it.

| Commit | Fold | Subject |
|---|---|---|
| `70ba5ba` | A | docs(skill): the failure reference names every declared error type |
| `968d6c7` | B | feat(provider): the plan's provider member gets a writer |
| `63a930f` | C | fix(provider): a timed-out session is retired, not left poisoned |
| `07bc581` | D | chore(docs): drift-check diagnostic, changelog dash, roster truncation, dead asserts |
| `0ec69e1` | C | test(provider): the poisoning proof fails on request 2, not on the wording |

Every commit is signed and `%G? = G`. The tracked tree was clean at the base.

## FOLD-A — the failure reference, and the check that could not see it

**What was wrong.** `skill/references/failures.md` named 17 of the 32
`ErrorType`s `errors.py` declares. Five of this round's seven were missing, so an
agent meeting `PositionConversionFailed` on stderr had an exit code and a type it
could not look up. The check could not notice: it read `^#+ .*\bN\b` headings,
which can only ever see codes.

**What landed.** All 15 missing types have rows — `ProviderTimeout` on exit 3;
`ProviderRefusal`, `ProviderFailure`, `PositionConversionFailed` and
`NonLineAlignedProviderEdit` on exit 1, and the other ten with them. I chose the
complete-taxonomy reading of ADR-0009 section 2 ("every exit code with its
`ErrorTypes`") over a deliberately partial reachable-failures table, and the
reference now says so in one sentence in its header rather than leaving a reader
to infer the scope.

Three rows that named a class rather than the string a caller sees were
re-labelled in the same edit, because the new assertion reads `ErrorType`s and
the old rows would have failed it falsely:

- `ExtensionUnavailable` becomes `ConfigError`, with the extension-unavailable
  class named in the cause: that class renders `ConfigError` and exits 3.
- `IndexerError` becomes `InternalError`, which is the string the base class
  actually renders.
- a `KeeperFailure` row under exit 5 is dropped, because `KeeperFailureError`
  exits 1 and its exit-1 row already exists. Exit 5 gains `LocatorMismatch`,
  which is what that section was missing.

That re-labelling is the one judgement I added: the fold asked for the five rows
and "check the rest of the declared set", and checking the rest meant fixing
three rows that would have been wrong in a table which now claims completeness.

**The check.** A fourth assertion in check 4 collects `error_type = "..."` from
`errors.py` and the first-column `ErrorType` cells of `failures.md`, and compares
them in both directions: a declared type with no row fails, and so does a row
naming a type no code declares. The column heading (`ErrorType`) is excluded by
name. A guard asserts the reader found more than twenty declarations, so a
broken pattern cannot pass vacuously.

**Proof it can fail, both ways.** A worktree at the base, carrying only this
tip's `tests/test_docs.py`:

```
E       assert not ['CarriageReturnInPlan', 'InternalError', 'LocatorMismatch', 'MixedNewlines', 'NewlineMismatch', 'NonLineAlignedProviderEdit', ...]
FAILED tests/test_docs.py::test_failures_reference_names_every_declared_error_type
1 failed, 35 deselected in 0.07s
```

A worktree at `70ba5ba` with the `ProviderFailure` row deleted from its copy of
the reference:

```
E       AssertionError: failures.md has no row for error types the code declares: ['ProviderFailure']; a caller that meets one cannot look it up
FAILED tests/test_docs.py::test_failures_reference_names_every_declared_error_type
1 failed, 35 deselected in 0.07s
```

**Measured.** `failures.md` 108 lines / 6,404 bytes at the base, 127 lines /
9,178 bytes at the tip, against the 400-line / 24 KiB budget. The entry document
is 117 lines / 6,447 bytes (was 118 / 6,506), inside 120 lines / 8 KiB.

## FOLD-B — the plan's `provider` member gets a writer

**What was wrong.** ADR-0006 sections 4 and 5 both require a plan built from
provider edits to carry the provider object the answers carry. Nothing wrote it:
`materialize(tf, edits)` took bare `ProviderEdit` values, so the `RenameEdits`
identity was unwrapped one call early and dropped at the seam, and
`build_document` stamped `tree-sitter-structural` over anything a caller
attached.

**What landed.**

- `materialize(tf, rename: RenameEdits)` returns the candidate plan body,
  `{"provider": {...}, "edits": [...]}`, and refuses anything that is not the
  `RenameEdits` a provider answered.
- `plan_provider_member(identity)` builds the member: `name`, `version` and
  `transport` from the provider's own identity, plus `conversion_version` from
  `provider_positions.CONVERSION_VERSION`, which is the one module ADR-0006
  section 5 makes the conversion's owner.
- `build_document(..., provider=None)` honours a supplied member and requires
  its three identity keys; with no member it falls back to
  `structural_provider()`, so every syntax-only plan is byte-identical to
  before.

**The shape disagreement, recorded rather than smoothed.** ADR-0004 section 1
says the member is `{name, version}`; ADR-0006 sections 4 and 5 say
`{name, version, transport}` and add the conversion's version. I implemented the
three-key object plus `conversion_version`, which is what section 4 states and
what this writer can honestly produce, and left ADR-0004 unedited: reconciling
two ratified records is the owner's call and a card exists for it. The
discrepancy is written down here so it is not carried by silence.

**Docs.** `docs/json-schema.md` described only the structural shape; it now
shows `provider` as `{name, version}` for a syntax-only plan, states the
provider-derived shape beside it, and says the reader requires a `provider`
object rather than a fixed key set. No schema version changed, and the reader
was neither loosened nor tightened.

**Tests.** Three new cases in `tests/test_provider_materialize.py`: the body
carries the producing provider and the conversion version; the sealed document
carries the member it was given rather than the structural constant; and a
syntax-only plan still names the structural producer. The contract suite's
materialize case now takes the `RenameEdits` and asserts the provider name
reaches the body.

```
E       TypeError: 'RenameEdits' object is not iterable
E       AttributeError: module 'code_indexer.plans' has no attribute 'structural_provider'
FAILED tests/test_provider_materialize.py::test_a_materialized_body_carries_the_provider_that_produced_it
FAILED tests/test_provider_materialize.py::test_the_document_honours_the_provider_member_it_is_given
FAILED tests/test_provider_materialize.py::test_a_syntax_only_plan_still_names_the_structural_provider
3 failed
```

That is the base worktree running this tip's tests: neither the signature nor
the seam existed there, which is the finding.

## FOLD-C — a timed-out session is retired instead of poisoning the invocation

**What was wrong.** After one request expired, the single worker was still
inside that call. `future.cancel()` cannot stop a running call, so request 2
queued behind it and reported `ProviderTimeout` at the 0.05 s deadline against a
provider that would have answered at once — a wrong answer about a healthy
provider, which is the class ADR-0006 section 7 exists to prevent.

**What landed.** `_run` retires the provider on expiry: `_retire` drops the
instance, the cached capabilities and the worker (shutting the executor down
without waiting), closes the provider, and records `retired_reason`. The next
request builds a fresh provider through the same factory. The timeout message
says the session was retired, so the caller is not left to guess why the next
request starts a second provider.

I chose restart over "mark dead and refuse" because refusing later requests
would leave the rest of an invocation unusable after one slow answer; the review
offered both as acceptable.

**Test.** The review's own probe: `OnceSlowProvider`, slow on the first semantic
call of any instance and instant afterwards, with the slow counter shared across
instances so a rebuilt provider counts as recovered. Request 1 is a timeout;
request 2 must answer. At the base, run with this tip's test file:

```
E           code_indexer.errors.ProviderTimeoutError: the <provider> provider did not answer 'resolve_symbol' within 0.05s: the request was cancelled ($/cancelRequest) ...
FAILED tests/test_provider_contract.py::test_a_timed_out_session_does_not_poison_the_next_request
1 failed, 20 deselected in 0.21s
```

That failure is request 2 itself, at `tests/test_provider_contract.py:164` — the
finding, not a message mismatch. At the tip the same selection passes, and the
test additionally asserts that a second provider instance was built and that the
first one was closed.

## FOLD-D — four pieces of polish

- **The drift-check diagnostic that raised instead of asserting.**
  `tests/test_docs.py` built its detail with `path.relative_to(REPO)`, which
  raises `ValueError` for a path outside the checkout, so a rig pointed at a
  copy elsewhere reported a crash rather than the drift it was asked about. A
  helper (`_under_checkout`) asserts first and a test holds it. Proved by
  restoring the old body in a worktree at the tip:

  ```
  E               ValueError: '<copy>/elsewhere/SKILL-CLI.md' is not in the subpath of '<copy>'
  FAILED tests/test_docs.py::test_a_drift_path_outside_the_checkout_is_an_assertion_not_a_crash
  1 failed, 36 deselected in 0.08s
  ```

- **The changelog dash.** `## [Unreleased] -- the agent surface` now uses the
  em dash the other eleven `## [Unreleased]` headings use.
- **The roster cut mid-parenthesis.** The generator's `_one_line` cut at 70
  characters at a word boundary and could leave a bracket open:
  `Force a full rebuild of a project's index (runs ...` at the base,
  `Force a full rebuild of a project's index ...` at the tip. `_bounded` now
  drops a bracket it cannot close, along with the words inside it.
  `SKILL-CLI.md` was regenerated by the generator, not hand-edited, and
  `--check` exits 0.
- **Two dead asserts.** The `assert`s in `_native_query` restated what
  `SymbolQuery.__post_init__` already refuses and would vanish under `-O`; they
  are gone.

## Bars at the tip

```
uv run pytest tests/ -q -W error
890 passed, 1 skipped, 2 deselected in 107.93s
```

884 at the base, plus six new cases: one taxonomy assertion, three materialize
cases, one session case and one diagnostic case.

```
uv run pytest tests/test_docs.py -q                        36 passed, 1 skipped
uv run python scripts/generate_skill_docs.py --check      exit 0
uvx ruff check src tests scripts                          All checks passed!
uv run python scripts/publishing_hygiene_lint.py
publishing hygiene: scanned=798 skipped=0 violations=0 (untracked files are out of scope)
```

The installed-copy check still skips with the legacy single-file reason, as the
brief predicted.

## What this record does not claim

- **No rename is applied.** Schema 1 still refuses `rename_symbol`; the
  materialize path produces and validates a body that no command can seal.
- **No language server was run.** Every session claim is against the in-process
  stub, and the fence forbids an install.
- **The ADR-0004 / ADR-0006 disagreement is recorded, not resolved.** Only the
  owner can reconcile two ratified records.
- **The folds were landed as ruled.** I disagree with none of them; the one
  judgement I added is FOLD-A's choice of a complete taxonomy over a reachable
  subset, and the three re-labelled rows that choice required.

## Captures

No capture files: every proof above is a pytest selection quoted verbatim, from
worktrees under a scratch directory that were removed afterwards. Nothing
committed here carries a host path, an index root, a board id or an internal
profile name.
