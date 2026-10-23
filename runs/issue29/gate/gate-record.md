# Gate record — lifecycle commands share the project selector surface

Issue #29. Verification gate. The fix gives `reindex-project`,
`remove-project`, `lookup-project` and `index-more` the same dual selector as
`index-status`: a positional path or `--project` / `--name` (path, slug, or
custom name), with the flag winning when both are given. Mutating commands
refuse to infer a project from the working directory.

## Verdict

**PASS-with-notes.** All nine behavioral and process bars pass. No code change
is required. Two evidence gaps in the gate's own run are closed in this record:
the base-worktree regression proof had no transcript and was re-run, and the
slug-resolution bar tested a selector that resolves to nothing rather than a
real hash slug. Both closures are stated under Rulings.

## Landed state at hand-off

| item | observed |
|---|---|
| tip | `f0b2ad5` `G` `docs: describe the unified lifecycle selector surface` |
| fix commit | `7305c52` `G` `feat(cli): unify project selector surface for lifecycle commands` |
| HEAD vs `origin/main` | identical at hand-off |
| working tree | known local evidence artifacts only; no tracked modifications |

## Gate bars

### 1. Flag acceptance — PASS

All four commands accept `--project` against a registered project. Each printed
the resolved canonical path or the command's normal success line:

- `lookup-project --project alphaproj` → registered, `path=<alpha>`
- `reindex-project --project alphaproj` → exit 0, pass summary printed
- `index-more --project alphaproj services/b` → exit 0, scope extended
- `remove-project --project gammaproj` → exit 0, collection dropped

Capture: `bar1-flag-acceptance.txt`.

### 2. Flag precedence — PASS

Flag plus positional resolves to the flag's project, not the positional one,
on all three commands where both can be supplied:

- `lookup-project <alpha> --project betaproj` → reported `<beta>`
- `reindex-project <alpha> --project betaproj` → reported `<beta>`
- `remove-project <alpha> --project deltaproj` → removed `<delta>`

Capture: `bar2-flag-precedence.txt`.

### 3. Positional compatibility — PASS

The historical positional forms still work for all four commands, including the
call shapes the MCP server uses (`["lookup-project", path]`,
`["remove-project", path]`, `["index-status", path]`,
`["reindex-project", path]`; `index-more` has no MCP tool). Capture:
`bar3-positional.txt`.

### 4. Slug and name resolution — PASS with an evidence correction

A custom name resolves on the flag path for every command that accepts one
(`alphaproj` → `<alpha>`), and a real hash slug resolves on both spellings:
`lookup-project --project 207a09f8` and `lookup-project 207a09f8` both report
`<beta>` with `slug=207a09f8`.

The gate's own bar-4 capture tested `--project betaproj`, which is neither the
beta project's slug (`207a09f8`) nor its name (none); it correctly returned
`not registered`. That capture does not prove slug resolution. The hash-slug
case was verified directly for this record and does resolve. Capture:
`bar4-slug-name.txt` plus the verification quoted here.

### 5. No cwd guess — PASS

With no selector, each mutating command exits 2 with an `ArgumentError` naming
the command and listing the registered projects:
`reindex-project`, `remove-project`, `index-more` all `EXIT: 2`.
`lookup-project`, being read-only, resolves by cwd and exits 0. Capture:
`bar5-no-cwd-guess.txt`.

### 6. Not-found semantics — PASS

`lookup-project` prints `not registered: <path>` and exits 0 for both an
unknown name and an unregistered path. The three mutating commands die with
`NotFoundError` and exit 1. Capture: `bar6-not-found.txt`.

### 7. `index-more` ambiguity and removal fidelity — PASS

- `--project` plus two positionals → exit 2, `ArgumentError`.
- `--project` with no subpath → exit 2, `ArgumentError`.
- Historical two-positional form → exit 0, scope extended.
- Flag form with one positional → exit 0, scope extended.
- `remove-project` against a temporary project dropped the collection, removed
  the manifest directory, and deleted the registry row — checked on the
  filesystem and in the registry before and after, not from the printed line.

Capture: `bar7-index-more-and-removal.txt`.

### 8. Base regression proof — PASS

The gate's summary claimed the base-worktree proof ran, but produced no
transcript. Re-run for this record against a worktree at base `1a7e70e` with the
new test file copied in:

`uv run pytest tests/test_lifecycle_selector.py -q`

→ **19 failed, 5 passed in 5.53s**. The 19 capability tests fail on base with
`No such option: --project` and the old usage text; the 5 positional regression
guards pass. The same file is green at the tip. The worktree was removed after
the run.

### 9. Suite, lint, hygiene, signature — PASS

At the tip `f0b2ad5`:

- `uv run pytest tests/ -q -W error` → **561 passed, 2 deselected** in 78.53s,
  exit 0. Capture: `runs/issue29/gate/pytest-full.txt`.
- `uvx ruff check src tests scripts` → **All checks passed!**, exit 0. Capture:
  `ruff.txt`.
- `uv run python scripts/publishing_hygiene_lint.py` →
  **scanned=304 skipped=0 violations=0**, exit 0. Capture: `hygiene.txt`.
- New selector file alone: **24 passed** (`runs/issue29/newtests.txt`).
- `git log -2 --format='%h %G?'` → both commits `G`. HEAD equalled
  `origin/main` at hand-off.

## Scrutiny items

1. **`_usage_die` taxonomy path.** `ArgumentError` is defined in
   `errors.py` with `exit_code = EXIT_USAGE` (2), distinct from `_die`'s
   `ValueError` path (exit 1). Observed exits match: 2 for usage errors, 1 for
   not-found. The one-line output contract holds — each failure prints one
   `ClassName: message` line. No test elsewhere assumed exit 1 for these usage
   cases; the full suite is green.
2. **`lookup_project` resolver change.** It now calls `core.resolve_entry`
   instead of `registry.get_by_path`, keeping the not-registered path and the
   canonical-path print. A symlinked path resolves to the canonical registered
   path on both spellings — `lookup-project --project <symlink>` and the
   positional form both reported `<alpha>`. Capture: `scrutiny-symlink.txt`.
3. **`index_more` positional parameter rename.** `path, subpath` became
   `first, second` in the signature only; the historical two-argument call
   still works and the help text documents both forms. Nothing outside the
   command referenced the old parameter names.
4. **Commit granularity.** The dev committed after the tree went green rather
   than as an early intermediate. The brief said "preferred but did not
   require"; two commits landed (`7305c52` code+tests+record, `f0b2ad5` docs).
   Acceptable, noted only.
5. **No real project was removed or reindexed.** All destructive checks ran
   against a temporary registry and index root.

## Rulings

1. **Gate evidence gaps are noted, not code folds.** The base-regression
   transcript was missing and bar 4's slug case was mis-chosen. Both are
   closed by direct verification here; neither indicates a defect in the fix.
   No code or doc change is required. The process note for later rounds: a gate
   that claims a bar must leave the transcript for it in the round's evidence
   directory.
2. **Bar 4 wording.** The development record does not overclaim slug
   resolution — it says path, slug and name all resolve, which the direct check
   confirms. No correction needed.
3. **`index-more` has no MCP tool.** The brief listed an `index-more` MCP call
   shape among the compatibility checks; no such tool exists. The positional
   form is still covered as a CLI surface. No change.

## Evidence paths

Local, untracked captures under `runs/issue29/gate/`:

- `pytest-full.txt`, `pytest-exit.txt` — full warn-clean suite and exit code
- `ruff.txt`, `ruff-exit.txt` — lint output and exit code
- `hygiene.txt`, `hygiene-exit.txt` — publishing hygiene and exit code

The gate's behavioral captures (`bar1`–`bar7`, `setup`, `scrutiny-symlink`) were
written to a temporary verification directory and are quoted here; they were not
copied into the tree. The base-worktree run is quoted by verdict only.
