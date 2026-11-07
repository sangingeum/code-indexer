# Round 4 two-axis review — plans that can be applied, and recovery that can be read

Card id withheld (an authored record is hygiene-governed). Range `4afe05b`..`9b6f4fc`,
eleven round commits plus four CI and hygiene repairs, tree `<project-root>`.
Two axes judged separately, then aggregated. I rule on the gate's twenty-two
bars; I did not re-run them.

## Verdict

**PASS WITH CHANGES.**

The round's claim stands on its own terms. I read the format, the locator, the
precondition sequence and the whole apply path, and I found no false write, no
wrong refusal, no unmet guarantee and no invented exit code. The four
observables ADR-0005 section 1 states are the four the code keeps.

Three things must change before the round is called closed, all small and
additive: the report's `partial` flag is `false` while a file has already been
written (reproduced, S1); the preview path never checks the plan's own hash, so
`plan-show` prints and diffs a document that `plan-validate` and `plan-apply`
both refuse (reproduced, S2); and a seventh plan command ships that no record
asks for and that a later round's acceptance line expects to be absent (S3).

**Blockers: none.** Should-fix: 3. Nice-to-have: 5. The gate's F1 and F2 fold;
F3 is accepted.

## Findings

### S1 — SHOULD-FIX — the report says `partial: false` about a file it half-wrote

`src/code_indexer/apply.py:106-112` (the `partial` property counts only files
whose status is `applied`) with `apply.py:401` (`outcome.status = FAILED` for
the whole file on any failed edit). A plan with two edits on one file, where
the first edit is written and the second is refused, reports:

```
$ uv run python runs/round4-review/partial_file_report.py
exit_code      : 1
ErrorType      : PartialApply
partial        : False
operations     : ['op_00000000001001']
file status    : failed
file bytes now : 'def one():\n    return 1\n\n\ndef two():\n    return 22\n'
```

Why it matters: `partial` is the documented answer to "did anything get
written", and it is the flag a caller checks before deciding whether to run
the recovery list. Here the file was written, one operation exists, and the
flag says nothing changed. `ErrorType: PartialApply` and `partial: false`
contradict each other in the same payload, and the status vocabulary
(`failed`) cannot express "half of this file's edits landed". The apply record
says this case "is reported as PartialApply and undoable in reverse" — true of
the error type, false of the report body.

No test reaches the shape: every suite fixture at file granularity has one
edit per file, so the file-level and edit-level counters cannot disagree. This
is the bar with no failing-without-the-fix test.

Fix direction: derive `partial` from the operations against the edits (or let
a file carry a status that says its first edit landed) and add the two-edit
fixture the format's ordering rules exist for. The record's own F2 fix — one
path, one record — does not touch this case.

### S2 — SHOULD-FIX — the preview path never checks the plan's own hash

`src/code_indexer/cli.py:2624` calls `plans.preview(document, entry.path)`,
and `plans.py:1170-1188` runs only `_load_files` + `_check_file_preconditions`.
No plan hash, no schema. `resolve_ref` (`plans.py:141`) hands `requested` back
to `plan-validate` and `plan-apply`, which pass it to `run_preconditions`; the
show path drops it.

```
$ uv run python runs/round4-review/show_reference_hash.py
ref offered      : sha256:3e4b...
file name behind : 3e4b....json
content hashes to: sha256:c050...
plan-validate   : passed=False
plan-show --diff: ACCEPTED, diff bytes=108
```

Two requirements go unmet for one of six commands. ADR-0004 section 1: "a path
whose content hash differs from the requested hash is refused" — and the
reason it exists, the Consequences section: "a plan file edited by hand is a
hash mismatch". A caller can preview a plan that will be refused at apply,
against bytes it claims to describe. Second, with no schema check a document
that is structurally invalid reaches `document["files"]` and dies as
`InternalError` (the last-line seam in `cli.py:2204`) instead of the taxonomy's
`PlanFormatError` — a user-visible bug report for a user's bad plan file.

The suite covers the disagreeing reference for `plan-validate` only
(`tests/test_plan_format.py:809`); no test exercises `plan-show` with a
mismatched reference or a tampered document.

Fix direction: give `preview` the `requested` argument and run the same
precondition sequence before diffing, or at minimum `_check_plan_hash` +
`_check_schema`; then one test per command for the tampered file.

### S3 — SHOULD-FIX — `plan-rename-symbol` is a seventh command no record asks for

`src/code_indexer/cli.py:2533-2549`. The round's deliverable is six commands
(three writers, validate, show, apply) and ADR-0004 section 1 asks that
`rename_symbol` be *an operation the format refuses*, which `plans.py:783-788`
already does. The command is unasked-for surface whose only behaviour is to
fail, and it breaks a bar a later round states as its own acceptance:

```
$ code-indexer --help | rg -c rename-symbol
1                       (<project> docs/overhaul-plan.md:266 expects 0)
```

Fix direction: delete the command (the schema refusal is the requirement), or
have the owner amend that acceptance line — but not both silently.

### N1 — NICE-TO-HAVE — exit 5 mid-loop carries `partial: true`

`apply.py:287-300` maps keeper's 5 to `PlanStale`, five, whatever has already
been written; `tests/test_partial_apply.py:139` asserts the code and the
report but not the flag. ADR-0005 section 1 says a mid-loop failure is exit 1
`PartialApply`, and ADR-0004 section 4 defines 5 as a whole-plan refusal with
zero mutations. The JSON schema doc already states the exception, so a caller
who read it is right; a caller who read the ADR is wrong. One sentence in
ADR-0005's exit mapping, or exit 1 when `operations` is non-empty, settles it.

### N2 — NICE-TO-HAVE — the syntax verdict has two sources

`plans.py:1047` reads `ts_chunker.parse_file` (which reports
`parse_unavailable` for a file whose AST walk yields no chunks); `apply.py:320`
reads `parse_tree` and `has_error`/`is_missing`. Both are honest, and they can
disagree about the same file: validate says `parse_unavailable`, the apply's
post-verdict says `parsed`. ADR-0004 section 7 names one rung; one function
should answer it.

### N3 — NICE-TO-HAVE — `plan-apply` accepts and ignores `--skip-stale-check`

`cli.py:2659`. Every other plan command documents the flag as inert; on the
write command a flag promising to skip the staleness check reads as a feature
and does nothing. Refuse it explicitly ("a plan's staleness is not skippable
when applying") or leave it off this command.

### N4 — NICE-TO-HAVE — `CHANGELOG.md:23-24` breaks its own bullet

Line 24 ("offsets into the stored bytes ...") begins at column 0, so the
coordinates entry renders as a bullet plus a detached paragraph. One indent.

### N5 — NICE-TO-HAVE — the reserved-rename refusal is stated twice

`cli.py:2543` (`ArgumentError`, "references are textual, so nothing here could
verify a rename") and `plans.py:784` (`PlanFormatError`, "no structural
relabelling ... references are textual"). Same rule, two wordings, two error
types, exit 2 both. The round's own standard is one statement of a rule; this
is a small one, but deleting S3 removes it.

## The gate's three findings — ruling

- **F1 (MEDIUM, three fixtures) — FOLD.** The medium label is right, and this
  is not fixture taste: the nested-declaration case is the only thing that
  exercises `plans._walk`'s container recursion and `_body_node`'s method path
  — new code in this round with no test at all — and a non-ASCII line is the
  only thing that exercises byte geometry where line and byte arithmetic can
  diverge while every ASCII test stays green. Not a blocker (nothing false is
  claimed today), not low.
- **F2 (LOW, duplicated `files[]` path) — FOLD.** Cheap, and the report is the
  deliverable. Add: `_check_paths` (`plans.py:847`) already dedupes into a set
  and prints "1 path(s)", so the duplicate is not merely unchecked, it is
  hidden from the one check that would have mentioned it.
- **F3 (INFO, `parse_unavailable` unreachable through `plan-apply`) — NOT A
  FOLD.** The verdict is right at the parser and unreachable by construction,
  because a grammarless file cannot be located. Accept; carry one sentence
  into the apply path's documentation so nobody cites it as an apply
  behaviour.

## The gate's own honesty

Strong. It declared F1 medium rather than low and defended it on the same
reason I do; it named four things it could not verify instead of implying a
broader sweep; its bar 16 (no test writes into the checkout) and bar 15
(vocabulary) are the two bars a hurried gate usually asserts and skips, and it
verified both by capture. Its zero-mutation proof for a stale apply — keeper's
journal for the plan id holds zero entries — is the right evidence, not a file
hash that could be explained another way.

Its four declared gaps, ruled acceptable for a round-4 pass: the CI workflow's
content (the suite is what the round claims, and CI green was watched),
non-Python grammars and Windows/macOS (the round's format is language-agnostic
by design and its fixtures are Python by record), the concurrent-writer race
(the guard is carried on every mutation and a mid-loop refusal is exercised;
instrumenting a real race is a hardware-timing exercise, not a review bar),
and CRLF through a live apply. The last is the one I would want before round 5
— it is the only gap where a format rule meets a write — but the translation
is tested at the unit boundary and keeper preserves the convention, so I
accept it rather than reopen the gate.

## Spec axis — does the tree answer ADR-0004 and ADR-0005?

Substantially yes, with S2 as the single unmet clause.

- **The document.** Canonical bytes, `plan_id` over the bytes with the member
  absent, file named by the hash, no store, `--print` writing nothing (not even
  the directory), unknown members preserved on `show` and never rewritten: all
  present and tested. Required and optional members match section 1 exactly;
  there is no `symbol_id` and no column anywhere.
- **The locator.** Five members plus the range as evidence; the four
  invalidation cases each have a capture and a test, including the
  two-identical-overloads refusal that lists both candidates. The block is
  `{verified: "structural", basis: [file_hash, decl_hash, region_hash]}` stated
  once (`plans.py:806-812`) and used by validate and apply; nothing relabels it
  semantic, and `semantic_required` is refused with exit 3.
- **Coordinates.** 1-based inclusive lines, no terminator in its line, BOM
  outside every range and preserved, `lf`/`crlf` declared and checked, mixed
  terminators refused, a plan carrying `\r` refused, `position: end` with no
  line number, and the line/byte cross-check that turns a corrupted offset into
  arithmetic failure. A pure-CR file is refused as `MixedNewlines` although it
  does not mix (`plans.py:376-384`) — the message is the only inaccuracy, and a
  test names the choice deliberately, so I note it and do not raise it.
- **Preconditions and the gap.** One sequence (`plans.py:1063-1106`), called by
  both commands and proven shared by a test that patches the function and
  counts its calls. A passing validate is evidence; a stale file at apply is
  exit 5 with zero mutations, proven by keeper's empty journal. Whole-plan
  refusal, no re-derivation, no partial honour — except the report defect S1.
- **Overlap, adjacency, ordering.** The rule is stated once
  (`order_key`/`check_edit_order`) and called by prepare, validate and apply; a
  range edit before an insertion at one boundary is ordered, an insertion
  strictly inside a range is refused, two insertions at one point are refused,
  adjacency is legal, and files go ascending while start lines descend.
- **The lifecycle.** Five stages, six commands, `--json` on each, write-free
  reads (measured against the plan dir, the source and the manifest triple) and
  one writing command.
- **Apply.** The four observables are stated honestly and nothing stronger; the
  primitive is the line primitives, never `apply-patch`, with the measured
  reasons in the record; pre-pass then loop with one mutation per edit and the
  hash the previous mutation returned; `--dry-run` through keeper's own flag;
  the report; recovery in reverse with a refusal reported and never retried;
  the receipt plus the journal's `--task-id`; the capability probe by flags;
  the exit mapping; no direct-write fallback (`KeeperUnavailable`, exit 3); and
  the post-apply verdict from the parser, not the chunker's fallback.
- **Validation honesty.** No plan surface says "valid", "compiles" or
  "type-checks"; the caveat travels with the verdict in text and JSON; the
  banned vocabulary appears in no feature output. I checked the governing ADR's
  own use of "atomically" rather than taking the gate's exception on trust, and
  it holds: keeper's edit path writes through `atomic_write_bytes`
  (temp file, `os.replace`), so "a crash mid-write leaves either the old bytes
  or the new ones" is true of the line primitives, not only of `file write`.
  The record should cite that line rather than leaving it implicit.

## Standards axis — house style and CONVENTIONS.md

- **Concept names.** Every new name is natural language (`run_preconditions`,
  `check_edit_order`, `ordered_edits`, `region_text`, `post_apply_verdict`,
  `not_attempted`, `would_apply`); no coordinate label in a module, function,
  test or doc. The `round4-*` directories under `runs/` are where this house has
  kept every round's captures since round 1 — provenance, not a concept name —
  and nothing under `src/`, `tests/` or `docs/` carries one.
- **Layering.** The CLI seam composes and maps flags; no coordinate arithmetic,
  no hash, no ordering rule appears in `cli.py`. The seam is not thin
  everywhere: `_resolve_plan_target` is a fifty-line query and
  `_replacement_text` implements the newline-at-the-input-boundary rule that is
  a format rule (ADR-0004 section 3 rule 5) — both are composition, both belong
  in a seam by this repo's existing style, and neither hides an algorithm the
  modules should own.
- **Duplication.** Order and overlap stated once; one precondition sequence;
  one identity block; one enum of operations. The exceptions are N5 (the rename
  refusal) and N2 (the syntax rung).
- **Error handling and taxonomy.** No exit number was invented: the five used
  are ADR-0003's, and `PlanStaleError` really does carry 5. `KeeperCapabilityMissing`
  subclasses `KeeperUnavailable` so a caller's next move is the same. The one
  handling defect is S2's `InternalError` for a malformed plan.
- **Over-engineering.** `plan-rename-symbol` (S3), the `--note` passthrough on
  two prepare commands (the format's `note` exists for the comment-block move;
  a user flag is extra surface and I would not defend it), and `recover()` —
  which the record defends as the reference the report's recovery lines name,
  and which the tests use; I accept that last one as the record's own stated
  choice rather than a second path to the same guarantee.
- **Test quality.** 66 tests across the four new files; they assert behaviour
  (bytes after a run, the argv keeper received, the journal, the receipt) more
  than implementation. Two named bars have no failing-without-the-fix test:
  S1's mid-file failure and S2's tampered plan. `test_the_pre_pass_and_plan_validate_share_one_code_path`
  is a real test, not a name — it patches the function and counts both calls.
  `test_a_file_with_no_grammar_reports_parse_unavailable` is honest about
  testing the function rather than the command.
- **Commit hygiene.** Eleven round commits, conventional subjects, one slice
  each, every one signed; the records name the docs commits, and the two
  "name the docs commit" commits exist because a record cannot name its own
  SHA. No commit mixes a feature with a record.
- **Docs.** `SKILL-CLI.md` is byte-identical to the installed skill
  (`3ec26040...`, both files, checked here); `docs/json-schema.md` documents
  all six surfaces and the error contract, and its `partial` line is exactly
  the sentence S1 shows to be insufficient; `CHANGELOG.md` sits under
  `## [Unreleased]` with N4's one indentation defect, and its heading still
  carries the previous round's subtitle.

## What I deliberately did not review

- The gate's twenty-two bars, the suite, ruff and the hygiene number — of
  record and re-run by the gate; I ruled on them, I did not re-establish them.
- The four CI and hygiene repairs in the range (`3b4d222`, `91bbfaa`,
  `852443c`, `4afe05b`): they bear on the round only as the reason its captures
  are clean, and none touches product code.
- Concurrency racing the pre-pass, CRLF through a live apply, non-Python
  grammars, Windows/macOS, the CI workflow's own content, `--refresh`'s index
  effect, performance, and the MCP surface (none exists, and none was added) —
  the same four the gate named, plus the three it left to the round's own
  scope.
- I ran no `plan-apply` against this checkout. Both reproducers build a
  throwaway project under a temp directory and print no host path; they are
  committed beside this record and re-runnable.

## Captures

- `runs/round4-review/repro.txt` — both reproducers, run from this directory.
- `runs/round4-review/show_reference_hash.py` — S2.
- `runs/round4-review/partial_file_report.py` — S1.
