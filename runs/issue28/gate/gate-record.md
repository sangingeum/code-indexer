# Gate record — `index-status` accepts the same project selector as `overview`

Issue #28. Verification gate. The fix unifies the selector surface with
`overview` while keeping the positional form the MCP server uses.

## Verdict

**PASS-with-folds.** All nine gate bars pass. Three doc-only folds are
required in the development record (rulings 1, 2 and 4), plus one stale
sentence in `AGENT.md` (ruling 5). No code change is required.

## Landed state at hand-off

| item | observed |
|---|---|
| tip | `2659804` `G` `docs(runs): round record for issue #28 index-status selector` |
| fix commit | `a940a8f` `G` `fix(cli): accept --project/--name on index-status (#28)` |
| HEAD vs `origin/main` | identical at hand-off |
| working tree | known local evidence artifacts only; no tracked modifications |

## Gate bars

### 1. Flag form — PASS

Commands, all exit 0 and all print a status line beginning
`project=<repo> state=idle`:

- `code-indexer index-status --project <repo> --skip-stale-check`
- `code-indexer index-status --project code-indexer --skip-stale-check`
- `code-indexer index-status --name code-indexer --skip-stale-check`

The installed tool is an editable install (`direct_url.json` has
`editable:true`), so the installed binary runs this working tree without a
reinstall. Local capture: `runs/issue28/gate/selector-forms.txt`.

### 2. Positional form — PASS

`code-indexer index-status <repo> --skip-stale-check` exits 0 and prints the
same status line. The MCP server's call shape (`src/code_indexer/server.py:241-249`,
`["index-status", path]`) is unchanged. Local capture: `selector-forms.txt`.

### 3. Precedence with distinct projects — PASS

`code-indexer index-status <repo> --project <other-project> --skip-stale-check`
resolved to the flag's project, not the positional one. The unit test
`test_index_status_flag_wins_when_both_given` registers two distinct projects,
points the positional at the first and the flag at the second, then asserts the
second path appears and the first does not; the test is present and green in
the full suite. Stated precedence: **the flag wins when both are given.**
Local capture: `selector-forms.txt`.

### 4. Output canonicality and the empty call — PASS

A slug/name invocation prints `project=<resolved canonical path>`, never the
raw selector. With neither form:

- from the repository directory the command resolves by cwd to `<repo>` and
  exits 0;
- from a neutral directory with multiple registered projects it exits 1 with
  `InternalError: multiple projects registered — pass project (path, slug, or
  name): ...` and lists the registered projects.

Neither case is a crash or a silent wrong project. Local captures:
`selector-forms.txt`, `no-selector.txt`.

### 5. No behavioural regression — PASS

Default invocation is informational only: it printed `state=idle` and no pass
summary. `--refresh` ran the staleness pass and printed an incremental
`last_pass={... files_indexed: 17 ...}` — not a full rebuild. No full reindex
was run. Local capture: `refresh-behaviour.txt`.

### 6. Tests — PASS

- `uv run pytest tests/ -q -W error` → **537 passed, 2 deselected in 75.37s**,
  warn-clean. Capture: `runs/issue28/gate/pytest-full.txt`.
- Targeted selector file: **12 passed in 2.58s**.
- The four selector tests are present in `tests/test_scoped_indexing.py` at
  lines 203, 211, 219 and 228.

### 7. Revert-sensitivity — PASS

Relied on the prior base capture (`<base-worktree>/base-tests.txt`):
**3 failed, 2 passed**, with the three capability tests failing on base and the
positional regression guard passing. Spot-ran one failing base case myself in
`<base-worktree>`:

`uv run pytest "tests/test_scoped_indexing.py::test_index_status_accepts_project_flag" -q -W error`

→ **1 failed in 0.98s**, `No such option: --project`. Local capture:
`<base-worktree>/spot-fail.txt`.

### 8. Lint and hygiene — PASS

- `uvx ruff check src tests scripts` → **All checks passed.**
- `uv run python scripts/publishing_hygiene_lint.py` →
  **scanned=299 skipped=0 violations=0**.
- The fix commit message and the authored development record scan clean for
  absolute host paths, internal profile names and board card ids.
  Capture: `runs/issue28/gate/lint-hygiene.txt`.

### 9. Signature and landed state — PASS

`git log -1 --format='%H %G? %s'` → `2659804... G docs(runs): round record for
issue #28 index-status selector`. HEAD equalled `origin/main` at hand-off; the
fix commit and the record commit are both signed. The working tree carried only
known untracked local evidence. The gate commit that follows this record is
signed and pushed to the designated remote.

## Rulings

1. **Development review-flag inconsistency — doc-only correction required.**
   The worker's final report carried a false review flag: that the installed
   CLI still runs the pre-fix code until `uv tool install --force .`. The
   installed tool is an editable install (`direct_url.json` has
   `editable:true`) and already runs the fixed working tree; the installed
   binary accepted `--project` during this gate. The development record says
   "Review flags: None" and therefore does not carry the false flag at all.
   Ruling: the record needs a doc-only correction that states the installed-CLI
   fact accurately and explicitly drops the reinstall claim. No code change.

2. **Hygiene count drift — acceptable as a point-in-time count, annotate it.**
   The development record reports `scanned=298`; the tip reports `scanned=299`
   because the record file itself became tracked. Ruling: 298 is acceptable as
   "the count when the dev's lint ran" provided the record labels it as such;
   the tip count is 299. Fold: annotate "298 when run; 299 at tip once this
   record was tracked", or simply state 299. Not a code change.

3. **README line length — does not matter.**
   One added README line exceeds 80 columns. The repository has no line-length
   gate, and the same file already contains substantially longer lines. This is
   cosmetic and does not block the round. No fold required.

4. **Development record wording contradiction — doc-only correction required.**
   The design-decision section says the positional form "stays mandatory for
   compatibility" while the changes section says positional `path` becomes
   optional. Those cannot both be true. Ruling: correct the wording to "stays
   supported for compatibility" (the MCP call shape still works; the argument is
   optional), and add a documented no-selector behaviour note: with neither
   form the command resolves by cwd when unambiguous, and otherwise exits 1
   with a clear list of registered projects. No code change.

5. **Other surfaces — one stale doc sentence.**
   `AGENT.md` still says the installed CLI needs `uv tool install --force .`
   after merged changes. On this host the install is editable and live, so that
   sentence is stale; fold it in the same doc pass as ruling 1. No behavioural,
   test or lint issue was found beyond the rulings above.

## Evidence paths

Local, untracked captures under `runs/issue28/gate/`:

- `selector-forms.txt` — five selector forms and the targeted selector suite
- `no-selector.txt` — empty-call resolution and clear-failure case
- `refresh-behaviour.txt` — default versus `--refresh`
- `pytest-full.txt` — full warn-clean suite
- `lint-hygiene.txt` — ruff and publishing-hygiene output
- `help.txt` — `index-status --help` surface

The raw base-worktree captures remain local; this record quotes only their
verdicts.
