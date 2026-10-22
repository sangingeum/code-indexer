# Re-verification record — issue #28 selector folds

Narrow re-verification of the doc-only folds applied after the gate commit
`c2a6fe2`. This is not a second full gate: it confirms the folds are what they
claim and that nothing moved underneath them. No code was changed.

## Verdict

**PASS.** The folds are present and correct. No source, test or behavioural
change landed after the gate commit. The gate rulings are satisfied at the tip.
The fix still behaves in the spot checks. The suite, lint and hygiene are clean.
Every commit from the gate commit to the tip is signed and the tip equals
`origin/main`.

## Doc-only proof

The commits after the gate commit `c2a6fe2` are `2212f5c` and `d2cc6e4`.

| commit | numstat | paths |
|---|---|---|
| `2212f5c` | `3 2 AGENT.md`, `13 3 runs/issue28/dev-record.md` | prose only |
| `d2cc6e4` | `121 0 runs/issue28/review/review-record.md` | prose only |

Every changed path is prose: two Markdown records and `AGENT.md`. No source
file, test file or behavioural file is in the set. The diff of `2212f5c`
contains only the folded sentences and the post-gate-folds note; the diff of
`d2cc6e4` adds the review record only.

## Gate rulings at the tip

### Ruling 1 — the false installed-CLI claim

Satisfied. The development record no longer claims a reinstall is needed. Its
review-flags section still reads "None", and the post-gate-folds note names the
stale `AGENT.md` install sentence. `AGENT.md` now reads:

> Installed CLI on this host: `~/.local/bin/code-indexer` (uv tool; an editable
> install of this checkout, so it runs the working tree and needs no reinstall
> after merged changes).

Location: `AGENT.md:37-39`. The sentence is accurate about the editable install.
The false "still runs the pre-fix code until `uv tool install --force .`" claim
appears nowhere in the record or in `AGENT.md`.

### Ruling 2 — hygiene count

Satisfied as a point-in-time annotation. `runs/issue28/dev-record.md:57-59`
reads:

> Publishing hygiene: `uv run python scripts/publishing_hygiene_lint.py`
> → scanned=298 when this lint ran; 299 once this record was tracked and 300 at
> the current tip after the gate record; skipped=0 violations=0.

The sequence is honest: 298 when the fix author's lint ran, 299 when the
development record became tracked, 300 at the tip after the gate record. The
measured tip now reports `scanned=301 skipped=0 violations=0`, because the
review record commit `d2cc6e4` added one more tracked file. The annotation's
"current tip" refers to the post-gate-record tip (`2212f5c`), not today's tip;
it does not misstate the sequence. This is later drift, not a fold error.

### Ruling 3 — README line length

No fold required. The ruling stands; no action was taken and none is needed.

### Ruling 4 — optionality wording

Satisfied. "stays mandatory" is gone. `runs/issue28/dev-record.md:22` now reads:

> The positional form stays supported for compatibility: the MCP server still
> invokes `["index-status", path]`.

The no-selector behaviour is documented at `runs/issue28/dev-record.md:25-28`
and is true when re-run. From the repository directory the empty call resolves
by cwd and exits 0. From a neutral directory with several registered projects
it exits 1 with `InternalError: multiple projects registered — pass project
(path, slug, or name)` and lists the registered projects.

### Ruling 5 — `AGENT.md` sentence only

Satisfied. The sentence quoted under ruling 1 is the corrected form; no other
behavioural, test or lint issue was found.

## Behaviour spot checks

| check | result |
|---|---|
| `code-indexer index-status --project code-indexer --skip-stale-check` | exit 0, printed the canonical project path and `state=idle` |
| `code-indexer index-status <repo> --skip-stale-check` | exit 0, printed the same line |
| `code-indexer index-status --skip-stale-check` from a neutral directory | exit 1, `InternalError: multiple projects registered — pass project (path, slug, or name)`, projects listed |
| `code-indexer index-status --skip-stale-check` from the repository directory | exit 0, resolved by cwd, printed the same line |

The `<repo>` placeholder is the repository's own path; the literal host path is
kept out of this record. The flag and positional forms both print the canonical
path and `state=idle`.

## Suite, lint and hygiene at the tip

- `uv run pytest tests/ -q -W error` → **537 passed, 2 deselected** in 74.55s,
  warn-clean. This matches the gate's 537 passed, 2 deselected.
- `uvx ruff check src tests scripts` → **All checks passed!**
- `uv run python scripts/publishing_hygiene_lint.py` →
  **scanned=301 skipped=0 violations=0**.
- This re-verification record is scanned explicitly with `--paths` before it is
  committed; the count will rise by one when this record becomes tracked.

## Signatures and landing

`git log --format='%h %G? %s' c2a6fe2^..HEAD`:

- `c2a6fe2 G gate(28): verification record for index-status selector unification`
- `2212f5c G docs(28): apply gate folds to the round record and AGENT.md`
- `d2cc6e4 G docs(28): two-axis review record for index-status selector fix`

All three are signed `G`. `git rev-parse HEAD` equals `git rev-parse origin/main`
at the time of this check. The working tree carries only known untracked local
evidence; no tracked modification was present.

## Review follow-up

Issue #29 exists (`gh issue view 29`): "Unify project selector surface across
all lifecycle commands", state open. Its body scopes it to `reindex-project`,
`remove-project`, `lookup-project` and `index-more`, the lifecycle commands that
remain positional-only. It does not overlap issue #28's closed scope, which is
`index-status` only. No duplicate or scope collision.

## Board

The board card for this leg was found before the checks; it is the narrow
re-verification task and was in progress. The card id is not reproduced here, per
the publishing rules.
