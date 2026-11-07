# Round 4 verification gate — plans that can be applied, recovery that can be read

Card id withheld (an authored record is hygiene-governed; the dispatch brief
carries it). Round-4 base `4afe05b`, plan-format base `fb932ff`, tip `055e635`,
tree `<project-root>`. Fifteen commits in the round's range, all signed.

## Verdict

**PASS WITH FINDINGS.** Every behavioural bar the card states was run fresh in
this session and holds: the plan format's four bars, its coordinate and
encoding rules, its ordering, and the apply path's seven bars, plus all six
standing bars. Sixty-six of sixty-six harness checks pass (34 format, 32
apply). Three findings are recorded, none of which is a false write, a wrong
refusal or an unmet guarantee:

- **MEDIUM — three of the six fixtures the card names are absent from the
  suite** (unicode content, attributes, nested declarations). The parser-rung
  and coordinate fixtures the ADRs require are all present; the three missing
  ones are the card's addition, so this is a coverage gap rather than a broken
  claim.
- **LOW — a plan that names one path twice in `files[]` is accepted, applied
  correctly, and then reported with one record `not_attempted`** — a status the
  report reserves for a file no mutation was attempted on. Reproduced.
- **INFO — `parse_unavailable` is unreachable through `plan-apply`**: a file
  with no grammar is refused earlier, by the locator, so the third post-apply
  verdict is verified at the parser level only.

No bar failed. Nothing in the language of the round claims a guarantee it does
not keep; the vocabulary fence held.

## The gates, as they ran

### 1. `plan_id` is content-addressed — PASS

`plan-replace-body alpha --file sample.py --text '    return 41' --print`, then
an independent recomputation: `json.dumps(document without plan_id,
keys sorted, separators=(',',':'), ensure_ascii=False)` hashed with sha256.

```
CHECK 1a plan_id == sha256(canonical bytes, plan_id absent): PASS
         printed sha256:5c59…, recomputed sha256:5c59…
CHECK 1b --print output IS the canonical bytes (sorted, no padding,
         no trailing newline): PASS — 1032 bytes, last byte '}'
CHECK 1c plan_id is the file name the prepare command uses: PASS
```

A separate canonicaliser was written for the check; it never calls the
product's own.

### 2. Three prepare commands write one plan file; `--print` writes nothing — PASS

Each of `plan-replace-body`, `plan-insert-symbol`, `plan-delete-declaration`
run twice against a plan-dir snapshot (name → sha256): the writing form adds
exactly one file, the `--print` form adds none. In a second project that has
never held a plan dir, `--print` does not even create the directory.
`plan-rename-symbol` is refused with exit 2 and adds nothing.

### 3. `plan-validate` and `plan-show --diff` write nothing — PASS

Hashes over the plan dir (every file in it), the touched source file, and the
manifest triple (`manifest.db`, `manifest.db-wal`, `manifest.db-shm`) taken
before and after a batch of three `plan-validate` and two `plan-show --diff`
calls: **identical**. The positive control — a prepare command, which is a
write — changes the plan-dir hash, so the method would have noticed.

Measured in a throwaway project registered under an isolated `INDEX_ROOT`, so
the standing watcher cannot write into it; it was left running and was never
frozen.

### 4. The refusal matrix — PASS, all ten cases, each with its own exit code

| case | exit | first line |
|---|---|---|
| path escapes the project (absolute) | 2 | `PathEscapesProject: plan path '/etc/passwd' is absolute; …` |
| path escapes the project (`..`) | 2 | `PathEscapesProject: plan path '../outside.py' contains '..'` |
| non-UTF-8 | 1 | `NotUtf8: bad.py: byte 5 of the body is not UTF-8 (invalid continuation byte); a plan never lossy-decodes` |
| mixed newlines | 1 | `MixedNewlines: mixed.py mixes line terminators (b'\n', b'\r\n')` |
| a plan encoding `\r` | 2 | `CarriageReturnInPlan: replacement text carries a carriage return` |
| newline mismatch | 1 | `NewlineMismatch: sample.py: the file uses lf terminators but the plan declares crlf` |
| overlapping edits | 2 | `OverlappingEdits: sample.py: lines 2-2 and 1-2 overlap; an overlap is refused, never ordered` |
| two insertions at one point | 2 | `AmbiguousOrder: sample.py: two insertions at the boundary before line 4 …` |
| stale file | 5 | `PlanStale: stale_case.py: the file's hash is sha256:afea… but the plan was prepared against …` |
| ambiguous node | 2 | `AmbiguousNode: dup.py:dup (function) matches 2 nodes …: dup.py:1-2 (…); dup.py:5-6 (…)` |
| `semantic_required: true` | 3 | `SemanticProviderUnavailable: … no semantic provider exists in phase 1` |

The ambiguous-node line lists both candidates with their ranges, as ADR-0002
requires. Exit codes reuse the existing taxonomy; no number was added.

### 5. Coordinates and encoding, as ADR-0004 section 3 states them — PASS

```
5a  1-based inclusive lines: alpha's body is lines 2-2
5b  the terminator is not part of the line: expected_old_text == '    return 1'
5c  start_byte/end_byte agree with the stored bytes: 13-25 == b'    return 1'
5d  a corrupted start_byte (+1) is caught by arithmetic:
      PlanFormatError: sample.py: edit 0 records start_byte 14 but line 2
      starts at 13                                   (exit 2)
5e  BOM outside every range: file bytes begin ef bb bf, the target starts at
      offset 0 (the first byte after it), the body edit at 13
5f  a final line with no terminator is legal: end_byte == 25 == the byte length
5g  `position: end` carries no line number: edit keys
      ['kind','new_text','path','position']
```

### 6. The six fixtures the card names — PASS as an inventory, with a finding

Searched the suite that this round added (`tests/test_plan_format.py`,
`tests/test_plan_apply.py`, `tests/test_partial_apply.py`, `tests/apply_rig.py`):

| fixture | in the suite? | where |
|---|---|---|
| CRLF file | **yes** | `test_crlf_is_declared_and_lf_replacement_text_is_kept_lf` (test_plan_format.py:331) |
| LF file | **yes** | the default fixture text, `TWO_FUNCTIONS` (test_plan_format.py:43) |
| unicode content | **no** | no non-ASCII byte anywhere in the four files |
| attributes | **no** | no attribute or decorator fixture in the plan tests |
| nested declarations | **no** | no class-with-method fixture in the plan tests |
| end of file | **yes** | `test_insertion_after_the_last_line_is_position_end_with_no_line` (:395) and `test_insert_after_the_last_declaration_is_position_end` (:674) |

A UTF-8 byte-order mark fixture is also present (test_plan_format.py:319),
which the card did not name. The three absences are finding F1.

### 7. Ordering, verified from behaviour — PASS

```
7a  descending start_line within a file: a two-edit plan applied to a
    throwaway file produces bytes equal to an INDEPENDENT descending byte
    splice (offsets spliced by start_byte, highest first); the report shows
    lines 5-5 then 2-2
7b  across files ascending path: a plan whose document order is z-then-a
    applies a-file first, report order ['- za_first.py: applied',
    '- zb_second.py: applied']
7c  adjacency is legal: two adjacent deletes (lines 1-2, then 3-4) validate
    → exit 0
```

Overlap and the two-insertions case are refused, per bar 4.

### 8. A changed file refuses the whole plan, zero mutations — PASS

A two-file plan on `a_one.py` and `b_two.py`; `a_one.py` is rewritten after
the plan is sealed.

```
$ code-indexer plan-apply sha256:<plan> --project gateapply
PlanStale: a_one.py: the file's hash is sha256:e4bb… but the plan was
prepared against …; the plan is stale and is refused whole
exit 5
```

`a_one.py` byte-identical to its changed bytes; `b_two.py` byte-identical to
its pre-run bytes; **keeper's journal for this plan id holds zero entries**
(`{"count": 0, "operations": []}`), which is the zero-mutation proof — the
later file was never attempted and no keeper call of any kind was made.

### 9. Per-file atomicity through the guard, and the report — PASS

Keeper was reached through a logging shim on `PATH` that records every argv
and then `exec`s the real keeper 0.1.8, so the call shape is observed rather
than inferred.

```
keeper file replace-lines --start=5 --end=5 --replacement=… \
  --expected-hash=sha256:854d… --expected-content=… -- pair.py
(…and two more, one per edit; every mutation carries --expected-hash;
 --expected-content present on all three, since all three plan regions fit
 the 8 KiB cap)
```

Three edits, three mutation calls, one probe call (`replace-lines --help`).
The report carries `plan_id`, `identity.verified: "structural"`,
`basis: [file_hash, decl_hash, region_hash]`, `partial: false`, a status per
file and, per edit, `start_line`/`end_line`/`operation_id`.

### 10. Partial failure at file 3 of 5 — PASS

Five files, one edit each; the shim injects exactly one keeper failure at
`c_three.py` (documented fault injection at the subprocess boundary, labelled
as such in the capture).

```
$ code-indexer plan-apply sha256:<plan> --project gateapply --json
exit 1   ErrorType: PartialApply   partial: true
files: [('a_one.py','applied'), ('b_two.py','applied'),
        ('c_three.py','failed'), ('d_four.py','not_attempted'),
        ('e_five.py','not_attempted')]
bytes: a and b written; c, d and e byte-identical to before the run
report on stdout: yes (a full JSON report, not a bare error line)
```

The failed edit carries keeper's own code and message
(`replace_body …: IO_ERROR: injected failure`).

### 11. Recovery — PASS

```
11a operations in application order [op_0e7a…, op_a40c…, op_0742…];
    the report's undo list is that list REVERSED
11b keeper history undo in reverse restores both files byte-for-byte
11c a refused undo (the path changed after the operation) via apply.recover():
    stopped=True, steps=1, code=UNDO_CONFLICT — reported, never retried
```

### 12. Keeper absent, and a keeper without the guard flag — PASS

```
$ PATH=/usr/bin:/bin code-indexer plan-apply sha256:<plan> --project gateapply
KeeperUnavailable: keeper is not on PATH, and this command applies a plan
through filekeeper only: install filekeeper 0.1.8 or newer -- `uv tool install
filekeeper-cli` …
exit 3        (the file is byte-identical afterwards: no fallback write)

$ PATH=<stub whose `replace-lines --help` lacks --expected-hash> …
KeeperCapabilityMissing: `keeper file replace-lines --help` does not advertise
--expected-hash, so this keeper build cannot …
exit 3        (nothing written)
```

The capability probe is by flag, not by version, and it fires before the first
mutation.

### 13. The journal reconstructs the application; the receipt matches — PASS

```
13a keeper history list --task-id sha256:<plan> → 3 entries, no receipt read
13b every entry's actor is code-indexer
13c every mutation argv carries --task-id <plan_id> and --actor code-indexer
13d the printed receipt path exists and its plan_id and files[] equal the
    report's
```

### 14. The post-apply verdict is the parser's — PASS, with one caveat

```
14a a replacement that leaves the file unparsable → 'broken.py parse_error at
    line(s) …'   (the chunker's fallback cannot produce an error verdict)
14b a good result → 'pair.py parsed', 'd_four.py parsed'
14c ts_chunker.parse_tree("notes.txt", …) → (None, 'parse_unavailable')
14d no plan can be PREPARED for a grammarless file: exit 5 LocatorMismatch,
    "the file has no AST extractor, so no node …" — so parse_unavailable is
    unreachable through plan-apply (finding F3, informational)
14e default run: refresh: skipped; the reparse wrote nothing
14f with --refresh: refresh: ran
```

### 15. Vocabulary fence — PASS

`rg -in 'atomic|transaction|all-or-nothing'` over `src/`, the docs this round
touched (`docs/adr/0004…`, `docs/adr/0005…`, `docs/json-schema.md`,
`SKILL-CLI.md`, `CHANGELOG.md`) and both leg records.

- **New lines in the whole round diff containing any of the three words: one**
  — `src/code_indexer/command_classes.py:61`, "Plan reads write nothing:
  validate and show open no transaction" — a **negation** about the read path.
- `src/code_indexer/store.py:4` and `manifest.py:439`, `:502`, `:800` use
  "atomic" about SQLite row replacement. All four are pre-existing (untouched
  by the round's diff) and describe storage, not the apply path.
- `SKILL-CLI.md:126`, `:852` and `CHANGELOG.md:295`, `:697` say "transaction"
  about unrelated commands (read paths, `--apply` counting) and predate the
  round.
- The nine hits in `docs/adr/0005-filekeeper-apply-and-recovery.md` are the
  ADR's own statements and refutations, which the bar excepts: section 1 states
  a **per-file** observable and denies cross-file atomicity in the same breath
  ("there is no cross-file atomicity anywhere", and "no output of this feature
  says 'atomic', 'transaction' or 'all-or-nothing'"); the context table records
  a measurement of keeper; the alternatives section rejects two designs *for*
  being atomic; the requirements section forbids claiming atomicity. None is a
  claim that this feature applies several files as one unit.
- Both leg records: the plan-format record has no hit; the apply record has one,
  and it is the sentence asserting the absence.

No output, help text, docstring or CHANGELOG line of the feature claims
atomicity.

### 16. No test mutates this checkout — PASS

Every write in the four new test files resolves to a `tmp_path` root:
`sample(root, …)` writes under the caller's `tmp_path`; the plan dir in the
unit tests is `tmp_path/.code-indexer/plans`; `tests/apply_rig.py` builds the
project at `tmp_path/proj` and points `INDEX_ROOT` at `tmp_path/state`; the
path-escape test's "outside" file is `tmp_path/outside.py`, still inside the
temporary directory. There is no `Path(__file__)`-relative write and no
reference to a checkout root in any of them. The suite's own plan dirs are all
under tmp.

### 17. `uv run pytest tests/ -q -W error` — PASS

```
811 passed, 2 deselected in 99.65s (0:01:39)   exit 0
```

Warn-clean under `-W error`. Matches the dev's report exactly.

### 18. `uvx ruff check src tests scripts` — PASS

```
All checks passed!   exit 0
```

### 19. Publishing hygiene — PASS

```
$ uv run python scripts/publishing_hygiene_lint.py
publishing hygiene: scanned=722 skipped=0 violations=0 (untracked files are out of scope)
exit 0

$ (the same linter, --paths, over this gate's own artifacts)
publishing hygiene: scanned=17 skipped=0 violations=0
exit 0
```

Every capture in `runs/round4-gate/` was masked before it was written: the
project root, the print-only control project, the index root, the work
directory, the repository root and the account home are `<project-root>`,
`<printonly-root>`, `<index-root>`, `<work-dir>`, `<repo-root>` and `<home>`.
No card id and no internal profile name appears in this directory.

### 20. `SKILL-CLI.md` is byte-identical to the installed skill — PASS

```
3ec26040c36544800ad8f707da16c7ae28af9a2664f55c45993b9deecfa97fb5  SKILL-CLI.md
3ec26040c36544800ad8f707da16c7ae28af9a2664f55c45993b9deecfa97fb5  <home>/.hermes/skills/code-indexer/SKILL.md
```

### 21. Scope fence — PASS

- **Schema: unchanged.** No commit in the range touches `manifest.py` or any
  schema module.
- **`EXTRACTION_VERSION`: unchanged** (`= 5`, `src/code_indexer/graph_extract.py:99`).
  The only `ts_chunker.py` change splits `parse_tree` out of `parse_file` so one
  parse decides both; no version constant moved.
- **No reindex** was run against this checkout.
- **The roster gains exactly one command**: 33 → 34 registrations, and the name
  diff between `4afe05b` and `055e635` is one added line,
  `@app.command(name="plan-apply")`. `command_classes.py` adds `plan-apply` to
  the write-only set and nothing else.
- **The commands the round claims exist do exist**: `plan-replace-body`,
  `plan-insert-symbol`, `plan-delete-declaration`, `plan-validate`,
  `plan-show`, `plan-apply`, plus the reserved `plan-rename-symbol` which
  refuses. Each carries `--json`; the `--help` of all six was read.

### 22. Signatures, tree, remote, CI — PASS

```
$ git log --format='%h %G?' fb932ff..055e635   → 15 commits, every one 'G'
                                                (0 unsigned)
$ git status --porcelain --untracked-files=no  → empty
$ git ls-remote origin main                    → 055e6359b9a25b37a6344cc636e54d66fe20e1bf
$ gh run list --limit 3                        → 42316453782 055e635 ci completed success
```

The gate's own push and the CI run it starts are reported to the dispatcher;
the commit that carries this record is signed and lands on `origin main`.

## Findings

### F1 (MEDIUM) — three of the six fixtures the card names are not in the suite

`tests/test_plan_format.py` (whole file), and the three other new test files.
The suite covers CRLF, LF, the byte-order mark, end of file, mixed terminators,
non-UTF-8 input, overlap, adjacency and the ambiguity refusal — every fixture
the ADR's Consequences list requires. It has **no unicode-content fixture, no
attribute/decorator fixture and no nested-declaration fixture**: `rg -n
'[^\x00-\x7F]'` over the four files returns nothing, and no fixture declares a
class whose method is the edit target.

Why it matters: the locator's `qualified_name` for a nested declaration
(`Container.method`) and the byte geometry of a multi-byte line are exactly the
places where a line/byte coordinate can be wrong while every ASCII test stays
green. The rules are stated correctly and verified by arithmetic; they are not
witnessed on non-ASCII or nested input.

Reproducer: `rg -c '[^\x00-\x7F]' tests/test_plan_format.py tests/test_plan_apply.py
tests/test_partial_apply.py tests/apply_rig.py` → 0 in each. Proposed failing
test: a `replace_body` plan on a method inside a class, in a file whose other
lines carry non-ASCII text, asserting the applied bytes and the report's
`parsed` verdict.

Owner: the Python developer; not this gate's to fix.

### F2 (LOW) — a duplicated `files[]` record is accepted and reported as `not_attempted`

`src/code_indexer/apply.py:379-382` builds one `FileOutcome` per `files[]`
record and then keys them by path, so a document naming one path twice yields
two records, and only the last receives the edits. The first stays
`not_attempted` although the file was written. `_check_schema`
(`src/code_indexer/plans.py:766`) counts the records ("2 file(s)") and
`_check_file_preconditions` prints the path twice, without refusing the
duplicate; `_check_paths` dedupes ("1 path(s)").

Reproduced (`runs/round4-gate/repro-duplicate-file-record.txt`):

```
$ code-indexer plan-validate <plan> --project dupeproj     → exit 0, passed
$ code-indexer plan-apply <plan> --project dupeproj --json  → exit 0
files: [{'path': 'sample.py', 'status': 'not_attempted', 'edits': []},
        {'path': 'sample.py', 'status': 'applied',
         'edits': [{'kind': 'replace_body', 'start_line': 2, 'end_line': 2,
                    'operation_id': 'op_db8c1a2d2bb04f5f'}]}]
partial: False
records reported not_attempted while the file WAS written: 1
```

Severity is low because every command this round ships writes one record per
path, and a hand-edited plan fails its own hash. It is still a report whose
status vocabulary contradicts what happened, reachable by any tool that builds
the document directly. Suggested fix: refuse a repeated `path` in `files[]` at
schema check (one path, one record), which makes the report's statuses
unambiguous by construction.

### F3 (INFO) — `parse_unavailable` cannot be reached through `plan-apply`

The post-apply verdict is the parser's, as required, and it is exercised for
`parsed` and `parse_error`. The third verdict is not reachable: a plan cannot
be prepared for a grammarless file, because `declarations()` yields nothing and
the reader refuses first (`LocatorMismatch`, exit 5, "the file has no AST
extractor, so no node …") — see bar 14d. The verdict itself is correct at the
parser level (`ts_chunker.parse_tree` returns `(None, 'parse_unavailable')`),
so the ADR's statement stands; it is the apply path that can never observe it.
Worth knowing before someone cites "a file whose language has no grammar
reports parse_unavailable" as an apply-path behaviour.

## What I did not verify

- **The CI workflow's own content.** I ran the suite locally and watched the
  tip's CI run conclude success; I did not audit `.github/workflows/ci.yml` or
  prove that CI runs the same commands this gate ran.
- **Windows or macOS behaviour**, and any tree-sitter grammar other than the
  Python one the fixtures use. All live applies were on Python files.
- **The concurrency window between the pre-pass and keeper's hash guard.** The
  guard was seen to carry a hash on every call and a mid-loop refusal was
  reproduced by injection; an actual concurrent writer racing the pre-pass was
  not instrumented.
- **CRLF through the apply path.** CRLF is verified at the plan/validate level
  (declaration, translation of the replacement block) and by the suite; I did
  not run a live `plan-apply` against a CRLF file with the real keeper.
- **`--refresh`'s index pass beyond its report line.** I observed `refresh: ran`
  and that the default skips; I did not measure what the incremental pass
  indexed.
- **MCP exposure.** None exists for any plan command, and I confirmed none was
  added; I did not test MCP surfaces generally.
- **Performance.** No timing of prepare, validate or apply was measured.

## Captures

- `runs/round4-gate/format_capture.py` → `format-capture.txt` (bars 1-5, 7;
  34 checks, all PASS)
- `runs/round4-gate/apply_capture.py` → `apply-capture.txt` (bars 8-14; 32
  checks, all PASS)
- `runs/round4-gate/repro-duplicate-file-record.py` →
  `repro-duplicate-file-record.txt` (F2)
- `runs/round4-gate/standing-bars.txt`, `pytest.txt`, `scope-raw.txt`,
  `bar6-fixtures.txt`, `bar6b.txt`, `bar6c.txt`, `bar15-16.txt`, `bar15b.txt`,
  `bar16b.txt`, `bar21-scope.txt`, `bar21c.txt`, `hygiene-paths.txt`

All host paths are masked at the point of printing, in the scripts themselves,
so a re-run reproduces the masked form rather than needing a scrub afterwards.
