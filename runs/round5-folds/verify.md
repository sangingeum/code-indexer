# Narrow re-verify of the four folds -- the failure reference, the plan's provider member, a retired session

Base `2f0a833`, tip `45ede9f`, tree `<project-root>`. This is a narrow repair
verification of the four folds, not a re-run of the round-5 verification gate's
sixteen bars. Every bar below was driven here, in this session, against the tip
in the checkout unless a copy is named. Captures were made in a scratch tree
outside the repo; host paths, index roots, board ids and internal profile names
are elided from everything quoted.

## Verdict

**PASS.** All four folds hold as the fold record declares them, and every
standing bar that was asked for is green at the tip. One new low finding: the
taxonomy assertion the failure fold added cannot see a declared `ErrorType`
whose name carries a digit, so the guard has a blind spot even though the table
it guards is complete today. One earlier owner call is restated rather than
filed, because the code and the reference genuinely disagree there and the
decision is already the owner's.

| Bar | Result |
|---|---|
| FOLD-A -- the failure reference names every declared type | PASS (one low finding) |
| FOLD-B -- the plan's provider member has a writer on both paths | PASS |
| FOLD-C -- a timed-out session is retired, the next request answers | PASS |
| FOLD-D -- drift helper, changelog dash, roster bracket, dead asserts | PASS |
| Standing bars (suite, docs, generator, budgets, ruff, hygiene, scope, signatures, remote, CI) | PASS |

## FOLD-A -- the failure reference, compared in both directions

I recomputed the set difference myself with a reader of my own (identifier
characters, so names with digits are visible), against the same two files:

```
python3 <scratch>/taxonomy_probe.py <checkout>
declared (wide, raw): 33 unique: 32
declared (test pattern, raw): 32 unique: 31
wide minus test-pattern: ['NotUtf8']
rows (wide): 39 unique excluding heading: 32
missing rows via wide reader: []
phantom rows via wide reader: []
```

The 33 raw declarations are 32 distinct strings (`InternalError` is declared
both on the base class and again on its own class), and the reference carries
32 rows plus the `ErrorType` column heading. **32 of 32 in both directions, no
missing row, no inventing row.** At the base the same comparison gives 17 of
32; the fifteen missing types are the ones the fold record lists, and the two
base rows that named a class rather than a rendered string were
`ExtensionUnavailable` and `IndexerError` -- exactly the two relabelled, so the
relabelling was required rather than cosmetic.

### The three relabels, checked against what the code renders

I walked every `IndexerError` subclass in `errors.py` (36 classes), built one
instance of each with a probe message, and compared `render()` and `exit_code`
with the section the reference files the type under:

```
<checkout>/src/code_indexer/errors.py
ExtensionUnavailableError      renders ConfigError  exit 3  row-in-exit-section: ok
IndexerError                   renders InternalError exit 1  row-in-exit-section: ok
KeeperFailureError             renders KeeperFailure exit 1  row-in-exit-section: ok
LocatorMismatchError           renders LocatorMismatch exit 5  row-in-exit-section: ok
VectorsDisabledError           renders ConfigError  exit 4  row-in-exit-section: ok
ProviderTimeoutError           renders ProviderTimeout exit 3  row-in-exit-section: ok
```

- `ExtensionUnavailableError(ConfigError)` inherits `error_type = "ConfigError"`
  and sets `exit_code = EXIT_BACKEND`, so a caller sees `ConfigError` and exit 3;
  the row is under exit 3 and names the class in its cause. Correct.
- `IndexerError` renders `InternalError` at exit 1; the `InternalError` row
  covers it. Correct.
- `KeeperFailureError` sets `exit_code = EXIT_FAIL` and now has a row only under
  exit 1. Correct -- the base had it under exit 5, which was wrong.
- `LocatorMismatchError` sets `exit_code = EXIT_LOCK`; the exit-5 row is right.
  Correct.

Every one of the three match what the code renders on stderr. A wrong label
would have been worse than a missing row; none is wrong.

### Proof the new assertion can fail

A copy of the tip, one row deleted from its copy of the reference:

```
... failures.md has no row for error types the code declares:
    ['ProviderFailure']; a caller that meets one cannot look it up
FAILED tests/test_docs.py::test_failures_reference_names_every_declared_error_type
1 failed, 36 deselected in 0.05s
```

The copy was restored (`git status --porcelain -- skill/ src/ tests/` empty) and
the same selection passed again.

The assertion does not fail for every row, though -- see finding F1: deleting
the `NotUtf8` row leaves the test passing.

## FOLD-B -- the plan's provider member, both paths driven live

```
uv run python <scratch>/live_probe.py <checkout>
materialize(tf, RenameEdits) ->
{
  "provider": {
    "name": "pyright-stub",
    "version": "1.2.3",
    "transport": "stdio",
    "conversion_version": 1
  },
  "edits": [ ... one whole-line edit, unchanged by this fold ... ]
}

build_document(provider=<materialized member>)["provider"] ->
{ "name": "pyright-stub", "version": "1.2.3", "transport": "stdio",
  "conversion_version": 1 }

build_document() with no member ["provider"] ->
{ "name": "tree-sitter-structural", "version": "1" }
```

Both paths behave as claimed: the writer honours the member it is handed
instead of stamping the structural constant over it, and a plan with no member
still gets `structural_provider()`, so syntax-only plans are unchanged. The
member carries the three identity keys plus `conversion_version` from the one
conversion module, and `ProviderIdentity.block()` returns a fresh dict, so the
conversion version does not leak back into the identity.

The writer refuses a partial member rather than sealing it:

```
incomplete member {'name': 'x', 'version': '1'}
  -> InternalError: a provider-derived plan member needs name, version,
     transport; it is missing ['transport']
materialize(tf, bare edits)
  -> InternalError: materialize takes the RenameEdits a provider answered,
     not list: the provider member lives there
```

**The three new tests fail at the base.** A copy carrying the base's sources and
this tip's tests:

```
E  TypeError: 'RenameEdits' object is not iterable
E  AttributeError: module 'code_indexer.plans' has no attribute 'structural_provider'
FAILED tests/test_provider_materialize.py::test_a_materialized_body_carries_the_provider_that_produced_it
FAILED tests/test_provider_materialize.py::test_the_document_honours_the_provider_member_it_is_given
FAILED tests/test_provider_materialize.py::test_a_syntax_only_plan_still_names_the_structural_provider
3 failed, 32 deselected in 0.08s
```

Those are shape/attribute failures at the base, not value comparisons: the
signature and the seam did not exist there, which is the finding itself. The
behaviour pins at the tip are the live probe above.

**The shape disagreement is recorded and left alone.** The fold record carries a
section named for it, which says ADR-0004 section 1 wants `{name, version}`,
the other record wants three keys plus the conversion version, the writer
implements the three-key form, and reconciling two ratified records is the
owner's call; `docs/json-schema.md` states both shapes and says the reader
requires a `provider` object rather than a fixed key set. The board's owner-call
card for that reconciliation exists and is untouched by this leg. Not a fold,
and correctly not folded.

## FOLD-C -- a timed-out session is retired

At the base, with this tip's tests, the review's own probe:

```
E  ProviderTimeoutError: the stub-fixture provider did not answer
   'resolve_symbol' within 0.05s: the request was cancelled ($/cancelRequest)
   and is reported as a timeout, never as an empty answer
FAILED tests/test_provider_contract.py::test_a_timed_out_session_does_not_poison_the_next_request
1 failed, 20 deselected in 0.16s
```

The leaked exception is raised by request 2 -- the wrong answer about a healthy
provider, not a wording mismatch.

At the tip, driven live rather than only as a test:

```
request 1 -> ProviderTimeoutError | the stub-fixture provider did not answer
  'resolve_symbol' within 0.05s: the request was cancelled ($/cancelRequest)
  and is reported as a timeout, never as an empty answer; the session is
  retired, so the next request starts a fresh provider rather than queuing
  behind this call
instances built: 1
after the timeout, before request 2:
  instance       = None
  capabilities   = None
  worker/executor= None
  retired_reason = stub-fixture did not answer within 0.05s
  first provider closed = True
request 2 -> answered: ['greet']
providers built by then: 2
```

So the retirement drops the instance, the cached capabilities and the worker
(executor shut down without waiting), closes the provider, and records why; the
message tells the caller the session was retired and the next request starts a
fresh provider. Request 2 answers instead of reporting `ProviderTimeout`.

## FOLD-D -- four pieces of polish

- **The drift helper asserts instead of raising.** Live, against a path outside
the checkout:

  ```
  AssertionError (as required): <outside>/SKILL-CLI.md is outside the checkout
    <checkout>: these checks read the tracked tree, so a copy elsewhere is not
    a tree they can judge
  inside the checkout -> SKILL-CLI.md
  ```

  No `ValueError`, and the helper still returns the relative path for a real
  path. The test that holds it is present and passes.
- **The changelog dash.** `## [Unreleased] -- the agent surface` became an em
dash; every one of the file's 25 `## [Unreleased]` headings now uses the em dash,
  including the round's own second entry.
- **No dangling bracket in the roster.** The generated line is now
  ``| `reindex-project` | write | Force a full rebuild of a project's index ... |``
  and a scan of the entry document finds no unclosed bracketed fragment. The
  generator's `_bounded` drops a bracket it cannot close together with the words
  inside it, and `generate_skill_docs.py --check` exits 0, so the tracked file is
  the generator's own output.
- **The dead asserts are gone.** `assert ` does not appear anywhere in
  `refactoring_provider.py` any more; the module's only guards are the ones that
  raise.

## Standing bars

```
uv run pytest tests/ -q -W error
890 passed, 1 skipped, 2 deselected in 103.41s        exit 0

uv run pytest tests/test_docs.py -q
36 passed, 1 skipped in 0.65s

uv run python scripts/generate_skill_docs.py --check
exit 0

wc -lc SKILL-CLI.md skill/references/*.md
  117  6447 SKILL-CLI.md            (budget 120 lines / 8192 bytes)
  127  9178 skill/references/failures.md     (budget 400 / 24576)
  287 12911 skill/references/indexing.md
  141  6224 skill/references/machine-output.md
  204 10595 skill/references/plans.md
  309 14563 skill/references/query.md

uvx ruff check src tests scripts
All checks passed!

uv run python scripts/publishing_hygiene_lint.py
publishing hygiene: scanned=799 skipped=0 violations=0 (untracked files are out of scope)
```

The failure reference is 127 lines / 9,178 bytes and the entry document 117
lines / 6,447 bytes, both as the fold record reports; every reference is inside
400 lines / 24 KiB.

**Scope.** `git diff --name-only 2f0a833..45ede9f` touches no command module, and
no added or removed line in the range changes `SCHEMA_VERSION` or
`EXTRACTION_VERSION` (the only matches are a context line inside
`build_document` and a sentence in the fold record). No schema version moved, no
reindex ran and no command was added.

**Signatures, tree, remote, CI.**

```
git log --format='%h %G? %s' 2f0a833..45ede9f
45ede9f G  docs(runs): the round 5 fold record
0ec69e1 G  test(provider): the poisoning proof fails on request 2, not on the wording
07bc581 G  chore(docs): drift-check diagnostic, changelog dash, roster truncation, dead asserts
63a930f G  fix(provider): a timed-out session is retired, not left poisoned
968d6c7 G  feat(provider): the plan's provider member gets a writer
70ba5ba G  docs(skill): the failure reference names every declared error type

git status --porcelain --untracked-files=no    (empty)
git rev-parse HEAD                             45ede9f
git ls-remote origin main                      45ede9f
gh run list ... 45ede9f completed success ci
```

## Findings

### F1 -- LOW -- the taxonomy assertion cannot see a declared type whose name
carries a digit

`tests/test_docs.py:371-385`. Both readers use `[A-Za-z]+`:

```
_declared_error_types   r'^\s+error_type = "([A-Za-z]+)"'
_documented_error_types r"^\| `([A-Za-z]+)` \|"
```

`errors.py` declares `NotUtf8`, and `failures.md` carries a `NotUtf8` row, so the
pair of patterns agrees with itself and the test passes -- but neither side can
see that type. Measured: the code declares 32 distinct types and my own reader
sees 32; these two readers see 31 each. Proven by deleting the row on a copy:

```
(delete the `ProviderFailure` row)  1 failed   -- the guard works
(delete the `NotUtf8` row)          1 passed   -- the guard is blind here
```

The table is complete today, so nothing a caller meets is unlookupable; the cost
is that the check this fold exists to add would not notice the next such row
going missing, and its docstring claims "every declared type". One-line fix in
each pattern (`[A-Za-z0-9_]+`), plus one case that deletes a digit-bearing row.

### F2 -- INFO -- `PartialApply` is filed under exit 5, and the code exits 1

`skill/references/failures.md` files `PartialApply` under "exit 5 -- a lock or a
concurrency conflict", while `PartialApplyError` sets `exit_code = EXIT_FAIL`
and `tests/test_partial_apply.py` asserts exit 1 with the same type on stderr.
The new assertion compares sets, not sections, so it neither notices nor is
supposed to.

I am not filing this as a defect because it is not one unknown: a keeper refusal
that arrives mid-loop with a lock-class code exits 5 as `PlanStale` while
the reference describes the same situation as `PartialApply`, and the owner
already has a card for the disagreement between the ratified apply record and
the code ("no code change until he rules"). The row's *type* is right for one of
the two paths and wrong for the other, which is exactly the open question. It is
recorded here so the fold is not read as having verified section placement.

## What I did not verify

- **No language server.** Every provider claim is against the in-process stub;
  the fence forbids installing one, and the reversal rule in the provider record
  stays untested.
- **The installed skill copy.** The drift check skips locally with the legacy
  single-file reason, as the brief predicted; the release install is the
  orchestrator's step.
- **Sealing or applying a provider-derived plan.** Schema 1 still refuses
  `rename_symbol`, so the materialized body is produced and validated but no
  command can seal it.
- **The sixteen gate bars.** Their record stands; I re-ran only the bars listed
  above.
- **CI for the middle commits.** Only the tip's run was read.
- **The owner's reconciliation card beyond its title and description.**
- **Nothing was fixed.** The one low finding and the restated owner call are for
  the owning developer and the owner respectively.

## Captures

No capture files are committed with this record: every command above is quoted
from the run made here, and the scratch tree they were made in lives outside the
repository. Nothing quoted carries a host path, an index root, a board id or an
internal profile name.
