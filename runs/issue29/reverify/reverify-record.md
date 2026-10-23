# Narrow re-verify — lifecycle selector surface (issue #29), post-fold tip

## Verdict

**PASS.**

The folds commit `b8ad620` closed both review folds: the published docs now
scope the "flag wins" and MCP call-shape claims to the commands where they are
true and state `index-more`'s ambiguity rejection, and the selector tests now
exercise genuinely distinct spellings plus the real hash-slug path for
`lookup-project`, `reindex-project` and `remove-project`. No implementation
file moved, no "flag wins" test was weakened, and the full suite is
warn-clean.

Verified tip: `b8ad62017f0c232d2f0d04d5abd9eab7f1c10491` (`b8ad620`),
signed (`G`), equal to `origin/main`.

## Bar results

1. **Fold 1 closed — wording. PASS.** All four surfaces scope "flag wins" to
   `reindex-project`, `remove-project` and `lookup-project`, state both
   `index-more` accepted forms and its ambiguity rejection, and scope the MCP
   call shape to the four commands with MCP tools while saying `index-more`
   has none:
   - `README.md:42-49`
   - `SKILL-CLI.md:511-524`
   - `CHANGELOG.md:5-20`
   - `runs/issue29/dev-record.md:8-17` (selector semantics); the per-command
     sections at `:19-58` keep "flag wins" only under the lookup and
     mutating headings, and `:44-49` states the ambiguity rejection.
   `README.md:66-88` additionally lists `index-more` under CLI-only commands in
   the CLI-to-MCP parity table. One low-severity wording residue remains in the
   dev record's test inventory — see R1.

2. **Fold 2 closed — real hash-slug coverage. PASS.** The two spellings now
   resolve to different strings and the hash-slug branch is exercised for all
   three commands:
   - `tests/test_lifecycle_selector.py:132` and `:146` (lookup): the named test
     registers `Mono Repo`, asserts `entry.slug == "Mono_Repo"` and
     `entry.slug != entry.name`, then resolves by both spellings; the hash test
     registers with `name=None`, asserts `entry.name is None` and
     `entry.slug != "monoproj"`, then resolves by `--project <hash>` and by the
     positional path.
   - `:222` and `:234` (reindex): same distinct-name pattern, plus a hash test
     that resolves by `--project <hash>` and the positional path and asserts
     the result names the hash slug.
   - `:311` and `:326` (remove): same distinct-name pattern (asserts the dropped
     collection name `idx_Mono_Repo`), plus a hash test that removes by
     `--project <hash>` and, after re-registration without a name, by the
     positional path.
   No loop over two spellings that are the same string remains in these tests.

3. **No weakening. PASS.** `git diff b78e946 b8ad620 -- tests/` contains zero
   changed lines touching any `*_flag_wins_when_both_given` test. All three
   remain in place and still assert the flag project's identity plus the
   absence of the positional one (`:172-183` lookup, `:251-262` reindex,
   `:347-359` remove).

4. **No behavior change. PASS.** `git show b8ad620 -- src/` is empty;
   `git diff --name-only b78e946 b8ad620 -- src/` returns zero paths. The folds
   commit touched only `CHANGELOG.md`, `README.md`, `SKILL-CLI.md`,
   `runs/issue29/dev-record.md` and `tests/test_lifecycle_selector.py`.

5. **Suite. PASS.** `uv run pytest tests/ -q -W error` → **564 passed,
   2 deselected** in 82.23s, exit 0. Tail captured to
   `runs/issue29/reverify/pytest-full.txt`.

6. **Targeted. PASS.** `uv run pytest tests/test_lifecycle_selector.py -q
   -W error` → **27 passed** in 5.28s, exit 0. Captured to
   `runs/issue29/reverify/pytest-targeted.txt`.

7. **Lint. PASS.** `uvx ruff check src tests scripts` → **All checks passed!**,
   exit 0. Captured to `runs/issue29/reverify/ruff.txt`.

8. **Hygiene. PASS.** `uv run python scripts/publishing_hygiene_lint.py` →
   **scanned=306 skipped=0 violations=0**, exit 0. Captured to
   `runs/issue29/reverify/hygiene.txt`.

9. **Landed state. PASS.** `git log -1 --format='%h %G? %s'` →
   `b8ad620 G docs(29): scope selector claims and exercise hash-slug branches`.
   `git rev-parse HEAD origin/main` returns the same commit for both. No tracked
   modifications in the working tree; only untracked local evidence artifacts.

## Behavioral spot checks

- `index-more --project <project> <subpath> <extra>` → exit 2,
  `ArgumentError: index-more got --project plus two positionals — use
  --project <project> <subpath> or <project> <subpath>, not both`. Run against a
  temporary index root; no real project touched.
- `index-more` with no selector → exit 2, listing no registered projects in the
  temporary root.
- `src/code_indexer/server.py` registers no `index_more` tool; the tool set is
  lookup, add, remove, list, semantic-search, index-status, reindex,
  find-symbol, find-symbols, skeleton, outline, find-definition,
  find-references, find-callers, find-callees, deps, changed-symbols and
  get-code-context.

## Residual findings

- **R1 — low — dev record test inventory not updated by the folds.**
  `runs/issue29/dev-record.md:80-88` still says "24 tests, one group per
  command" and lists "flag wins when both are given" as a group attribute; the
  file now holds 27 tests and `index-more` has no "flag wins" behavior (its
  fourth combination is the ambiguity rejection). The selector-semantics
  section is correctly scoped; this is an internal record, not a published doc,
  and the same bullet later enumerates `index-more`'s ambiguity-rejection test.
  No published claim is affected. Evidence: `grep -c '^def test_'
  tests/test_lifecycle_selector.py` and `runs/issue29/dev-record.md:80-88`.
- **R2 — low — dev record verification counts are stale.**
  `runs/issue29/dev-record.md:92-97` cites "561 passed, 2 deselected" and
  "scanned=302"; the observed post-fold counts are 564 passed / 2 deselected
  and scanned=306. The record predates the three added tests; the published
  docs carry no count. Evidence: bar 5 and bar 8 captures in
  `runs/issue29/reverify/`.

No correctness or behavior finding. Both residuals are record-hygiene only and
do not affect the selector surface.

## Observed counts

| check | result |
|---|---|
| full suite | 564 passed, 2 deselected, exit 0 |
| targeted selector suite | 27 passed, exit 0 |
| ruff | All checks passed, exit 0 |
| publishing hygiene | scanned=306 skipped=0 violations=0, exit 0 |
| folds commit source changes | 0 files under `src/` |
| "flag wins" test edits | 0 lines |

## Landed state

- HEAD: `b8ad62017f0c232d2f0d04d5abd9eab7f1c10491` (`b8ad620`)
- Signature: `G` (good), repo-local identity
- HEAD vs `origin/main`: identical
- Tracked working-tree modifications: none (untracked local evidence only)
