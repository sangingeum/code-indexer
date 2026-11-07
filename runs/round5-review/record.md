# Round 5 two-axis review — a provider behind a contract, and an agent surface that fits

Card id withheld (an authored record is hygiene-governed). Range `1177458`..`5f7b745`,
eleven round commits, tree `<project-root>`. The two axes were judged separately and
then aggregated. I rule on the gate's sixteen bars; I did not re-run them, and I did not
re-establish them.

## Verdict

**PASS WITH CHANGES.**

Both halves of the round are real. The provider interface is shaped by a contract and
not by a server's quirks: five operations with documented results or typed refusals, a
capability report where `unknown` is not a denial, exit 2 and exit 3 kept apart by what
the caller does next, one position-conversion module whose boundary test refuses rather
than rounds, and provider edits expanded to whole lines with the whole-old-line hash.
Nothing in it degrades into text replacement, and nothing spawns a server a syntax-only
command did not ask for. The agent surface is the shape ADR-0009 decided: 118 lines and
6,506 bytes at the entry, five references with generated command sections, one roster
source, and six drift checks that the gate proved able to fail one at a time.

The two changes are gaps in what the round says about itself, not in what it does: the
failure reference names two of the seven error types this round introduced, and the
plan's `provider` member that ADR-0006 sections 4 and 5 both require has no writer and
two contested shapes. Neither is observable from the CLI today, because no command
exposes the provider -- which is why neither is a blocker, and why both are cheap now
and awkward after round 6 wires the provider into a command. S1 should land before the
release step installs the skill, since it installs the incomplete reference.

**Blockers: none. Should-fix: 2. Nice-to-have: 3.** Of the gate's findings, F1 folds;
F2, F3 and F4 do not. Both declared deviations are honest and both records state them
accurately.

## Findings

### S1 — SHOULD-FIX — the failure reference names two of this round's seven error types

`src/code_indexer/errors.py:265-319` (seven new `ErrorType`s) against
`skill/references/failures.md:39-60` and `tests/test_docs.py:346-352` (the check that
should have caught it).

The round adds `UnsupportedOperation`, `ProviderUnavailable`, `ProviderTimeout`,
`ProviderRefusal`, `ProviderFailure`, `PositionConversionFailed` and
`NonLineAlignedProviderEdit`. `failures.md` names the first two. A set difference
between the code's declarations and the reference's text (`error_type_coverage.py`,
capture `error-type-coverage.txt`):

```
error types declared in src/code_indexer/errors.py : 32
of those named in skill/references/failures.md    : 17
  missing: ... NonLineAlignedProviderEdit, PositionConversionFailed,
           ProviderFailure, ProviderRefusal, ProviderTimeout, ...
```

Why it matters: ADR-0009 section 2 says `failures.md` carries "every exit code with its
`ErrorTypes`, the cause and the remedy for each", and the whole point of the split is
that this file is the second read an agent makes when a failure is not obvious. An agent
that meets `PositionConversionFailed` on stderr has an exit code and a type it cannot
look up. The shortfall is not only the round's -- ten of the fifteen missing types
predate it -- which is why this is a should-fix rather than a blocker: the round
inherited a remedy table that was never a complete taxonomy, and then added five more
rows' worth of gap without the check that would have noticed.

Check 4 is the reason it went unnoticed, and it is faithful to the letter of ADR-0009
section 5.4 ("every exit code ... appears") while missing its substance: it reads
`^#+ .*\bN\b` headings, so it can only see codes. `ProviderTimeout` is discussed in
prose at `failures.md:59` without being named, so even a grep for the string fails.

What I would accept as the fix: five rows (`ProviderTimeout` under exit 3;
`ProviderRefusal`, `ProviderFailure`, `PositionConversionFailed`,
`NonLineAlignedProviderEdit` under exit 1), and a fourth assertion in the taxonomy check
that each `error_type` declared in `errors.py` appears in the reference -- or, if the
reference is deliberately a reachable-failures table, a sentence saying so and a check
that the two sets are compared on purpose.

### S2 — SHOULD-FIX — the plan's `provider` member has no writer, and two records disagree on its shape

`src/code_indexer/refactoring_provider.py:654` (`materialize(tf, edits)`),
`src/code_indexer/plans.py:721` (`build_document` stamps every document with
`tree-sitter-structural`), `docs/adr/0004` (the member is `{name, version}`) against
`docs/adr/0006` section 4 ("a plan built from one carries the same object", meaning
`{name, version, transport}`) and section 5 ("the conversion's version is recorded in
the plan's `provider` member").

`materialize` is the one function whose output becomes a plan body, and it takes bare
`ProviderEdit` values: the `RenameEdits` that carries the identity is unwrapped by the
caller one call before the document is built, so provenance is dropped at the seam.
`build_document` then hardcodes the structural provider, so even a caller who attached a
block by hand would have it overwritten on the standard path. The two accepted records
also disagree about whether the member has two keys or three, and nothing in the round
resolved it because nothing writes it.

Why it matters: this is the only clause of ADR-0006 that the round neither implements
nor records as deferred. It is unobservable today (schema 1 refuses `rename_symbol`, so
no plan carrying a provider edit can be sealed), and that is exactly the argument for
settling it now: `materialize`'s signature is published and its caller does not exist
yet, so changing it costs one line today and a compatibility question later. The leg
record's provenance paragraph claims "every semantic answer carries `provider`" and is
silent about the plan member, which is what makes this a record gap as well as a code
one.

Direction: either `materialize(tf, rename: RenameEdits)` returning a body that carries
the provider block and the conversion version, or one explicit sentence in ADR-0006
section 6 and in the leg record that the member is deferred to the schema-2 record.

### N1 — NICE-TO-HAVE — a timed-out session stays poisoned for the rest of the invocation

`src/code_indexer/refactoring_provider.py:440-461` (one-worker pool; `future.cancel()`
and the provider's `cancel()` on expiry).

Measured (`timeout_then_retry.py`, capture `timeout-then-retry.txt`) against a provider
whose first answer takes 0.30 s and whose later answers are instant, with a 0.05 s
deadline:

```
request 1: ProviderTimeout: ... did not answer 'resolve_symbol' within 0.05s  [0.05s]
request 2: ProviderTimeout: ... did not answer 'resolve_symbol' within 0.05s  [0.05s]
request 3: answered (0 candidate(s))                                         [0.00s]
provider calls made: 2
```

Request 2 is a false statement about a healthy provider: the single worker is still
running the expired call, `future.cancel()` cannot stop a running call, and the stub's
`cancel()` only sets a flag. Request 3, asked after the slow call finished, answers at
once. ADR-0006 section 4 requires the timeout to be reported as a timeout and it is;
section 7 is written against exactly this class of answer -- one that is wrong and
indistinguishable from right at the moment it is relied on. Direction: retire the session
on expiry (close it and let the next request build a fresh provider), or record that a
timed-out session is single-use. Either is one sentence; silence is the cost.

### N2 — NICE-TO-HAVE — the gate's F4: a drift check whose failure message cannot be built outside the checkout

`tests/test_docs.py:366` builds its own detail with `path.relative_to(REPO)`. Pointed at
a temp copy, the check raises `ValueError` instead of `AssertionError`, so it reports a
crash rather than a drift. It has no effect on the real run, because the real paths are
under the checkout. I agree with the gate that it is a defect; I disagree with calling
it low severity -- it costs a diagnostic in a rig, not a verdict on the tree, so it is a
nice-to-have and belongs in the next commit that touches the module.

### N3 — NICE-TO-HAVE — three pieces of polish

- `CHANGELOG.md:3` opens with `## [Unreleased] -- the agent surface`; the other eleven
  `## [Unreleased]` headings in the same file use an em dash, including the round's own
  second entry at line 35. One character, inconsistent with the file it edits.
- `SKILL-CLI.md:35-77`: the generated "Reach for it when" column truncates click's help
  at 70 characters, mid-parenthesis -- "Register a project directory for semantic
  indexing ...", "Force a full rebuild of a project's index (runs ...". Nothing is lost
  (the decision tree carries the choice) and the budget is met, but the roster is the one
  place where generated text stands alone, and a dangling parenthesis at the end of the
  load path's central table reads as a defect rather than as brevity.
- `src/code_indexer/refactoring_provider.py:524-525`: two `assert`s restate what
  `SymbolQuery.__post_init__` already refuses, and would vanish under `-O`. Unreachable
  and therefore harmless -- named only so a later reader does not mistake them for
  validation.

## The gate's four findings — ruling

- **F1 — fold.** The change is exactly what bar 6 and ADR-0006 section 3 require: a
  reserved operation refusing as `UnsupportedOperation` at exit 2, with no text edit.
  The allowlist omitted the two files that implement the refusal the round exists to
  deliver -- a fence drawn too tightly, not a scope ambition. Widen it; no fix round.
- **F2 — not a fold.** `README.md` carries two `SKILL-CLI.md` links and both resolve
  (`README.md:35`, `README.md:90`). The bar's "five" is the pre-split count and two of
  the five were anchors into `#key-rules` and `#subcommand-reference`, headings the
  split removed; restoring them would have produced the dead links the bar's substance
  is about. Nothing to change in the tree; the bar's wording is what is stale.
- **F3 — not a fold.** A worktree under the system temporary directory is residue
  outside the range, already removed; `git worktree list` now shows the main tree only.
  It cost the gate a finding rather than a verification, so the cheap repair is a line
  in the gate brief: list worktrees before filing one.
- **F4 — not a fold.** See N2. Real, but a diagnostic defect with no verdict attached;
  severity nice-to-have, not low.

## The two declared deviations — do I agree?

**`becef85` red until `b918fe1` — agree, and honestly stated.** The leg record names the
red commit in its own commit table and says why ("the working tree that passed there
carried an uncommitted closed-session check"); `b918fe1`'s message says the same thing
without softening it. What settles it is the shape of the fix: `capabilities()` now
consults `_closed` (`refactoring_provider.py:479-482`) and the source-scan test closes
its file handle. Production code was corrected and no assertion was weakened to make a
green tree -- which is the only question a red-commit deviation actually raises. A
tree-level `git bisect` would land on a commit that fails, and the record says so.

**The surface landed as one commit — agree.** The index-closure check makes the split
all-or-nothing: an entry document whose triggers name files that do not exist fails
check 2, and a reference with no generated block fails check 5. The gate proved that
independently (mutations 2a and 2b), so the claim is the check's behaviour rather than
the author's convenience. Two blemishes, neither hidden: `aaea01b` carries a four-line
`README.md` touch although the file moves in `021585c`, and the record's "prescribed
slice order was followed as an authoring order" is an accurate description of what
happened rather than a claim that four green commits existed.

## Spec axis — ADR-0006

Read against the record, clause by clause:

- **The five operations and their documented results** hold. `capabilities`,
  `resolve_symbol`, `find_references`, `prepare_rename` are delegated
  (`refactoring_provider.py:591-647`), `materialize` is adapter-side
  (`654-727`), and the protocol at `318-341` is the whole surface. Ambiguity is
  `AmbiguousSymbolError` listing every candidate; a provider that returns nothing is
  `ProviderFailure`, not an empty answer (`596-599`, `620-623`, `637-640`).
- **Capability honesty** holds and is the best-built part. Three states, and `state()`
  defaults a missing operation to `unknown` (`137-138`); `_require` refuses
  `unsupported` before the provider is asked and *attempts* `unknown` with a log line
  (`497-510`). Exit 2 versus exit 3 is not just coded but explained where the caller
  will read it -- `errors.py:253-266` and `failures.md:20-24` both say the next move
  differs: another operation, or an install. A start budget that expires is the same
  exit 3 (`432-435`), which the record requires.
- **Lifecycle** holds: constructing a session calls the factory zero times and leaves
  `_instance is None` (`379`); `session_for` keys one session per project and language
  (`737-753`); `close()` terminates the provider and the executor and `atexit` covers
  the rest (`393-409`, `763`); no daemon exists. Timeouts are reported as timeouts, with
  N1 as the qualification.
- **Provenance on every answer** holds -- all three semantic results carry the same
  block (`605`, `624`, `647`) and the test compares all three. **The plan member does
  not** -- S2.
- **No provider identifier in the index** holds by construction: the module imports
  neither the store nor the manifest, and the contract test greps its own source for the
  imports and the SQL. Nothing in the round writes symbol rows.
- **One conversion module, refusing at the boundary** holds. `provider_positions.py` is
  imported by exactly one production file, holds the only two functions, and refuses
  rather than rounds in both directions (`_require_boundary`, `118-131`; the
  surrogate-pair walk, `92-115`). The mid-line case is deliberately *not* a failure
  (`test_a_mid_line_position_is_not_a_conversion_failure`), which is what section 5
  requires and what a reviewer reading only ADR-0004 would get wrong.
- **Provider edits to plan edits** holds: whole-line expansion by byte splicing
  (`706-713`), descending merge (`707-709`), overlap refused rather than ordered
  (`699-705`), the hash over the whole old line (`721`), `expected_old_text` under the
  8 KiB cap and omitted above it (`723-724`), cross-line edits refused
  (`676-680`) and carriage returns refused (`681-684`).
- **Rename is not enabled** holds: `SCHEMA_VERSION = 1`, `RESERVED_OPERATIONS` still
  refuses, the refusal now speaks the provider's taxonomy (`plans.py:781-791`), and the
  command does not exist. The materialized candidate body is produced and validated but
  cannot be sealed -- which is the record's own section 6 boundary, kept.
- **No fallback of any kind** holds. There is no retry, no regex, no AST guess and no
  text-replacement path in the module; a refusal is returned as a refusal.

## Spec axis — ADR-0009

Holds, with S1 as the gap:

- **The entry document's contents and nothing else.** I read all 118 lines. Decision
  tree (15 shapes), generated roster with read-or-write classes, the six-code exit table
  with the `ErrorType` rule, the staleness rule in exactly three sentences (`96-102`),
  the read-only guarantee in one (`104-108`), and the reference index with one trigger
  each. Per-command flags, examples, the plan workflow, provider behaviour and the MCP
  parity table are gone from it.
- **Budgets, measured.** `SKILL-CLI.md` 118 lines / 6,506 bytes against 120 / 8,192; the
  five references at 287/12,911, 309/14,563, 204/10,595, 141/6,224 and 108/6,404 against
  400 / 24,576 (`wc -lc`, matching the leg and the gate to the byte). The failure message
  prints the measured size against the budget (`test_docs.py:303-319`) -- the gate proved
  that on a temp copy, and I read the assertion rather than the proof.
- **One command per reference, 33 covered.** 11 + 16 + 6 = 33, with `machine-output.md`
  and `failures.md` carrying no command sections by design; closure is a test, not a
  claim (`test_docs.py:271-300`), and the gate proved each of its four shapes able to
  fail.
- **Generated versus written.** Marker-delimited regions, the generator as their only
  writer, `--check` as the drift gate, and the roster from the same typer registry the
  CLI builds from plus `command_classes`. The generated prose (`_one_line`) is the only
  place a value is transformed rather than copied -- see N3.
- **Install ownership.** The round did not touch the installed skill; the record says so
  and the skip's reason on this host is the legacy single-file copy, which is the pre-
  release state the brief declares. The digest check was proved against a throwaway root
  with equal, tampered and legacy copies, which is the only way it could be proved
  before the release step.
- **The six drift checks** exist, are independent of the generator, and the gate's
  able-to-fail exercise covers each one separately. Check 4's scope is S1.

## Standards axis

- **Concept names.** `provider_positions`, `refactoring_provider`, `materialize`,
  `session_for`, and test names written as sentences. ADR numbers appear only in
  docstrings and reference headers, as provenance, which the terminology rule allows. No
  coordinate labels in the range.
- **Layering.** The provider is a seam, not a second path to the same answers: no
  production module imports it at all, so nothing it computes can disagree with what the
  CLI already answers. That is the right shape for a round whose record says the
  interface lands before any server, and it is also the reason both should-fix findings
  are unobservable today.
- **Duplication.** One conversion module, one generator, one roster source (the registry
  plus `command_classes`). `README.md` and the entry document each name the reference
  index, which is a pointer and not a second copy of the facts.
- **Error taxonomy.** No invented exit code: 2 for a supported-usage refusal, 3 for
  nothing answering, both following ADR-0003 section 5, and `ProviderTimeout` subclassing
  `ProviderUnavailable` so the number and the type tell the same story. S1 is the
  documentation half.
- **Over-engineering.** I looked for anything built that no record asks for and found
  one line: `CapabilityReport.supports()` (`140-141`), which no caller uses. Not worth a
  finding. `--json` on the four pass commands is granted by ADR-0003 section 7 and
  scheduled by the plan; the generator is required by ADR-0009 section 3.
- **The load path itself.** 118 lines is enough, and the trade landed on the right side.
  A real question -- "who calls this symbol, does the command write, what does exit 3
  mean" -- is answered from the entry document alone: the decision tree gives the
  command, the roster gives the class, the exit table gives the branch. What the second
  read buys is flags, examples and remedies, which is exactly the layer the record says
  belongs in a reference, and the triggers name situations rather than files so an agent
  knows when to spend it. The one place the entry document leans on a reference for
  something structural is the plan workflow, and the ADR moves it deliberately. I would
  not trade back to one file.
- **Test quality -- which bar has no failing-without-the-fix test.** Two: check 4 cannot
  fail on a new `ErrorType` (S1), and nothing asks a session for a second request after a
  timeout (N1). Everything else has one -- the position cases and the materialize cases
  are per-clause, the refusal paths are asserted as types and exit codes rather than as
  messages, the closed-session behaviour has the test that made `b918fe1` necessary, and
  the gate proved the six drift checks able to fail individually rather than in a batch.
- **Commit hygiene.** Eleven commits, every one `G`, conventional subjects, no amend and
  no force; the two deviations declared where a reader of the log will find them. The
  bare-`git log` habit is avoided.
- **Doc quality.** `README.md` is rewritten around the new behaviour rather than patched
  (`25-40` carries the surface table and the entry-document role), keeps the parity list
  on its own, and its two `SKILL-CLI.md` links resolve. `CHANGELOG.md` carries both
  entries under `## [Unreleased]` with the em-dash heading exception of N3. The
  generator's `--check` exits 0 at the tip, per the gate.

## What I deliberately did not review

- **The real language server.** None is installed and the fence forbids one, so the
  contract, the refusals and the capability report are proved against the stub. ADR-0006
  section 2's reversal rule ("if the Python server cannot express rename, the next
  candidate is C#") remains untested and untestable here.
- **The release install** and the household skill directory. The round did not touch it;
  check 6's skip is the pre-release state, and the digest check has only ever run against
  throwaway roots.
- **Per-commit CI for the middle provider commits.** Runs exist for the round base, the
  follow-up and the tips only, so "CI green" is a statement about the tip.
- **`plan-apply` with a materialized edit.** Validation was gated; applying writes
  sources, so the write path for a semantic plan is untested.
- **The MCP relay** and the parity list's content beyond its presence in `README.md`.
- **Sampled, not read whole:** `indexing.md`, `query.md` and `machine-output.md`. I read
  `SKILL-CLI.md` and `failures.md` in full and the other three by structure; their
  per-command flag accuracy is check 5's job and the gate proved that check able to fail.
- **Performance and cost claims** were not re-measured, and no coverage threshold is
  declared for this round.

## Captures

All under `runs/round5-review/`, masked: no host path, no index root, no board id, no
internal profile name. `error_type_coverage.py` and `error-type-coverage.txt` (S1);
`timeout_then_retry.py` and `timeout-then-retry.txt` (N1). Both are probes of my own,
not re-runs of a gate bar.

## Hygiene

```
uv run python scripts/publishing_hygiene_lint.py
```

```
publishing hygiene: scanned=798 skipped=0 violations=0 (untracked files are out of scope)
```

Read from the run made after this record and its two captures were written and staged,
and carried in the same commit as they are. No host path, index root, board id or
internal profile name appears in any of them.
