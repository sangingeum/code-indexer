# Round 4 folds — narrow re-verification

Base `65c88fa`, tip `1fcb1eb`. Scope: the six folds and the three cheap closes the
fold leg landed, verified from behaviour, plus the standing bars. This is not a
re-run of the round-4 gate's twenty-two bars; those stand at the gate record.

Six folds, by the commit that carries each: the three absent fixtures
(`5f6c662`), a duplicated `files[]` path (`7815e32`), `partial` about a
half-written file (`a678c56`), the preview's own hash and schema (`d057c69`),
the removed rename command (`a6eab09`), and the `parse_unavailable` sentence
(`734ea00`). The same commit carries the three closes.

## Verdict

**PASS WITH FINDINGS.** All eight bars hold on behaviour: six folds, the three
closes, and the one refusal to fold a wording question. One LOW finding: the
`--skip-stale-check` option's own help string still tells a reader of
`plan-apply --help` that the flag is "accepted on every command and INERT",
two lines under the docstring that says it is refused there. The behaviour the
close promised is real (refused, exit 2, before the keeper probe); the flag's
description was left behind.

Every failing-without-the-fix claim the leg made is true, measured at a
worktree of the base with the post-fix test files: the two duplicated-path
tests, the two half-written-file tests and the three preview tests all fail
there, and the three fixtures the leg called coverage rather than a fix proof
do pass there. The leg's "eleven more tests than the base" is arithmetic I
re-measured (822 against 811; twelve added, one removed with the command).

## Bars

### 1. The three absent fixtures — PASS

```
uv run pytest tests/test_plan_apply.py tests/test_plan_format.py \
  tests/test_partial_apply.py tests/test_read_contract.py -q -W error \
  -k "nested_declaration or non_ascii_line or attribute_block or ..."
  13 passed, 126 deselected in 0.99s          # tip: 12 new tests + the roster test

<base worktree, post-fix tests, pre-fix sources>
  3 passed, 25 deselected in 0.65s            # the three fixtures, at the base
```

Read at the tip, each assertion exercises what the fold claims rather than an
exit code alone:

- **nested declaration** — qualified names are exactly
  `["Outer", "Outer.inner", "Outer.other"]` (only the walk's container
  recursion builds the dotted name), the method's `(start_line,
  body_start_line)` is `(2, 3)`, the target carries `Outer.inner`, the applied
  file is compared byte for byte including the untouched class line and the
  sibling method, and exactly one keeper mutation is counted.
- **non-ASCII line** — `start_byte == len(prefix.encode("utf-8"))` **and**
  `start_byte > len(prefix)`, so the byte and character answers are shown to
diverge on this fixture; `plan-validate` prints `line and byte cross-check:
ok`, and the applied file equals the expected text as bytes.
- **attribute block** — the decorated function's range starts at line 4 (the
  decorator) with the body at line 6, the decorated method's body at line 14,
an insertion "before" lands at `start_line` 4 with `start_byte ==
line_starts[3]` (above the decorator, not between it and its `def`), and the
applied file keeps both the decorator and the sibling method.

The dev's claim that these pass at the base is **true**, and the leg states it
as coverage rather than a fix proof. I confirmed both halves instead of
accepting the sentence: the same three tests pass at `65c88fa`, and their
assertions are behavioural (qualified names, byte geometry, applied bytes), so
they would fail if the walk or the locator regressed — they simply do not
witness a fix, because there is no defect to witness.

### 2. A duplicated `files[]` path — PASS

Chosen resolution is refusal, naming the path. Driven live in a throwaway
project (a registered fixture project, a stub keeper first on `PATH`), with a
sealed plan whose `files[]` names `mod.py` twice:

```
$ code-indexer plan-apply <duplicated plan> --project foldsample --json
exit=2
{"schema": 1, "ErrorType": "PlanFormatError",
 "error": "files[] names 'mod.py' more than once: one path, one record -- a
           duplicated entry would be applied twice and reported once, so it is
           refused rather than collapsed"}
mod.py bytes after the refusal: 'def one():\n    return 1\n\n\ndef two():\n    return 2\n'
keeper journal entries: 0
```

The concealment is gone in the source: the paths check walked a `set` before,
so the duplicate was folded away before the one check that would have named
it; it now walks a list and raises on the repeat.

Report shape for a legitimate multi-file plan (two files, one edit each,
`--dry-run --json`):

```
report file records: [('mod.py', 'would_apply'), ('other.py', 'would_apply')]
```

One record per distinct path — the fold's own stated reason for refusing the
duplicate, so the shape is verified independently of the refusal.

At the base, both new tests fail:

```
E       assert 0 == 2                       (plan-apply accepted the duplicate)
E       Failed: DID NOT RAISE PlanFormatError  (the paths check never refused)
FAILED tests/test_plan_apply.py::test_a_duplicated_files_path_is_refused_before_any_mutation
FAILED tests/test_plan_format.py::test_a_duplicated_files_path_is_refused_with_the_path_named
2 failed, 92 deselected in 0.57s
```

### 3. `partial` about a half-written file — PASS

Two edits on one file, the stub keeper refusing the second. Text report at the
tip:

```
- mod.py: partially_applied
    replace_body lines 6-6: op_00000000001001
    replace_body lines 2-2: INVALID_ARGUMENT: the stub keeper was told to fail this mutation
1 of 2 edit(s) in mod.py landed; the rest were refused
partial: true
recovery (undo in reverse order):
  keeper history undo op_00000000001001
```

JSON at the tip, same plan: `"partial": true`, the file record `"status":
"partially_applied"`, `"operations": ["op_00000000001001"]`, `"undo":
["keeper history undo op_00000000001001"]`, `ErrorType: PartialApply`, exit 1.

That `partial` is derived rather than forced true is shown by the same rig with
the stub failing the **first** mutation — a refusal with nothing written:
`"partial": false`, `ErrorType: KeeperFailure`, no operations. So the flag
answers "were bytes written", which is the question the format's docs say it
answers.

At the base, both new tests fail:

```
E       assert False is True                       (partial: false about a written file)
E       assert 'mod.py: partially_applied' in <stdout>
FAILED tests/test_partial_apply.py::test_two_edits_on_one_file_second_refused_is_partial_and_named
FAILED tests/test_partial_apply.py::test_the_text_report_states_how_many_edits_of_the_file_landed
2 failed, 9 deselected in 0.94s
```

### 4. The preview's own hash and schema — PASS

Baseline first: the sealed plan validates and previews. Then the same file,
tampered in place (the `replacement_text` of its single edit rewritten):

```
$ code-indexer plan-validate <tampered plan>
exit=2
- plan hash: failed -- PlanHashMismatch: the document hashes to sha256:40ab...
  but carries plan_id sha256:a58f...: a plan rewritten after sealing is refused

$ code-indexer plan-show <tampered plan> --diff        # no --diff also checked
exit=2
stdout: (empty -- no diff drawn)
stderr: PlanHashMismatch: the document hashes to sha256:40ab... but carries
        plan_id sha256:a58f...: a plan rewritten after sealing is refused
```

Structurally invalid (a document with no `files[]`, referenced by path):

```
$ code-indexer plan-show <structurally invalid plan> --diff
exit=2
stderr: PlanFormatError: the document is missing required member(s): created_at,
        files, plan_id, provider, repository, target, validation
```

No `InternalError` and no traceback: the schema check runs first, in the
format's own vocabulary.

At the base, all three new tests fail, and the third failure is the reported
bug itself — the taxonomy's last-line seam:

```
E       assert 0 == 2                       (tampered document previewed)
E       assert 0 == 2                       (disagreeing reference previewed)
E       AssertionError: assert 1 == 2
E        +  where 1 = <Result KeyError('files')>.exit_code     (invalid plan: InternalError)
3 failed, 63 deselected in 0.58s
```

### 5. The removed rename command — PASS

```
$ code-indexer --help | rg -c 'rename-symbol'
0
$ uv run pytest tests/test_read_contract.py -k roster_is_closed
test_roster_is_closed_and_every_command_classified PASSED   (len(roster) == 33)
$ rg -n 'plan-rename-symbol' SKILL-CLI.md CHANGELOG.md docs/json-schema.md
(none)
```

The three documents agree with the roster. `docs/overhaul-plan.md:266`'s
round-5 expectation — `code-indexer --help | rg -c rename-symbol` answering 0 —
now holds, unedited. What survives in tracked files is the *operation*, not a
command: `plans.RESERVED_OPERATIONS` still carries `rename_symbol` and the
format still refuses it (ADR-0004 section 1's requirement, which was never a
command). The other hits are ADR prose and the historical gate documents, which
are records of decisions, not statements about the roster.

### 6. The `parse_unavailable` sentence — PASS

Where a reader of `plan-apply` meets it, not only in the leg's record:

```
$ code-indexer plan-apply --help            # lines 25-33
  the offending lines, or parse_unavailable. They are not a compile check,
  ...
  disagree about one file; parse_unavailable is unreachable through an
  apply by construction, because a file whose language has no grammar is
  refused at the locator first, with LocatorMismatch.
```

Also in `apply.py`'s module docstring, in the `plan_apply` docstring it is
rendered from, and in `SKILL-CLI.md`'s post-apply paragraph ("unreachable
**through** `plan-apply` by construction ... the verdict is verified at the
parser -- it is not an apply behaviour"). Three surfaces, one sentence, no
claim that the verdict is an apply outcome.

### 7. The three closes — PASS (with the finding below)

**One syntax rung.** The post-apply verdict now reads `ts_chunker.parse_file`
(`apply.post_apply_verdict`), the same rung `plan-validate` reads; the second
parse (`parse_tree`) runs only under `verdict == PARSE_ERROR` and only to name
the offending lines — it never decides the verdict. The test asserts the two
surfaces agree for a file with no AST unit and for a broken one.

**The inert stale flag is refused.** `plan-apply --skip-stale-check`:

```
exit=2
stderr: ArgumentError: plan-apply refuses --skip-stale-check: a plan's
        staleness is not skippable when a source file is about to be written;
        the pre-pass compares every touched file's hash
mod.py unchanged: True
```

The refusal also runs with no keeper on `PATH`, and still answers exit 2
`ArgumentError` rather than exit 3 `KeeperUnavailable` — so it precedes the
keeper probe, as the close claims, rather than after it.

**The changelog bullet.** The coordinates entry's continuation lines all begin
with two spaces (`cat -A` on `CHANGELOG.md:23-26`: `  offsets into the stored
bytes...`); nothing breaks at column 0.

**No other command moved.** The whole source diff between base and tip is seven
hunks in `apply.py`, four in `cli.py`, one in `command_classes.py` and two in
`plans.py` — the docstring, the `partial` property, the text report line, the
verdict rung, the file-record construction, the per-file status, the deleted
command, the two `plan-show` docstring/preview call sites, the `plan-apply`
docstring and flag refusal, the read-roster membership, and
`verify_document`/`_check_paths`/`preview`. No other command's body appears in
the range.

### 8. The un-folded wording question — PASS (not folded, as stated)

Nothing in code or docs claims the mid-loop exit 5 is the ADR's exit 1:

```
docs/json-schema.md:491   "the exit code is the taxonomy's: 5 for a stale
                          pre-pass or a keeper 5, 1 for PartialApply ..."
ADR-0005:50               exit 1 with ErrorType PartialApply, report on stdout
ADR-0005:87               keeper 5 (stale hash) maps to this tool's 5
```

The two statements are separated in the ratified record itself (a mid-loop
apply versus a keeper-level stale refusal), and the schema document names the
keeper-5 case explicitly. The leg's record carries the question under "Not
folded", names it a wording question about a ratified record, and leaves it to
the owner — which is what the review ruled (not a fold).

## Standing bars

```
uv run pytest tests/ -q -W error
  822 passed, 2 deselected in 101.06s        # tip
  811 passed, 2 deselected in 101.80s        # base, same suite, base tests
  delta 11 = 12 added - 1 removed with the deleted command

uvx ruff check src tests scripts
  All checks passed!

uv run python scripts/publishing_hygiene_lint.py
  publishing hygiene: scanned=747 skipped=0 violations=0 (untracked files are out of scope)

sha256sum SKILL-CLI.md <installed copy>
  38167676fa36c9be18b58b285018654a9a642044e1798d34c1bb9a2d2d9dc0d5  SKILL-CLI.md
  38167676fa36c9be18b58b285018654a9a642044e1798d34c1bb9a2d2d9dc0d5  <installed copy>
```

The hygiene run scans one file more than the leg reported (747 against 746);
the leg's bar line was run before its own record was committed, and that
record is now tracked. Nothing else differs.

Scope fence: the range touches `CHANGELOG.md`, `SKILL-CLI.md`,
`docs/json-schema.md`, the leg's record, four source files, five test files.
No schema change, no `EXTRACTION_VERSION` change, no reindex (the token does
not appear in the diff except in the leg's own scope sentence), one command
removed and none added.

Lineage: seven commits on `65c88fa..1fcb1eb`, every one signed and verifying as
`G`; the tracked tree is clean (only untracked run captures, which this commit
does not sweep); `git ls-remote origin main` is
`1fcb1eb95375b6a876f351d77553bb3adbbbb220`; the CI run for that SHA is
`42320262255`, conclusion `success`.

Of note in scope, not a finding: the fold made plain `plan-show` (without
`--diff`) verify hash and schema too, not only the preview. That is the
review's finding applied to the sixth plan reader as ADR-0004 section 1 states
it, and the docs and the leg's record say so; it is a widening of the fix
inside the same finding, not a second behaviour change.

## Finding

### LOW — the stale flag's own help string still promises inertness

`plan-apply --help` now shows two contradictory statements ten lines apart: the
command's docstring says the flag is refused here (exit 2), and the shared
option description says it is accepted and inert on every command.

Reproducer:

```
$ code-indexer plan-apply --help
  parse_unavailable is unreachable through an apply by construction ...
  --skip-stale-check is refused here (ArgumentError, exit 2): a plan's ...
  ...
  --skip-stale-check   Accepted on every command and INERT: reads never index,
                       so there is nothing to skip. ...
```

Cause: `cli.SkipOpt`'s help text is one string shared by every command and was
not touched by the close; `SKILL-CLI.md`'s "Key rules" bullet repeats the same
unqualified sentence (the Global flags paragraph and the `plan-apply` command
bullet do carry the exception). Behaviour is correct — the flag is refused —
so this is documentation truthfulness, the very axis the close was about, and
it is below the bar for a repair: one sentence in the option help, one in the
Key rules bullet.

Severity LOW: no caller is misled about an outcome they cannot observe by
trying the flag; a reader of the help page is told both things at once.

Proposed guard (for whoever folds it): a unit test over `SkipOpt.help` cannot
say this; the honest guard is the same `test_docs.py`-style check that compares
a command's own refusal sentence with the flag paragraph it renders, or simply
naming the exception in the shared string ("... except on `plan-apply`, which
refuses it").

## What I did not verify

- **No able-to-fail mutation of the walk.** The three fixtures are coverage, by
the leg's own statement and my measurement; I read their assertions and
confirmed they are behavioural, but I did not perturb `plans._walk`'s container
recursion or `_body_node`'s method path to watch them fail. Nothing in the
round claims they witness a defect.
- **No real keeper.** Every apply that wrote anything ran against
`tests/keeper_stub.py` first on `PATH` in a throwaway project, as the suite
does; no live apply touched this checkout (`--dry-run` only where it was
pointed at a real tree), and a real `keeper 0.1.8` apply was not exercised.
- **No coverage numbers.** The bars did not ask for a coverage run and the
project's thresholds are set outside this record; I measured none.
- **No re-run of the round-4 gate's other bars** (the sanitizer-equivalent
checks, the spec axis, the standards axis). They are of record at the gate and
review artefacts; this leg is the fold repair only.
- **The CI run was observed, not re-run.** I read its conclusion; I did not
re-execute the workflow locally beyond the three local bars above.

## Captures

`runs/round4-folds/verify/`: `pytest-tip.txt`, `pytest-base.txt`,
`named-tests-tip.txt`, `base-fold-a.txt` .. `base-fold-d.txt`, `live-drive.txt`,
`help.txt`, `plan-apply-help.txt`, `ruff.txt`, `hygiene.txt`, `sha256.txt`,
`ci-run.txt`. Absolute host paths, the scratch roots and the worktree path were
elided before commit; each file carries a header saying so, and the commands
and their output are otherwise unmodified.
