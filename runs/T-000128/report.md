# Reference split -- narrow verification record

Round under test: the split of `query.md` into `query.md` + `semantic.md`
(the reference-split round), verified against the owner's 2026-11-07 ruling and
ADR-0009 as amended at `e5aa8a1`.

- Base: `e5aa8a1` (the ADR amendment)
- Tip at dispatch: `2af2ab2`, plus the docstring fold `638c56a`
- HEAD verified: `638c56a`; `git ls-remote origin refs/heads/main` returned
  `638c56abff2b3aa64327e4f8a93f6b913ec67b88` -- pushed ref equals the tip.
- Every command below was run at that tip, in the verifying session. Raw
  captures live beside this file; they stay untracked because an eval report
  and a pytest log carry this machine's project path and are not publishable.

## Verdict rows

| # | Row | Verdict | The number |
|---|---|---|---|
| 1 | One home per command | PASS | 33 registered commands, 33 generated sections: indexing 11, query 13, semantic 3, plans 6, machine-output 0, failures 0 |
| 2 | Budgets | PASS | entry 109 lines / 6,931 B (of 120 / 8,192); `query.md` 292 / 13,798 (of 400 / 24,576); `semantic.md` 191 / 11,451 |
| 3 | Four restored examples exist and run | PASS | 4/4 executed, exit 0 each; the dotted-name claim also run and it fails exactly as documented |
| 4 | Entry-document trigger for `semantic.md` | PASS | `SKILL-CLI.md:106`, sixth row of a six-row table; installed `references/semantic.md` present and digest-equal |
| 5 | Generated blocks equal the generator | PASS | `--check` exit 0; the generator maps the three commands to `semantic.md` |
| 6 | Record and tree agree | PASS | the ADR table names six references, six files exist; `query.md` carries its 13, no semantic section left behind |
| 7 | Counts | PASS | six at `README.md:108` and `docs/overhaul-plan.md:253`; the ADR's historical "five" kept as provenance |
| 8 | Regression | PASS | 999 passed / 2 deselected; docs 39 passed; ruff clean; hygiene 0; every commit signed; pushed ref equals tip |
| 9 | No code behaviour changed | PASS | `git diff --stat e5aa8a1..tip -- src/` = 2 files, 2 insertions, 2 deletions, both docstring lines |

**VERDICT = PASS**

## 1. One home per command

Counted on the generated section heading `### `<name>`` in each reference:

| Reference | Commands |
|---|---|
| `indexing.md` | 11 |
| `query.md` | 13 |
| `semantic.md` | 3 |
| `plans.md` | 6 |
| `machine-output.md` | 0 |
| `failures.md` | 0 |
| **total** | **33** |

The entry document's roster carries 33 rows (`rg -c '^\| `[a-z-]+` \| ' SKILL-CLI.md`),
so no command is homeless and none is doubled. The index-closure test that
proves it -- `tests/test_docs.py::test_every_command_is_covered_by_exactly_one_reference`
-- is collected and passed in the run below.

Commands that moved (base `e5aa8a1:skill/references/query.md` had 16 sections):

- `semantic-search`
- `eval`
- `eval-compare`

`query.md` went 16 -> 13; `semantic.md` is new with 3. No structural command
left `query.md`: its 13 sections are `overview`, `skeleton`, `map`, `outline`,
`file-outline`, `find-symbol`, `find-definition`, `find-references`,
`find-callers`, `find-callees`, `get-code-context`, `deps`, `changed-symbols`.

## 2. Budgets, measured

Entry document budget 120 lines / 8,192 B; reference budget 400 lines / 24,576 B.

| File | Lines | Limit | Bytes | Limit | Headroom (lines / bytes) |
|---|---|---|---|---|---|
| `SKILL-CLI.md` (entry) | 109 | 120 | 6,931 | 8,192 | 11 / 1,261 |
| `skill/references/indexing.md` | 337 | 400 | 16,146 | 24,576 | 63 / 8,430 |
| `skill/references/query.md` | 292 | 400 | 13,798 | 24,576 | 108 / 10,778 |
| `skill/references/semantic.md` | 191 | 400 | 11,451 | 24,576 | 209 / 13,125 |
| `skill/references/plans.md` | 204 | 400 | 10,595 | 24,576 | 196 / 13,981 |
| `skill/references/machine-output.md` | 141 | 400 | 6,224 | 24,576 | 259 / 18,352 |
| `skill/references/failures.md` | 127 | 400 | 9,178 | 24,576 | 273 / 15,398 |

`query.md` at the base was 399 lines / 20,982 B -- one line under its budget,
which is the split's reason. At the tip it is 292 / 13,798: 73.0% of the line
budget and 56.1% of the byte budget, so the headroom is real (108 lines,
10,778 bytes). `semantic.md` fits at 47.8% of lines and 46.6% of bytes.
Measured with `wc -l -c`; the six `test_reference_budget[...]` parametrizations
and `test_entry_document_budget` pass in the documentation-test capture.

## 3. The four restored examples: documented, executed

Each was run as written (only `--project` added where the documented line
relies on a registered project).

| Example | Documented at | Executed | Exit |
|---|---|---|---|
| `outline <file> --docstrings` | `skill/references/query.md:125` | `code-indexer outline src/code_indexer/core.py --docstrings` | 0 |
| `get-code-context <file> --symbol <name>` | `skill/references/query.md:235` | `code-indexer get-code-context src/code_indexer/core.py --symbol search --context-lines 3` | 0 |
| `eval` (dense, to a report file) | `skill/references/semantic.md:64` | `code-indexer eval --project code-indexer --queries eval/queries.jsonl --k 1,3,5,10 --out <evidence>/eval-dense.json` | 0 |
| `eval` (heuristic reranker) | `skill/references/semantic.md:68` | `code-indexer eval --project code-indexer --queries eval/queries.jsonl --rerank heuristic --out <evidence>/eval-rerank.json` | 0 |
| `eval-compare a b` | `skill/references/semantic.md:72` | `code-indexer eval-compare <evidence>/eval-dense.json <evidence>/eval-rerank.json` | 0 |
| `changed-symbols --include-untracked` | `skill/references/query.md:257` | `code-indexer changed-symbols --include-untracked` | 0 |
| `changed-symbols --base/--head --impact` | `skill/references/query.md:260` | `code-indexer changed-symbols --base HEAD~5 --head HEAD --impact` | 0 |

Raw captures: `ex-outline-exact.txt`, `ex-gcc-exact.txt`, `ex-gcc-dotted.txt`,
`ex-eval.txt`, `ex-eval-rerank.txt`, `ex-eval-compare.txt`, `ex-changed-exact.txt`,
`ex-changed-wt.txt`, plus the two reports `eval-dense.json` and
`eval-rerank.json`. `eval-compare` printed the metric delta table over the two
freshly written reports (recall@3 0.7619 -> 0.8095, mrr 0.6949 -> 0.7060, five
improved, one regressed) -- a real comparison, not a fixture.

**The dotted-`--symbol` claim, checked.** `query.md:238-243` says `--symbol`
matches the bare row name and that the dotted spelling is not a locator:
`--symbol Core.search` answers `InternalError` and exits 1. Run:

```
code-indexer get-code-context src/code_indexer/core.py --symbol Core.search --context-lines 3
exit=1
InternalError: symbol 'Core.search' not indexed in src/code_indexer/core.py
```

The claim is accurate, message and exit code both; the bare form at the same
site answers with the padded span and exits 0.

## 4. The entry-document trigger

`SKILL-CLI.md:106` is the `semantic.md` row:

```
| [semantic.md](skill/references/semantic.md) | a question is asked in words rather than as a name, or a ranked answer's order needs reading |
```

It sits in the six-row reference table (`SKILL-CLI.md:101-107`) beside
`indexing.md`, `query.md`, `plans.md`, `machine-output.md` and `failures.md`.
Installed shape: `<skills-root>/code-indexer/references/semantic.md` exists
(six reference files installed), and its SHA-256
`4a346f2bb6f8f56dc87fd55baef3e9e2bf0f0a39987e74f197048cc219a4e603` equals the
tracked file's. The installed `SKILL.md` digest `2f828ac8...` equals
`SKILL-CLI.md`'s. `test_installed_copy_digest_matches_the_tracked_copy` passes
and nothing skipped in that run, so the trigger resolves inside the installed
shape.

## 5. Generated blocks

`uv run python scripts/generate_skill_docs.py --check` -> exit 0, no output
(`generator-check.txt`), i.e. every generated region equals the generator's
output; a hand edit inside one would have failed here and in
`test_generated_blocks_match_the_generator` (passed).

Marker pairs present and balanced: `roster` in `SKILL-CLI.md`,
`commands-indexing`, `commands-query`, `commands-semantic`,
`commands-plans`, `json-commands`.

The generator's mapping (`scripts/generate_skill_docs.py:63-67`) is:

```
"semantic.md": ("semantic-search", "eval", "eval-compare"),
```

and `COMMAND_REFERENCE` is derived from that table, so the roster pointer for
those three commands is `semantic.md` -- which is what the roster prints at
`SKILL-CLI.md:57-59`.

## 6. Record and tree agree

The ADR's section 2 table (`docs/adr/0009-agent-surface-skill-layout.md:44-49`)
names six rows: `indexing.md`, `query.md`, `semantic.md`, `plans.md`,
`machine-output.md`, `failures.md`. All six files exist under
`skill/references/`. Line 51 states the set is six under the 2026-11-07 ruling.

The `query.md` row names `overview`, `skeleton`, `outline`, `find-*`,
`get-code-context`, `deps`, `changed-symbols`, `map`, `file-outline` -- the 13
commands the file's generated block actually carries, in that order, and no
semantic section left behind. The `semantic.md` row names `semantic-search`,
`eval`, `eval-compare` plus the shared ordering policy; the file carries those
three sections and the ordering policy (`## Ordering: source, then samples,
tests, docs, artifacts` at `semantic.md:91`, with the two walks at
`semantic.md:155`). Both cross-links from `query.md` resolve to those headings
(`query.md:138`, `query.md:221`), and `test_relative_links_resolve` passes.

## 7. Counts

- `README.md:108`: "one entry document plus six references under `skill/references/`"
- `docs/overhaul-plan.md:253`: "six reference files read on demand (amended under the 2026-11-07 ruling ... the set was five, is six, and the budget did not move)"
- `docs/adr/0009-agent-surface-skill-layout.md:101` still says "five references" inside the historical consequences paragraph; that is provenance for the pre-ruling state and is allowed to stay.

## 8. Regression at the tip

- `uv run pytest -q` -> `999 passed, 2 deselected, 5 warnings in 107.72s`
  (`pytest-tip.log`). All five warnings are the same one,
  `src/code_indexer/watcher.py:343: DeprecationWarning: ... fork()`, the known
  watcher issue; warn-clean is not claimed.
- `uv run pytest tests/test_docs.py -q` -> `39 passed in 0.80s`, 0 skipped
  (`pytest-docs.log`).
- `uvx ruff check src tests scripts` (the CI lint line) -> `All checks passed!`,
  exit 0 (`ruff.txt`).
- `uv run python scripts/publishing_hygiene_lint.py` -> run after the last
  capture and after this record was committed; `violations=0` (`hygiene.txt`).
- Commits at the tip are signed: `git log --format='%h sig=%G? %s'` reports
  `sig=G` for `638c56a`, `2af2ab2`, `055dcd6`, `e5aa8a1`.
- `git ls-remote origin refs/heads/main` equals `638c56a...`; the verified tree
  is the pushed tree.

## 9. No code behaviour changed

`git diff --stat e5aa8a1..HEAD -- src/`:

```
 src/code_indexer/ordering.py | 2 +-
 src/code_indexer/ranking.py  | 2 +-
 2 files changed, 2 insertions(+), 2 deletions(-)
```

The full diff (`src-diff.txt`) is two docstring lines, each replacing the
pointer `skill/references/query.md` (Ordering) with
`skill/references/semantic.md` (Ordering). No executable line, no import, no
signature. Nothing else in `src/` changed between the base and the tip.

## Evidence index

| File (beside this one) | What it holds |
|---|---|
| `pytest-tip.log` | full suite at `638c56a` |
| `pytest-docs.log` | the 39 documentation-drift tests, no skips |
| `ruff.txt`, `generator-check.txt`, `hygiene.txt` | lint, generator drift check, publishing hygiene |
| `src-diff.txt` | `git diff e5aa8a1..HEAD -- src/` |
| `ex-outline-exact.txt`, `ex-gcc-exact.txt`, `ex-gcc-dotted.txt` | the two structural examples, bare and dotted form |
| `ex-eval.txt`, `ex-eval-rerank.txt`, `ex-eval-compare.txt`, `eval-dense.json`, `eval-rerank.json` | the eval harness example end to end |
| `ex-changed-exact.txt`, `ex-changed-wt.txt` | `changed-symbols` over a revision range and the working tree |
