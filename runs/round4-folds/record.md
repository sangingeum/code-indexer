# Round 4 folds — the three should-fix findings, the gate's fixes, three closes

Card id withheld (an authored record is hygiene-governed; the dispatch brief
carries it). Base `65c88fa`, tip is the seventh commit below, tree
`<project-root>`. The authority for what to change is the round-4 two-axis
review; where I had to choose, the choice is stated per fold.

## Verdict

**LANDED.** Six folds and three cheap closes, seven commits, signed; every
behaviour change is one a finding names, and nothing else moved. The full
suite is warn-clean at the tip (822 passed, 2 deselected, against 811 at the
base — eleven more: twelve tests added, one removed with the deleted command).

FOLD-C, FOLD-B and FOLD-D each have a test that fails at the base commit and
passes here; the proof lines are below. **FOLD-A's three fixtures pass at the
base too** — they are regression cover for behavior that was correct and
simply untested, and claiming otherwise would be a false bar. FOLD-F carries
one sentence, not a behavior.

## The folds

### FOLD-A — the three absent fixtures (gate finding, review: fold)

Commit `5f6c662`. Three tests in `tests/test_plan_apply.py`, each asserting
what it exercises rather than an exit code alone:

- **nested declaration** — `class Outer` with two methods. Asserts the walk's
  container recursion produces `Outer`, `Outer.inner`, `Outer.other`, that the
  method's body path starts on the statement line rather than the `def` line,
  that the locator carries `qualified_name: Outer.inner`, and that the applied
  file has the class line and the sibling method byte-identical.
- **non-ASCII line** — declarations after a line whose UTF-8 length exceeds its
  character length. Asserts the locator's `start_byte` equals the byte offset
  of the prefix and is strictly greater than the character count (the two
  arithmetic answers differ here and agree in every ASCII fixture), that
  `plan-validate` reports `line and byte cross-check: ok`, and that the applied
  file equals the expected text byte for byte.
- **attribute block** — a decorated function and a decorated method. Asserts
  the declaration range begins at the decorator line, the body path at the
  first statement, that a body replacement leaves the decorator intact, and
  that an insertion "before" lands above the decorator (`start_line` 4, the
  decorator's line) rather than between it and its `def`.

Measured: `3 passed` here; the same three run against a worktree at the base
commit also pass — so this closes a coverage gap, not a defect.

### FOLD-B — a duplicated `files[]` path (gate finding, review: fold)

Commit `7815e32`. **Decision: refuse.** A duplicated path is refused as
`PlanFormatError` naming the path, not collapsed. Two `files[]` records for one
path carry two edits' worth of intent and one file's worth of report; collapsing
would keep applying a document the format cannot describe, and refusing is the
one answer the report never has to apologize for. The report is additionally
built one record per distinct path, so its shape holds independently of the
pre-pass.

The concealment is gone: the paths check kept a `set` and printed `1 path(s)`,
so the duplicate was hidden from the one check that would mention it; it now
walks the list and refuses the repeat.

Measured — the plan path and the report path:

```
base:  plan-apply <duplicated plan>  -> exit 0, file written
       (the second record reported not_attempted)
tip:   plan-apply <duplicated plan>  -> exit 2 PlanFormatError
       "files[] names 'mod.py' more than once ...", 0 keeper mutations,
       file bytes unchanged
       plans.run_preconditions      -> PlanFormatError (DID NOT RAISE at base)
```

### FOLD-C — `partial: false` about a file it half-wrote

Commit `a678c56`. `Application.partial` is derived from the operations now, not
from a count of per-file statuses: any operation that ran means bytes were
written, and `partial` answers exactly that question. A file whose first edit
landed and whose second was refused takes a fourth status,
`partially_applied`, so the report says *which* edit was written (its operation
id) instead of losing it behind `failed`; the text report states
"N of M edit(s) in <file> landed; the rest were refused" beside the
`partial: true` line.

Measured through the review's own repro script, before and after:

```
before:  exit_code 1  ErrorType PartialApply  partial False
         operations ['op_00000000001001']  file status failed
         bytes: 'def one():\n    return 1\n\n\ndef two():\n    return 22\n'
after:   exit_code 1  ErrorType PartialApply  partial True
         operations ['op_00000000001001']  file status partially_applied
         bytes: identical (the first edit's bytes were always written)
```

**Failing-without-the-fix proof**, the new test
`test_two_edits_on_one_file_second_refused_is_partial_and_named`, run in a
worktree at `65c88fa` with the new test file copied in:

```
base:  E  assert False is True            (partial: false about a written file)
       FAILED test_partial_apply.py::test_two_edits_on_one_file_second_refused_is_partial_and_named
       FAILED test_partial_apply.py::test_the_text_report_states_how_many_edits_of_the_file_landed
tip:   2 passed in 3.97s
```

The sibling test fails at the base on `assert 'mod.py: partially_applied' in
<report>`.

### FOLD-D — the preview never checked the plan's own hash or schema

Commit `d057c69`. One function, `plans.verify_document`, runs the schema check
then the hash check; `plan-show` calls it on every read (with and without
`--diff`) and `preview` calls it before the file preconditions. A tampered
document is `PlanHashMismatchError`, a structurally invalid one
`PlanFormatError`, both exit 2, and neither draws a diff. This is ADR-0004
section 1 and its Consequences, applied to the sixth command that reads a plan.

Measured through the review's own repro script:

```
before:  plan-validate : passed=False
         content hashes to sha256:c050...
         plan-show --diff: ACCEPTED, diff bytes=108
after:   plan-validate : passed=False
         plan-show --diff: REFUSED PlanHashMismatchError: the document hashes to
         sha256:c050... but carries plan_id sha256:4766...: a plan rewritten
         after sealing is refused
```

**Failing-without-the-fix proof**, three new tests run at `65c88fa`:

```
base:  3 failed, 62 deselected
       - test_show_diff_refuses_a_document_that_fails_its_own_hash
       - test_show_refuses_a_reference_that_disagrees_with_the_file
       - test_show_refuses_a_structurally_invalid_plan_in_the_format_taxonomy
tip:   65 passed (whole test_plan_format.py)
```

### FOLD-E — `plan-rename-symbol` removed

Commit `a6eab09`. The command is deleted, not just refused: no record asks for
it, and the round-5 acceptance line expects `code-indexer --help | rg -c
rename-symbol` to answer 0 (it now does). The format still refuses the
`rename_symbol` operation in the schema check, so ADR-0004 section 1's
requirement survives as a format rule rather than a command. Its removal also
resolves N5 (the reserved-rename refusal stated twice, with two error types).

The roster count moves deliberately: 34 names to 33 in the read-contract test,
and `plan-rename-symbol` leaves `READ_COMMANDS`, the `NO_REFRESH_READS` set, the
skill's command block and subcommand list, the changelog's opening paragraph,
and the one CLI-level test that asserted the refusal.

### FOLD-F — `parse_unavailable` is not an apply behaviour (one sentence)

The apply module's docstring and `plan-apply`'s docstring now say it: the
verdict is unreachable **through** `plan-apply` by construction, because the
locator refuses a file whose language has no grammar first, with
`LocatorMismatch`, and the verdict itself is verified at the parser. The skill's
post-apply paragraph says the same. Commit `734ea00`.

## Cheap closes

- **N2 — one syntax rung.** `apply.post_apply_verdict` now reads
  `ts_chunker.parse_file`, the rung `plan-validate` reads, instead of
  `parse_tree`. They could disagree about one file: `parse_tree` answers
  `parsed` where the AST walk yields no unit, `parse_file` answers
  `parse_unavailable` for the same bytes. The tree is parsed a second time only
  to name a `parse_error`'s offending lines, never to decide the verdict. Test:
  two files (`# nothing to declare` and a broken `def`) report the same verdict
  through the post-apply path as through `parse_file`.
- **N3 — the inert flag on a write command.** **Decision: refuse.** `plan-apply`
  answers `ArgumentError`, exit 2, before keeper is probed and before any
  precondition runs, with a message saying a plan's staleness is not skippable
  when a source file is about to be written. The flag stays on every command
  (the documented flag contract still holds for parsing); it is only this one
  command that refuses it. Test: exit 2, zero mutations, bytes unchanged.
- **N4 — the changelog bullet.** `CHANGELOG.md`'s coordinates entry is
  re-indented into its bullet. Commit `734ea00`.

## Not folded

**N1** (exit 5 mid-loop carrying `partial: true` where ADR-0005 says exit 1 /
`PartialApply`) is untouched. It is a wording question about a ratified record,
it needs the owner's word, and the review did not rule it a fold; this leg
states the exception and leaves the card to the owner.

## Bars, as they ran at the tip

```
uv run pytest tests/ -q -W error
  822 passed, 2 deselected in 98.19s (0:01:38)      # base: 811 passed, 2 deselected
uvx ruff check src tests scripts
  All checks passed!
uv run python scripts/publishing_hygiene_lint.py
  publishing hygiene: scanned=746 skipped=0 violations=0
SKILL-CLI.md -> <home>/.hermes/skills/code-indexer/SKILL.md
  38167676fa36c9be18b58b285018654a9a642044e1798d34c1bb9a2d2d9dc0d5  SKILL-CLI.md
  38167676fa36c9be18b58b285018654a9a642044e1798d34c1bb9a2d2d9dc0d5  <home>/.hermes/skills/code-indexer/SKILL.md
```

Scope: no schema change, no `EXTRACTION_VERSION` change, no reindex, no new
command (one removed). `plan-apply` was run against this checkout only with
`--dry-run`; every apply that wrote anything ran in a throwaway project under
the test rig's own directory.

## Commits

| # | commit | what |
|---|---|---|
| 1 | `a6eab09` | FOLD-E: remove `plan-rename-symbol` and the docs/roster it touches |
| 2 | `d057c69` | FOLD-D: preview and every plan read verify hash and schema |
| 3 | `a678c56` | FOLD-C: `partial` true when anything was written; `partially_applied` |
| 4 | `7815e32` | FOLD-B: a duplicated `files[]` path is a format refusal |
| 5 | `5f6c662` | FOLD-A: nested, non-ASCII and attribute-block fixtures |
| 6 | `734ea00` | FOLD-F, N2, N3, N4 and the docs they touch |
| 7 | this record | the leg record |
