# Round 5 verification gate -- a provider behind a contract, and an agent surface that fits

Card id withheld (an authored record is hygiene-governed; the dispatch brief carries it).
Base `1177458`, tip `5d2771a`. Tree `<checkout>`.
No language server was installed, spawned or probed by this gate: `command -v` finds none of
`clangd`, `pyright-langserver`, `pylsp`, `roslyn-language-server`, `OmniSharp`, `csharp-ls`,
`gopls`, `typescript-language-server` (capture `tooling.txt`). Every run below is fresh, by me,
in this session.

## Verdict

**The provider contract and the agent surface both hold.** Fifteen of the sixteen bars pass as
written; bar 14 fails on its last clause only -- the diff outside the declared set is **not empty**: two
files, thirteen lines (`src/code_indexer/plans.py`, `tests/test_plan_format.py`). That change is
the one bar 6 requires (a reserved operation must refuse as `UnsupportedOperation` at exit 2), so the
breach is in the brief's fence, not in the work; it is reported as finding F1, severity low, and my
recommendation is acceptance rather than a fix round.

Both declared deviations are **honest**: the red commit is real, declared in the leg record, and fixed
by a production-code guard rather than by a weakened test (bar 16a); the one-commit agent surface is
the true shape of the work, and nothing was held back to the end (bar 16b).

Findings: 1 low (scope fence), 1 low (test-message robustness), 2 info (README link count, stale
worktree). No high or medium finding. No fabricated pass is claimed anywhere below: every number in
this record is printed by a capture under `runs/round5-gate/captures/`.

## Provider contract (ADR-0006)

### 1. PASS -- the contract suite passes with no server installed

    uv run pytest tests/test_provider_contract.py -q
    20 passed in 0.15s

With all eight server binaries absent from `PATH` (`tooling.txt`). Capture `refusals.json` was produced
by my own probe, not by the suite, and reaches the same paths independently.

### 2. PASS -- refusals by behaviour, each with its exit and its `ErrorType`

My probe drives the provider session directly (capture `refusals.json`):

| shape | exception | `ErrorType` | exit | observed |
|---|---|---|---|---|
| absent provider | `ProviderUnavailableError` | `ProviderUnavailable` | **3** | one line naming the install route: `Install route: uv tool install python-lsp-server (or: uv tool install pyright)` |
| unsupported operation | `UnsupportedOperationError` | `UnsupportedOperation` | **2** | refused before the provider was asked; no edit exists |
| provider answers nothing in time | `ProviderTimeoutError` | `ProviderTimeout` | **3** | returned in 0.05 s against a 0.5 s sleep, `cancelled` recorded |
| provider never becomes ready | `ProviderTimeoutError` | `ProviderTimeout` | **3** | "did not become ready within 0.05s" |

The capability report, as data:

    {"provider": {"name": "stub-fixture", "version": "1.0.0", "transport": "in-process"},
     "operations": {"resolve_symbol": "supported", "find_references": "supported",
                    "prepare_rename": "unsupported"}}

`prepare_rename` is `unsupported` -- the accepted risk ADR-0006 section 2 names. `unknown` is a third
value and is not a denial: with `find_references` set to `unknown`, `state()` returns `unknown`
(`unknown_is_denial: false`) and the request is still attempted and answered
(`unknown_attempt_still_answered: true`).

### 3. PASS -- lazy start, isolation, and no provider in the index

**Lazy start and one session per project** (capture `lifecycle.json`): constructing a session calls the
factory zero times and leaves `_instance is None`; the first semantic request starts it exactly once
(`starts: 1`); `session_for(project)` twice returns the *same* object while a different root returns a
different one; the request executor has one worker; after `close()` the session is closed and a further
call raises `ProviderUnavailableError`; `close_all` is registered with `atexit`.

**Every syntax-only command is untouched**, tested the decisive way (capture `isolation.json`): ten
commands (`overview`, `skeleton`, `outline`, `find-symbol`, `find-definition`, `find-references`,
`deps`, `changed-symbols`, `plan-validate`, `plan-show`) were run once with no provider registered, then
again after a provider factory was registered that *raises if it is ever constructed*. Results:

    identical_to_pass1: true      factory_calls: 0
    provider module loaded by the CLI at all: false

Corroborating static fact: no production module imports the provider at all -- `rg` over `cli.py`,
`core.py`, `plans.py`, `server.py` for `refactoring_provider|session_for|ProviderSession` returns
nothing (exit 1). The provider cannot be reached from a command that does not ask for it.

**No provider identifier in the index**: the probe scanned every column name and every distinct value
of every table in a real manifest built by this round's code (capture
`manifest-provider-scan.json`) for `provider`, `lsp`, `langserver`, `stub-fixture`, `clangd`, `pylsp`,
`pyright`, `roslyn`: `provider_column_hits: []`, `provider_value_hits: []`. The manifest schema is
unchanged at v7 and `EXTRACTION_VERSION` is untouched (bar 14).

### 4. PASS -- position conversion in one module; the five cases and the boundary test

`rg` for UTF-16 handling outside the conversion module returns no arithmetic: the only hits are
(a) docstrings in `refactoring_provider.py` that state where the conversion lives,
(b) the `ProviderPosition` boundary type that holds the provider's raw units, and
(c) the word "surrogate" in `manifest.py`/`cli.py`, which is the unrelated revision-scoped row id.
`provider_positions` is imported by exactly one production module (`refactoring_provider.py`).

Driven independently (capture `positions-and-edits.json`), all five fixed cases:

| case | observed |
|---|---|
| astral plane | unit 5 -> byte 5, unit 7 -> byte 9 (2 units, 4 bytes); reverse byte 5 -> unit 5, byte 9 -> unit 7 |
| CRLF | line 2 starts at byte 9; an offset on the terminator is refused |
| byte-order mark | `has_bom: true`, offsets are body-relative (unit 4 -> byte 4) |
| empty last line | `lines: ["one", ""]`, its start at byte 4; `(1, 1)` refused |
| offset inside a surrogate pair | unit 6 -> `PositionConversionFailed` |

Boundary test firing in both directions: reverse direction on byte 6 (inside the astral character's four
bytes) -> `PositionConversionFailed`, "lands inside a multi-byte character, so it is not a byte boundary
of the decoded line".

### 5. PASS -- provider edits become line-aligned plan edits

From the same capture, against `mod.py` = `def greet(name):` / `    return value`:

- whole-line expansion preserves bytes outside the range: a rename of `greet` yields one record,
  lines 1-1, bytes 0-16, `expected_old_text: "def greet(name):"`, `new_text: "def hello(name):"`;
- two edits on one line merge descending: `return` -> `yield` and `value` -> `other` give a single
  record with `new_text: "    yield other"`;
- `expected_old_text_hash` covers the whole old line: the record's hash equals the `sha256` of the bytes
  the record names, and `expected_old_text` reproduces them (`equal: true`);
- an edit crossing a terminator is refused `NonLineAlignedProviderEdit` (lines 1-2);
- overlap is refused `OverlappingEdits`.

`plan-validate` on a document built from the materialized edit (capture `plan-validate.txt`):

    "passed": true      EXIT=0
    region hashes: 1 region(s) hash to the bytes the plan recorded
    line and byte cross-check: every recorded line and byte offset agrees with the stored bytes
    syntax verdicts: mod.py parsed

and one it refuses -- the same materialized edit under `operation: rename_symbol`: `"passed": false`,
`EXIT=2`.

### 6. PASS -- a rename plan is refused, exit 2, with no text edit written

    code-indexer plan-validate <rename plan> --project <throwaway project>
    EXIT=2
    UnsupportedOperation: operation 'rename_symbol' is reserved in the plan format and refused in
    phase 1: no structural relabelling of a symbol is offered, because references are textual and
    nothing would verify the rename

The target file's `sha256` was identical before and after, and the project directory gained no file
(`sha256sum -c`, `diff <(ls ...)` in `plan-validate.txt`). And:

    code-indexer --help | rg -c rename-symbol     -> 0

## Agent surface (ADR-0009)

### 7. PASS -- budgets, measured by the test and by me, with the measured size in the failure

| file | lines / bytes | budget |
|---|---|---|
| `SKILL-CLI.md` | 118 / 6,506 | 120 / 8,192 |
| `references/indexing.md` | 287 / 12,911 | 400 / 24,576 |
| `references/query.md` | 309 / 14,563 | 400 / 24,576 |
| `references/plans.md` | 204 / 10,595 | 400 / 24,576 |
| `references/machine-output.md` | 141 / 6,224 | 400 / 24,576 |
| `references/failures.md` | 108 / 6,404 | 400 / 24,576 |

(`wc -l -c`, capture `docs-suite.txt`; 120 x 1024 = 122,880 bytes and 8 x 1024 = 8,192 bytes per
ADR-0009, checked with `calc`.) The failure message prints the measured size -- proved on a temp copy:
appending five lines to an entry copy makes the check fail with `SKILL-CLI.md is 123 lines / 6511
bytes; the budget is 120 lines / 8192 bytes`, and 300 appended lines in a reference gives
`skill/references/failures.md is 408 lines / 6704 bytes; the budget is 400 lines / 24576 bytes`
(capture `drift-checks.json`).

### 8. PASS -- all six drift checks, each proved able to fail

`uv run pytest tests/test_docs.py -q` -> `34 passed, 1 skipped`; the skip is check 6 in the household,
whose installed copy is still the legacy single file (`docs-suite.txt`).

Able-to-fail exercise (capture `drift-checks.json`): a temp copy of the whole surface, one mutation at a
time, the named check re-run, the tree restored, and each check also proved to pass on the unmutated
copy before and after (so a failure cannot be an artefact of the rig):

| # | mutation | the check failed with |
|---|---|---|
| 1 | one roster class edited (`changed-symbols` read -> write) | `roster class disagrees with ADR-0003 section 1: {'changed-symbols': ('write','read')}` |
| 2a | a reference naming a nonexistent file | `the entry document names references that do not exist: ['ghost.md']` |
| 2b | a file nothing names | `reference files the entry document never names: ['extra.md']` |
| 2c | a command covered by none | `registered commands no reference documents: ['overview']` |
| 2d | a command covered twice | `commands documented by more than one reference: {'overview': ['failures.md','query.md']}` |
| 3 | budgets | the two measured-size messages above |
| 4 | one exit code dropped | `exit codes missing from the entry table: [4]` |
| 5 | one generated block hand-edited | `generated blocks differ from scripts/generate_skill_docs.py output: ['roster in SKILL-CLI.md']` |
| 6 | installed copy: equal / one byte / legacy | see below |

Check 6 against a throwaway skills root (`CODE_INDEXER_SKILLS_ROOT`, capture `installed-copy.txt`), never
the household directory:

    equal copy      -> 3 passed, 32 deselected
    one byte appended to one reference -> 1 failed, 2 passed (digest 0da5ab.. vs 68f2aa..)
    legacy single file -> 2 passed, 1 skipped: "is not installed in this shape (a directory holding
    SKILL.md and references/); a legacy single-file copy cannot carry the references"

The household skip is the expected local state: the installed skill is still the legacy one-file copy
(this gate read that directory's listing only, and wrote nothing there).

### 9. PASS -- coverage arithmetic and the entry document's contents

My own count, from the click registry and the reference headings (capture `coverage.json`):

    registry_count: 33
    sections_per_reference: indexing 11, query 16, plans 6, machine-output 0, failures 0
    covered: 33   doubled: {}   uncovered: []   orphan_sections: []

33 = 11 + 16 + 6 (`calc`), each command in exactly one reference. The entry document carries the
decision tree (15 question shapes), the roster with classes, the exit table with the `ErrorType` rule,
the staleness rule in three sentences, the read-only guarantee in one sentence, and the reference index
with one trigger each -- and none of what belongs in a reference: no per-command flag lists, no examples,
no plan workflow beyond the one-line pointer, no provider behaviour, no MCP parity table.

### 10. PASS with a note -- the MCP parity list is in `README.md`; every remaining link resolves

`README.md` carries `### CLI ↔ MCP parity`: eight CLI subcommand -> MCP tool rows, the removed list, the
CLI-only list and the read-only claim. **The link count is two, not five.** Both are
`[SKILL-CLI.md](SKILL-CLI.md)` and both resolve (the file is at the repository root). The base had five,
and two of those five were anchor links into `#key-rules` and `#subcommand-reference` -- headings the
118-line document no longer has, so keeping them would have produced dead anchors. Reported as finding
F2 (info): the bar's count is stale, the substance (no dangling link) holds.

### 11. PASS -- `--json` on the four pass commands

All four, on a throwaway project with a throwaway index root (capture `pass-json.txt`), each `EXIT=0`
and each a pass result as data:

    add-project      {"schema": 1, "command": "add-project", ..., "index": {"slug": ..., "state": "idle",
                      "result": {"files_indexed": ..., ...}}}
    index-more       {..., "scope_include": ["lib"], "index": {"slug": ..., ...}}
    reindex-project  {..., "vectors": "off", "index": {...}}
    remove-project   {..., "removed": true, "collections": [], "alias": "idx_...", "alias_dropped": false,
                      "notes": ["no vector collection to drop"]}

Documented in `docs/json-schema.md` under `## add-project / index-more / remove-project /
reindex-project --json` (line 241), and covered by `uv run pytest tests/test_pass_json.py -q` ->
`7 passed`, with a named test per command asserting the pass result as data
(`test_add_project_json_reports_the_registration_and_the_pass`,
`test_index_more_json_reports_the_scope_and_the_pass`, `test_remove_project_json_reports_the_drop`,
`test_reindex_project_json_reports_the_rebuild`).

## Standing bars

### 12. PASS -- the whole suite, warn-clean

    uv run pytest tests/ -q -W error
    884 passed, 1 skipped, 2 deselected in 102.83s      EXIT=0

It matches the developer's report exactly (884 / 1 / 2), captured before I touched anything else.

### 13. PASS -- lint, hygiene, generator

    uvx ruff check src tests scripts                     -> All checks passed!   EXIT=0
    uv run python scripts/publishing_hygiene_lint.py     -> violations=0         EXIT=0
    uv run python scripts/generate_skill_docs.py --check -> (no output)          EXIT=0

(`tooling.txt`. The hygiene number re-read after this record and its captures were written is in the
closing section.)

### 14. FAIL on one clause -- the scope fence

Holds: no schema change (manifest `SCHEMA_VERSION` still `"7"`, no diff in `manifest.py`/`store.py`/
`indexer.py`), no reindex, no command added or removed (33 registered, `coverage.json`).

Does not hold: the diff outside the declared set is **not empty**.

    git diff --stat 1177458 5d2771a -- . <excludes for the declared set>
     src/code_indexer/plans.py    | 7 ++++++-
     tests/test_plan_format.py    | 9 +++++++--
     2 files changed, 13 insertions(+), 3 deletions(-)

The change replaces `PlanFormatError` with `UnsupportedOperationError` in `_check_schema` for a
reserved operation, and updates the one test that asserted the old type. It is exactly what bar 6
requires (exit 2, `UnsupportedOperation`, no text edit), it is described in the provider leg's record,
and it changes no schema and no command -- but the fence as written does not list these two files.
Finding F1, low.

### 15. PASS -- history, tree, remote, CI

    git log --format='%H %G? %an' 1177458..5d2771a   -> ten commits, every %G? = G, author sangingeum
    git status --porcelain --untracked-files=no      -> empty (tracked tree clean)
    git rev-parse HEAD origin/main                   -> both 5d2771afe3a39f09301cbbde4b819da9dd884d28
    git ls-remote origin main                        -> 5d2771afe3a39f09301cbbde4b819da9dd884d28 refs/heads/main

CI for that SHA: run `42324494052`, workflow `ci`, event `push`, status `completed`, **conclusion
`success`** (`gh run list --limit 12 --json ...`). The same listing shows runs for the base (`1177458`,
success) and for the follow-up (`b918fe1`, success); no run exists for the other intermediate SHAs (see
"what I did not verify").

### 16. PASS -- both declared deviations are honest

**(a) `becef85` was red.** I re-ran the affected suite on that commit in a detached worktree:

    becef85  tests/test_provider_contract.py -> 1 failed, 19 passed
             test_the_provider_is_terminated_at_close_and_no_daemon_survives
             Failed: DID NOT RAISE ProviderUnavailableError
    b918fe1  tests/test_provider_contract.py -> 20 passed

The leg record declares it ("**red until the follow-up below** ... a declared deviation, not a green
one") and the follow-up amended the record in the same commit. The fix is not a mask: `b918fe1` adds a
closed-session guard to `capabilities()` in the production module (`if self._closed: raise
ProviderUnavailableError(...)`), and the only test change in that commit is closing a file handle in the
source-scan test -- a `ResourceWarning` fix for `-W error`, not a relaxation of the failing assertion.
The assertion that failed at `becef85` is unchanged and now passes against the guard.

**(b) One commit instead of six slices.** `aaea01b` is the whole surface in one commit: entry document,
five references, generator, and `tests/test_docs.py` (nine files, +1,877/-1,241). Then `021585c` is
`README.md` only (+71/-34) and `5d2771a` is `CHANGELOG.md` + the leg record only (+172). Nothing was
held back to the end, and the stated reason is true rather than convenient: the split is red in smaller
pieces because the index-closure check fails on a dangling reference -- which I proved myself in the
able-to-fail exercise (mutations 2a and 2b), so the all-or-nothing claim matches the check's behaviour.
The one blemish in that commit is a four-line `README.md` touch, consistent with the file moving in the
next commit.

## Findings

**F1 -- low -- scope fence breached by two files outside the declared set.**
`src/code_indexer/plans.py` (+6/-1) and `tests/test_plan_format.py` (+8/-1) changed in
`1177458..5d2771a`; neither is in the brief's allowlist. Reproduce:
`git diff --stat 1177458 5d2771a -- src/code_indexer/plans.py tests/test_plan_format.py`.
Effect: a plan declaring `rename_symbol` now refuses as `UnsupportedOperation` (exit 2) instead of
`PlanFormatError`, which is what bar 6 asks for; no schema, command or behaviour change beyond that
refusal's taxonomy. Ruling: the work is correct, the fence was drawn too tightly -- accept, and add both
paths to the allowlist in the brief's next form.

**F2 -- info -- `README.md` carries two `SKILL-CLI.md` links, not five.** Reproduce:
`rg -n 'SKILL-CLI' README.md`. Both resolve; the three that went with the rewrite included two anchors
(`#key-rules`, `#subcommand-reference`) that the new 118-line document has no heading for. No dead link
exists; the bar's "five" is the pre-split count.

**F3 -- info -- a developer worktree is still attached.** `git worktree list` shows a detached worktree
under the system temp directory (commit `2f2dcd9`) besides the main tree. Harmless, outside this gate's
fence to remove, but it will confuse a later worktree sweep.

**F4 -- low -- one drift check's failure message cannot be built outside the checkout.**
`test_generated_blocks_match_the_generator` formats its failure with `path.relative_to(REPO)`; pointed
at a temp copy of the surface (as my able-to-fail rig does) it raises `ValueError` instead of a readable
`AssertionError`. Reproducer: override the module's entry/reference paths to a temp tree and hand-edit
one generated block. No effect on the real run -- the real paths are under the checkout -- but a gate
that cannot state its own failure is weaker than one that can.

## What I did not verify

- **No real language server was exercised.** The fence forbids installing one, and none is installed; so
the contract suite, the refusals and the capability report are proved against the stub only. A real
provider's compliance, and the "rename cannot be expressed" reversal rule of ADR-0006 section 2, remain
unproven until a server is installed.
- **The installed household skill copy was not refreshed**, and I did not run the release install. Check
6's skip is the expected pre-release state; the digest check has only been proved against throwaway
roots and a synthetic equal copy.
- **Per-commit CI for the provider leg's middle commits.** The remote listing has `ci` runs for `1177458`,
`b918fe1` and `5d2771a` only; `db2b5f8`, `467175b`, `9af08f0`, `becef85`, `9b92cea` and `2239b67` have no
run of their own, so "CI green" is verified at the tip and at the follow-up, not per commit.
- **`plan-apply` with a materialized edit.** Bar 5 asks for validation, and I validated; applying would
write sources, so the write path for a materialized plan is untested here.
- **The MCP relay** (`readOnlyHint`, stream split, exit-code preservation) was not exercised: it is
ADR-0007's surface and no round-5 bar asks for it.
- **No sanitizer or coverage matrix applies**: this is a Python round; there is no C++ or .NET artefact
in the diff. Coverage numbers were not measured because no threshold is declared for this project in
this round.
- **Performance and cost claims** (ADR-0006's workload table, ADR-0003's budgets) were not re-measured.

## Captures

All under `runs/round5-gate/captures/`, masked: absolute host paths were replaced by `<checkout>`,
`<scratch>`, `<skills-root>` and `<home>` with a declared header, nothing else changed; the unmodified
captures are kept in the local evidence store outside the repository.

`full-suite.txt`, `refusals.json`, `lifecycle.json`, `isolation.json`, `manifest-provider-scan.json`,
`positions-and-edits.json`, `plan-validate.txt`, `pass-json.txt`, `docs-suite.txt`, `drift-checks.json`,
`installed-copy.txt`, `coverage.json`, `tooling.txt`.

## Hygiene

    uv run python scripts/publishing_hygiene_lint.py
    publishing hygiene: scanned=793 skipped=0 violations=0

Re-run after this record and its captures were written, inside the commit that carries them; the number
above is read from that run.
