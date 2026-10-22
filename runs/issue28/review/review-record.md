# Two-axis review — issue #28

**Verdict:** PASS

The gate's four doc-only folds are correctly applied in commit `2212f5c`.
The fix itself faithfully implements issue #28. One follow-up is owed for the
remaining lifecycle commands, filed as #29.

## Standards axis

### Output contract
The single status line is preserved. `index_status` still prints one line
(`project=<canonical path> state=<state>`); the change from `project={path}` to
`project={entry.path}` only makes the printed path canonical for slug/name
invocations. No bare output was added. **No findings.**

### Additive-only change
The signature gains optional `--project`/`--name` and makes the positional
argument optional. No query path, index pass, schema, or MCP tool signature
moved. **No findings.**

### Warn-clean suite
Gate evidence: `uv run pytest tests/ -q -W error` → 537 passed, 2 deselected,
75.17s, warn-clean. **No findings.**

### Signed commits
`a940a8f` (fix) and `2212f5c` (folds) are signed by the repo-local identity.
**No findings.**

### Hygiene-clean authored surfaces
`uv run python scripts/publishing_hygiene_lint.py` at the tip reports
`scanned=300 skipped=0 violations=0`. The authored surfaces touched by the fix
(`README.md`, `SKILL-CLI.md`) are clean. **No findings.**

### Optional positional argument safety
With neither selector the command resolves by cwd when exactly one registered
project matches, and otherwise exits 1 with a clear list. The dev-record
documents this. The failure message (`multiple projects registered — pass
project (path, slug, or name)`) is the right affordance because the command
accepts all three selector forms. **No findings.**

## Spec axis

### Selector surface matches `overview`
The accepted selector surface matches `overview`'s: path, slug, or custom name
via `--project`/`--name`, flag wins when both are given. Both spellings reach
the single resolver `_resolve(core, project or path)`. **No findings.**

### Positional form survives for MCP
The positional `PATH` remains; the help text states the flag wins. The MCP
server still invokes `["index-status", path]`. **No findings.**

### Documented call form works
README quick-start/troubleshooting and SKILL-CLI lifecycle block show the dual
spelling and flag precedence. Gate evidence confirms the documented form works
against the installed CLI. **No findings.**

### Scope draw
The fix correctly scopes to the reported symptom (`index-status` vs `overview`).
The other lifecycle commands remain positional-only. This is a valid scope draw
for issue #28, but the asymmetry warrants a follow-up. **Finding:** filed as
#29, "Unify project selector surface across all lifecycle commands".

## Numbered rulings

### 1. The four gate folds — did they land correctly?
**Yes.** All four are present in commit `2212f5c` and are correct:
- **Positional-form wording:** "stays supported" replaces "stays mandatory" in
  `runs/issue28/dev-record.md`.
- **No-selector behaviour note:** added to `runs/issue28/dev-record.md`.
- **Hygiene count annotation:** "298 when this lint ran; 299 once this record
  was tracked and 300 at the current tip after the gate record" in
  `runs/issue28/dev-record.md`.
- **Stale `AGENT.md` sentence:** replaced with "an editable install of this
  checkout, so it runs the working tree and needs no reinstall after merged
  changes".
No over-reach: the folds are strictly doc-only. **PASS.**

### 2. The dev's false operational claim
The dev's final report claimed the installed CLI "still runs the pre-fix code
until `uv tool install --force .`". That was false; the install is editable.
The claim never entered the committed record. The fold corrected `AGENT.md`,
the source of the false claim. The dev-record now states "Post-gate folds: ...
the stale `AGENT.md` install sentence." The record now tells the truth about the
installed binary. The absent false claim needs no further trace in the record,
but it is a **process note** for later rounds: verify the installed binary's
state before claiming it is stale. **PASS with process note.**

### 3. Hygiene count drift
The fold's annotation reads correctly: "298 when this lint ran; 299 once this
record was tracked and 300 at the current tip after the gate record." The
actual count at the tip is 300 tracked files. The dev's original lint ran at
298 tracked files; the dev-record commit added one (299); the gate record added
one more (300). The annotation does not misstate the counts. **PASS.**

### 4. Optionality semantics
Documented adequately and safe. The dev-record's no-selector note matches the
observed behavior: cwd resolution when exactly one registered project matches,
and a clear failure listing the registered projects otherwise. The failure
message "multiple projects registered — pass project (path, slug, or name)" is
the right affordance because the command accepts all three selector forms. The
command should not require a selector explicitly; the cwd default is a
reasonable CLI affordance, and the failure is loud when ambiguous. **PASS.**

### 5. README line length
One added README line (line 187) exceeds 80 columns (129 chars). The repo has
no line-length gate; neither `AGENT.md` nor `CONVENTIONS.md` specifies one. The
existing README already has multiple lines exceeding 80 columns. **Does not
matter.** Non-blocking style observation.

### 6. Scope draw
The asymmetry is a valid scope draw for issue #28, which specifically asks for
`index-status` to match `overview`. A follow-up is owed to align the other
lifecycle commands' selector surface. Filed as #29, "Unify project selector
surface across all lifecycle commands". **Follow-up filed.**

## Summary
The fix faithfully implements issue #28: the selector surface is unified, the
positional form survives, the output contract is preserved, and the change is
additive. The four gate folds are correctly applied. The only actionable item
is the follow-up for the remaining lifecycle commands. **Verdict: PASS.**
