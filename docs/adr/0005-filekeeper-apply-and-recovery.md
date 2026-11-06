# ADR-0005: applying a plan through filekeeper, and recovery

- Status: proposed (awaiting owner ratification)
- Date: 2026-11-07
- Deciders: owner (ratification), architecture review
- Supersedes / Superseded by: --

## Decision in one paragraph

A plan is applied as one filekeeper line-range mutation per edit, each carrying `--expected-hash` and, where the region fits the cap, `--expected-content`, after a read-only pre-pass has checked every file hash and re-derived every edit's region -- so the guarantee is four observables: nothing is written if any file fails its precondition, each file's bytes move atomically from old to new, the operations that did run can be undone in reverse order, and there is no cross-file atomicity anywhere. filekeeper's `apply-patch` is rejected as the primitive, because keeper 0.1.8 gives it no hash guard at all and its hunk matcher relocates a hunk to a unique preimage elsewhere in the file: measured, a hunk declared at line 4 applied at line 7, exit 0, no warning. A partial failure stops, does not auto-undo, reports applied, failed and not-attempted per file, and hands back the ordered operation ids, which filekeeper's own journal also finds by the plan hash recorded as `--task-id`. There is no direct-write fallback in phase 1: an absent or older keeper is exit 3 with the install line, never an unguarded write.

## Context

1. **The interface that exists, measured on this host.** `keeper --version` answers `keeper 0.1.8`; the guards below are read from each command's own help, and the argument parsers are what decide them, not the README. The mutation primitives and the guards their argument parsers actually accept:

| Primitive | `--expected-hash` | `--expected-content` | `--dry-run` | Journal and undo |
|---|---|---|---|---|
| `file write` | yes | -- | yes | yes, one operation |
| `file replace-lines` | yes | yes | yes | yes, one operation |
| `file insert-lines` | yes | yes | yes | yes, one operation |
| `file delete-lines` | yes | yes | yes | yes, one operation |
| `file apply-patch` | **no** | **no** | yes | yes, one operation for the whole patch |

2. **This settles a question the audit left open.** Audit section 9 says whether `apply-patch` accepts a per-file `--expected-hash` "was not established from the top-level help text" and would need its own help. It has been read: `keeper file apply-patch --help` advertises `--dry-run` and `--i-accept-secret-write-risk`, and nothing else. There is no hash guard on the patch path.
3. **`apply-patch` relocates hunks.** The matcher scans the file for each hunk's preimage, prefers the declared position, and otherwise applies at the preimage's only occurrence (`src/filekeeper/core/operations/edit.py:806-880`, `_apply_hunks`: `elif len(matches) == 1: position = matches[0]`). Measured: a one-line patch declaring `@@ -4,1 +4,1 @@` against a file whose matching line is the seventh was applied at line 7 with exit 0. For a unified diff read by a human that is a convenience; for a plan that recorded a byte range it is a silent wrong-offset write.
4. **One file is atomic, several are not.** A whole-file write is a temporary file in the same directory, flushed and fsynced, then renamed over the destination; copy, move and recursive delete are sequences of individually safe steps, and a multi-file patch is one logical operation whose undo restores every file but whose application is "recoverable, not atomic across files" (`README.md`, "Durability guarantees, in plain words" and "Unified diffs"). Exit code 8 is the tool's own admission that a failed patch could not be fully rolled back (`keeper --help`).
5. **`apply-patch` does validate before writing.** The whole patch is parsed and every hunk checked against its file before anything is written (`README.md`, "Unified diffs"); measured, a patch whose second file's hunk was absent exited 7 with the first file untouched. So the tool does refuse before the first mutation -- on context, not on file identity. A context match is not a file identity check, and check 3 shows it is not even a position guarantee.
6. **Undo is safe and refuses rather than guesses.** Before restoring, undo compares every affected path with the recorded after state; a path that moved is refused with `UNDO_CONFLICT`, exit 12, and nothing is modified (`README.md`, "History and undo").
7. **The requirement's own framing.** Requirement 3 says filekeeper commits per file, that compensation and recovery must be defined, and that atomicity must never be claimed; the reviewed position forwarded with requirement 3 fixes the four-part guarantee. This record keeps that shape and replaces its weakest assumption -- that the guard is available on whatever primitive is used, which check 2 refutes.

## Decision

### 1. The guarantee, stated as observables

A caller of `plan-apply` may assume exactly this, and nothing more:

- **Preconditions first, nothing written before they pass.** Before the first mutation the command reads every touched file, compares its whole-file hash with `files[].expected_hash`, re-derives every target's node and compares `decl_hash`, and reproduces every edit's region against `expected_old_text_hash`. One failure refuses the whole plan (`PlanStale`, exit 5) and no file is touched.
- **Per-file atomicity through the hash guard.** Each edit is one keeper mutation carrying the hash observed immediately before it. A concurrent change between the read and the write is refused by keeper itself (`CONTENT_CONFLICT`, exit 5, measured) with the file untouched, and a crash mid-write leaves either the old bytes or the new ones, never a mix.
- **Ordered undo of what ran.** Every mutation returns an `operation_id`; the report lists them in application order, and undo runs in the reverse of that order.
- **No cross-file atomicity.** There is no moment at which all files are simultaneously old or simultaneously new, and no output of this feature says "atomic", "transaction" or "all-or-nothing".

What a caller may assume when file 3 of 5 fails, and what it may not:

| File | Bytes after a failure at the third file |
|---|---|
| 1, 2 | the plan's new bytes; they stay that way unless recovery is requested |
| 3 | its old bytes -- the mutation was refused or failed before replacing the file |
| 4, 5 | untouched; their preconditions were checked in the pre-pass but no mutation was attempted |

The exit code is 1 with `ErrorType: PartialApply` on stderr and the machine-readable report on stdout. ADR-0003's taxonomy gains no number: a partial apply is "any other runtime failure" there, and the `ErrorType` line is the discriminator it says it is. A refusal in the pre-pass is exit 5 and is not a partial apply -- that case has no applied file to report.

### 2. Which primitive: line-range edits, not patches

A plan uses `file replace-lines`, `file insert-lines` and `file delete-lines`, per edit, and does not use `file apply-patch`. Reasons, in order of weight:

- The hash guard exists on the line primitives and not on the patch primitive (context table, check 2). The guard is the only thing that proves the bytes being edited are the bytes the plan was prepared against.
- The patch matcher relocates a hunk by offset (check 3). A relocated hunk is an edit the plan did not describe, reported as success.
- The plan's ranges are lines and byte offsets (ADR-0004 section 3), and the line primitives take exactly that vocabulary: 1-based inclusive, out of range rejected rather than clamped, newline convention and byte-order mark preserved (`README.md`, "Line numbers" and "Line ranges").

What this gives up, stated plainly: keeper's cross-file pre-validation and its single-operation undo for a whole multi-file patch. The first is replaced by our own pre-pass, which is stricter -- it compares file hashes, where a patch compares context lines. The second is replaced by a list of operation ids undone in reverse, which is more work for the recovery path and is the price of per-edit evidence.

### 3. The pre-pass and the apply loop

- **Pre-pass, read-only**: for each file, `keeper file hash`; for each edit, reparse the file's current bytes and compare the region. No mutation primitive is called yet. This is the same code `plan-validate` runs (ADR-0004 section 4), so the two commands cannot disagree about what "stale" means.
- **Apply loop**: files in ascending path order; within a file, edits in descending `start_line` (ADR-0004 section 5), so an earlier edit never shifts a later one's coordinates. Each edit is one keeper call with `--expected-hash <current hash>` and, when the plan carries it, `--expected-content <expected_old_text>`; the current hash for the next edit is the `after_hash` keeper returned.
- **A failure aborts the loop.** No further file is attempted, no compensating write is issued, and the report is assembled from what actually happened.
- **`--dry-run`** delegates to keeper's own `--dry-run` for every call and therefore still performs the pre-pass; it prints the same report with every file marked `would_apply`.

### 4. Compensation and recovery

- **No automatic undo.** A partial failure stops instead of unwinding, because undo can itself fail: keeper refuses with exit 12 when a path changed after the operation, and that refusal is more or less certain just after a failure, when another agent or the watcher may have touched the files. An auto-undo that silently half-succeeds would leave a state nobody's report describes. The caller decides, from a report it can read.
- **The report** names every file in the plan with one of `applied`, `would_apply`, `failed`, `not_attempted`, plus per edit the line range, the `operation_id` when one exists, and the keeper error code and message for the failure. It also carries `plan_id`, `identity.verified` (ADR-0004 section 2), and `partial: true|false`.
- **The recovery reference** is the ordered list of `operation_id` values, printed in text and carried in JSON. Undo them in reverse with `keeper history undo <id>`. A refusal (exit 12) stops recovery and is reported; it is never retried into place.
- **Where a later session finds this.** Every keeper call passes `--task-id <plan_id>` and `--actor code-indexer`, so `keeper history list --task-id sha256:<hex>` reconstructs the whole application from keeper's own journal without our cooperation -- the journal is the source of truth, not a receipt we wrote. A JSON receipt is also written to `.code-indexer/receipts/<plan-hash>-<utc-timestamp>.json` and its path printed, because a human asking "what happened" should not have to know keeper's flag names; losing the receipt loses convenience, not evidence.

### 5. No direct-write fallback in phase 1

- **Refused, against the proposal.** Proposal section 6.7 permits "a direct filesystem adapter ... for users who do not install filekeeper". This record does not ship one in phase 1. The reasons, in order: a second mutation path doubles the surface that must carry the hash guard, the region guard, the journal and the tests, and a guarantee that holds only on the path an agent happened to take is not a guarantee -- the weakest path defines what the product promises. filekeeper is already the file-mutation tool the agent runtimes around this project are built on, so the population that lacks it is close to the population making no agent-driven edits at all. And this project's own value in this feature is the evidence, which is the journal.
- **Absent or too-old keeper is a refusal, not a degradation**: exit 3, `ErrorType: KeeperUnavailable`, one stderr line naming the install command and the minimum version. No plan is applied by any other means, and no fallback is attempted silently.
- If a fallback is ever wanted, it is a superseding record with its own measurement, and it must state which of section 1's four observables it keeps and which it loses.

### 6. The adapter boundary

- **Subprocess with an argv list, never a shell and never an import.** The adapter invokes `keeper --root <project root> --json --actor code-indexer --task-id <plan_id> file replace-lines ...` and never imports `filekeeper`, whose internals are a different distribution with its own release cadence; the proposal's section 6.7 rule that code-indexer must not touch filekeeper's private database is the same boundary.
- **What it parses**: the JSON envelope's `ok`, `operation_id`, `undo_available`, `before_hash`, `after_hash`, and on failure `error.code`, `error.message`, `error.details.path`. It does not parse human text, and it does not parse stdout by position.
- **Capability probe, by flags rather than by version**: once per invocation the adapter runs `keeper file replace-lines --help` and requires the flags it is about to pass (`--expected-hash`, `--expected-content`, `--dry-run`). A version floor of 0.1.8 is recorded for the install line, but the flag probe is the test that runs: a build that keeps the version and drops a guard has to be caught. A missing flag is `KeeperCapabilityMissing`, exit 3, and the message names the flag.
- **Exit-code mapping**: keeper 5 (stale hash) maps to this tool's 5; keeper 12 during recovery is reported as a recovery failure, not translated into a retry; every other keeper failure maps to exit 1 with `ErrorType: KeeperFailure` and the keeper code preserved in the report.

### 7. Post-apply

- After the loop, every modified file is reparsed and its verdict reported: `parsed`, `parse_error` (with the offending lines) or `parse_unavailable` when the file's language has no tree-sitter grammar, in which case the chunker's regex fallback is what ran (`src/code_indexer/ts_chunker.py:122-160`) and no syntax claim is made. This is a parse verdict, never a compile verdict, per ADR-0004 section 7.
- The reparse writes nothing. ADR-0003 section 1 makes a read write-free, and a verification step is not an indexing pass: the index is not refreshed unless `--refresh` is passed explicitly, in which case one incremental pass runs after the report and its own lock applies. The report says which of the two happened, because an answer that silently indexed would be the behaviour ADR-0003 exists to end.

## Requirements this record resolves

- **Requirement 3** in full: validate-all-preconditions-first is section 1's first observable, per-file atomicity is the hash guard, ordered undo is the reverse-order list, no cross-file atomicity is stated in the same breath as the guarantee, and compensation and recovery are section 4.
- **Requirement 4**'s apply half: applying is a write command, preview is not, and the post-apply reparse writes nothing unless asked (section 7).
- **Addition to requirement 3's guarantee, stated as such:** the requirement's four parts do not say which primitive carries the guard. Section 2 answers it, and the answer is forced by measurement rather than preference -- one of the two candidate primitives has no guard to carry.
- **Disagreement with the reviewed position on requirement 3's "ordered best-effort undo":** ordered, yes; best-effort is the wrong word, because keeper's undo is all-or-nothing per operation and refuses rather than restoring what it can. Recovery therefore fails closed at the first refusal (exit 12) and reports it. Reading "best-effort" as "keep going" would produce exactly the half-restored tree this requirement exists to prevent.
- **Disagreement with proposal section 6.3's "Multi-file atomic-ish apply workflow":** the hyphenated qualifier is the defect. A workflow is either recoverable, with a stated recovery, or it is claimed atomic; "atomic-ish" reads as the second while behaving as the first, and it is what produces a caller that does not check. This record ships the first and forbids the vocabulary (section 1).
- **Disagreement with proposal section 6.7's direct-write fallback:** refused for phase 1, with the reasoning in section 5 and the condition for a future record.
- **Disagreement with the audit's open question:** audit section 11 question 8 assumes the choice is "filekeeper-only or an additional adapter" and section 9 leaves `apply-patch`'s guard unestablished. Both halves of that framing move once the guard is measured: the additional adapter is refused, and `apply-patch` -- the primitive an adapter would most naturally use -- is the one that cannot enforce the requirement.

## Alternatives considered, and why rejected

- **`apply-patch` with a generated unified diff, as the proposal's preview suggests.** It has the best cross-file story and no hash guard, and it relocates hunks. For a plan whose whole value is a recorded range, a primitive that overrides the range is disqualified.
- **`apply-patch` for multi-file plans and line edits for single-file plans.** Two primitives, two guarantees, one command, and a caller that must know which one it got. The guarantee would then hold conditionally, which is the same as not holding.
- **One `file write` per file with the fully reconstructed new content.** Atomic and single-operation per file, and it makes every per-edit region guard unverifiable by the tool that writes: a bug in our reconstruction writes a file the plan never described, and keeper cannot object because it never saw an edit. The line primitives let keeper reject a wrong region.
- **Copy the files to a backup directory before writing, then restore on failure.** A second journal, worse than the one keeper already keeps, and it inherits every concurrency problem the guard solves.
- **Direct `open()`/`os.replace()` in Python, bypassing the subprocess.** It is the direct-write fallback with fewer checks, and it forfeits keeper's journal, which is the only evidence a later session can read.
- **Auto-undo on failure.** See section 4: the undo that most needs to succeed is the one most likely to be refused.

## Consequences

- A five-file plan is five to fifteen keeper operations rather than one; the report is longer and recovery is a list rather than a single id. That is the cost of per-edit evidence, and it is paid on the failure path, which is rare.
- `plan-apply` requires keeper on `PATH` for a working install; the requirement that filekeeper must not be a dependency of the core package still holds, because the dependency is on the feature, not the install.
- Every apply leaves rows in keeper's journal tagged with the plan hash, so a second apply of the same plan after a partial failure is visible as a second set of operations rather than as a continuation.
- Tests that must exist: pre-pass refusal with zero mutations, a mid-loop failure with the report and the untouched later files, reapplication of a stale plan, recovery in reverse order, a refused undo reported rather than retried, keeper absent, and a keeper whose `--expected-hash` is missing from its help text.
- A plan that touches one file has the same code path as one that touches five; there is no single-file shortcut that skips the pre-pass.

## What this record does not decide

- The plan format, its coordinates, its locator and its CLI surface -- ADR-0004's.
- Whether filekeeper itself should grow a hash-guarded patch primitive. This record would use it if it existed and verifies it is absent today; proposing it upstream is a separate act.
- Whether the apply path is exposed over MCP, and how MCP would carry the partial-apply report.
- Which language server comes first, and whether a semantic provider ever supplies edits that this primitive cannot express.
- The milestone plan and the project's name.
