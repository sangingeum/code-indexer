# Fold verification for issues #24-#26

Narrow re-verification of the eight folds the review record specified
(`runs/issue24-26/review.md`, section "Fold list"). This is not a re-gate: the
full gate at `0221b30` passed and its bars are of record. Only the folded
surface is checked here.

- Base of the fold set: `bf6bcc0` (review record).
- Tip: `7ea6b63`, branch `main`, worktree clean apart from the pre-existing
  untracked `runs/…` captures and the QA document.
- Fold commits, all `%G? = G`: `e4dbcde` (wording and test-pin folds),
  `2972972` (wrapper prune), `7ea6b63` (the `symbol_rows` marker — the only
  behaviour change). Capture: `fold-captures/git-state.txt`.
- Verify-only: no code was changed and no GitHub issue was opened, commented on
  or closed.

Every figure below comes from a capture under `runs/issue24-26/fold-captures/`.
Two captures (`live-skeleton.redacted.json`,
`index-status-tip-fold.redacted.txt`) are redacted copies: the checkout path is
written `<checkout>`, nothing else was changed, and the unmodified captures sit
in the local evidence store outside this repository.

## Bar 1 — every fold is present

| Fold | Specified change | Present | Tip evidence |
|---|---|---|---|
| 1 — restore the merge verb in the CLI reference | `SKILL-CLI.md`: "ranges … that overlap or touch **are merged into one span per contiguous run**, so a decorated definition answers as one segment" | yes | `SKILL-CLI.md:560-562` (`fold-captures/diff-docs.txt`) |
| 2 — state the padding consequence | the three-sentence padding paragraph added after "Spans are listed in start-line order." | yes | `SKILL-CLI.md:377-380` |
| 3 — prune the single-span wrapper | `_symbol_range` deleted from `evalharness.py`; test retargeted to `_symbol_ranges` asserting `[(5, 6)]`; module docstring retargeted | yes | `evalharness.py:116-118` (no function), `tests/test_symbol_resolution.py:237-240`, `:7` |
| 4 — state the rule, not a shipped regression | the `_merge_symbol_ranges` adjacency paragraph rewritten to "so merging AFTER padding would fuse …" | yes | `CHANGELOG.md:44-50` |
| 5 — drop the two "stated in" pointers | both pointers deleted, both contract sentences kept | yes | `core.py:68` ("The merge is part of the answer, never a silent convenience."), `core.py:1233-1234` ("It is part of the answer, never a silent normalisation.") |
| 6 — make the merge visible | merge returns `(start, end, symbol_rows)`; segment builder adds `symbol_rows` only when `symbol` is set; docstring clause; `SKILL-CLI.md` clause; `docs/json-schema.md` block rewritten; `CHANGELOG.md` field sentence; three test pins | yes | `core.py:58-86`, `:1244-1246`, `:1284-1285`, `:1298-1312`; `core.py:1231-1233`; `SKILL-CLI.md:380-383`; `docs/json-schema.md:46-56`; `CHANGELOG.md:37-41`; `tests/test_symbol_resolution.py:362`, `:371`, `:424` |
| 7 — scope the differential | the "suffix branch was live behaviour" sentences replaced by the four-sentence scoped account | yes | `runs/issue24-26/gate-verification.md:69-78` |
| 8 — pin the leaf, not either form | `find_symbols("Sim", …)` asserted truthy and `find_symbols("Estate.Sim", …)` asserted empty, each with a message | yes | `tests/test_csharp_symbols.py:150-153` |

No fold is absent. Fold 8's record offered `substring=False` as a fallback if
the dotted read came back as a substring match; it is not needed — the default
is already `substring=False` (`manifest.py:292-294`) and both of the new
assertions pass (Bar 4).

## Bar 2 — fold 6's contract, live at the tip

Captures: `fold-captures/live-symbol-mode.json`, `live-line-mode.json`,
`live-key-comparison.txt`, `doc-json-schema-get-code-context.txt`.

| Mode | Command | Segments | Segment keys | `schema` |
|---|---|---|---|---|
| symbol | `get-code-context src/code_indexer/cli.py --symbol get_code_context --json --skip-stale-check --project code-indexer` | 1, span `1435-1452` | `start_line`, `end_line`, `lines`, `symbol_rows` (`= 2`) | 1 |
| line | same file, `--start-line 1435 --end-line 1440 --json` | 1, span `1435-1440` | `start_line`, `end_line`, `lines` | 1 |

Top-level keys in both modes: `file`, `stale`, `re_resolved`, `segments`,
`schema`. Only one key separates the modes: `symbol_rows`, and it is absent from
line mode. This reproduces the fold's self-report exactly (one segment
`1435-1452`, `symbol_rows: 2`).

Documentation compared key by key against the live payload:

| Documented | Emitted, symbol mode | Emitted, line mode |
|---|---|---|
| `schema` | yes (`1`) | yes (`1`) |
| `file` | yes | yes |
| `stale` | yes | yes |
| `re_resolved` | yes | yes |
| `segments[]` | yes | yes |
| `start_line`, `end_line` | yes | yes |
| `lines` | yes | yes |
| `symbol_rows` (symbol mode only) | yes | correctly documented as absent |
| `error` instead of `lines` past end of file | documented; path confirmed by reading `core.py:1304-1308`, where `error` is set and `continue` skips `lines` | same path |

The block no longer describes the retired `text` / `symbol|null` shape; prose
below it states the `lines` rendering, the `error` substitution, and that
`symbol_rows` is additive with `schema` staying `1`.

Additive-ness:

- `outline --json` parses, `schema: 1`, keys `declarations`, `file`, `schema`
  (`fold-captures/live-outline.json`).
- `skeleton --json` parses, `schema: 1`, keys `files`, `project`, `schema`
  (`fold-captures/live-skeleton.redacted.json`).
- No `schema` bump anywhere in the fold diff (Bar 5).

## Bar 3 — the prune is complete

Capture: `fold-captures/prune-search.txt`.

- `rg -n '_symbol_range\b' src tests` → **no match**: no definition and no call
  of the pruned wrapper survives.
- `rg -n '_symbol_range([^s]|$)' src tests` → the only hit is the test function
  name `test_eval_symbol_range_resolves_a_row_past_the_cap`
  (`tests/test_symbol_resolution.py:232`). A test name is neither a definition
  nor a call of the helper; the record did not ask for a rename, so this is
  noted, not counted against the fold.
- `_symbol_ranges` is the only read of a label's rows in the scoring path:
  defined at `evalharness.py:100`, called once at `evalharness.py:134`, and
  uncapped — `manifest.find_symbols(symbol, substring=False, file=rel_file,
  limit=None)` (`evalharness.py:115-116`).
- The retargeted past-the-cap eval test passes and asserts `[(5, 6)]`:
  `tests/test_symbol_resolution.py:237-240`; run result in
  `fold-captures/pytest-fold-pins.txt`.

## Bar 4 — suite and linter at the tip

Capture: `fold-captures/pytest-tip-fold.txt` (one run, full output to file).

```
512 passed, 2 deselected in 73.23s (0:01:13)
```

Exit 0. `-W error` was in force, so a single warning would have failed the run:
zero warnings. Counts match the gate's of-record numbers (512 passed, 2
deselected); 7 × 72 + 8 = 512.

Pins for the folded surface, run separately and captured once
(`fold-captures/pytest-fold-pins.txt`): 4 passed — the past-the-cap eval test,
the decorated-definition segment test, the two-disjoint-rows padding test, and
the C# namespace-leaf test.

Linter (`fold-captures/ruff-tip-fold.txt`): `uvx ruff check src tests` →
`All checks passed!`, exit 0.

## Bar 5 — no unintended surface

Capture: `fold-captures/git-state.txt`, `diff-docs.txt`, `diff-src-tests.txt`.

`git diff --name-status bf6bcc0..7ea6b63` is exactly the eight expected files:

```
M CHANGELOG.md
M SKILL-CLI.md
M docs/json-schema.md
M runs/issue24-26/gate-verification.md
M src/code_indexer/core.py
M src/code_indexer/evalharness.py
M tests/test_csharp_symbols.py
M tests/test_symbol_resolution.py
```

- No `EXTRACTION_VERSION` and no `CHUNKER_VERSION` in either half of the diff
  (exact-literal search over the captures: no match), so no stored row's meaning
  moved and no reindex is owed.
- No `schema` value other than `1` is introduced.
- No `reindex` / `reindex-project` was run during this verification; the only
  mutating-looking call was `get-code-context`, which reads.
- `code-indexer index-status .` → `state=idle`, no fingerprint-mismatch line
  (`fold-captures/index-status-tip-fold.redacted.txt`).

## Bar 6 — rulings on the three flags the fold leg raised

**1. Error segments also carry `symbol_rows` — ACCEPT.**
The record said "only when `symbol` is set", and the code applies it literally:
`symbol_rows` is written at `core.py:1300-1303`, before the past-end-of-file
branch at `:1304-1308`. So a symbol-mode segment whose span starts past the live
end of the file carries `error`, `symbol_rows` and no `lines`; line mode never
carries the key. The marker answers "how many stored rows did this span merge",
which stays true when the live file has shrunk under a stale row — the count is
a fact about the merge, not about the rows of text. A lines-bearing-only rule
would make the field's presence depend on the file's length rather than on the
mode, and would silently drop the very case where a reader most needs to see
that rows were merged. Literal application is the right reading; the document
already says "symbol mode only", which is the rule actually wanted. No change
owed.

**2. Fold 5's surviving pronoun — READS CORRECTLY.**
`core.py:1229-1234` now runs: "Each segment carries ``symbol_rows``, the number
of stored rows it merged, so the merge is auditable and never silent; a
definition nested inside another of the same name is absorbed by the outer
span. It is part of the answer, never a silent normalisation." The antecedent of
"It" is "the merge", named in the same sentence two clauses earlier; the
alternatives in reach ("a definition", "the outer span") cannot be a
"normalisation", so the semantic fit resolves the reference without ambiguity.
The sentence also still carries its job — the merge is contract, not formatting.
The reference is two clauses back rather than adjacent, which is weaker than the
original "The merge is stated here … — it is part of the answer", but it is
grammatical and single-valued. No change owed.

**3. "The order is identity first, presentation second" — READS AS THE RULE.**
`CHANGELOG.md:50-53`: the sentence stands inside an entry that now states the
rule counterfactually ("merging AFTER padding would fuse …"), and the em-dash
gloss that follows ("the rows' own ranges are merged, then `context_lines` is
applied to each surviving span") spells out the shipped order as an invariant,
not a delta. A reader takes it as "this is the order, and here is why it must be
this order". Residual wrinkle worth naming, not worth a fold: the tail "so a
display flag no longer changes how many segments answer" keeps one before/after
phrasing inside a rule-stated entry. It reads as the consequence of the rule
(padding used to run first; it no longer can), and the entry above it already
owns the change narrative, so it does not pull the entry back into
change-reporting. No change owed.

## Verdict

**PASS.** All eight folds are present, fold 6's contract is live in both modes
and matches `docs/json-schema.md` key by key, the prune is complete and its
retargeted pin asserts `[(5, 6)]`, the suite is 512 passed / 2 deselected with
zero warnings under `-W error`, ruff is clean, the diff touches only the eight
expected files, and all three raised flags are accepted as they stand. No
failing item; nothing returns to the implementation side.

Scope note: this record verifies the folded surface only. It does not re-open
the full gate, re-measure coverage, or re-run the evaluator.
