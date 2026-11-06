# Narrow re-verify — `remove-project` reclaims what the project owns

- Tip under test: `96b24b1` (branch `main`, pushed). The red being re-ruled is in
  `runs/round2-reverify/record.md`; the fix record is
  `runs/round2-remove-project-fix/record.md`.
- Scope: the one probe, plus regression (suite, ruff, hygiene). Nothing from the
  eight folds is re-opened.
- Live, not stub: the embedding service and the vector store were reachable this
  leg, so the probe drives the shipped CLI against the real store and asks Qdrant
  what it holds before, between and after the commands.

## Verdict

_PENDING — filled from the live probe's own output._

## The ruling

1. Everything the project owns is reclaimed (alias, physical collection behind it,
   a collection left under an older identity).
2. The message is true in both directions (collections exist / none exist).
3. The alias never outlives its collection — end state observed, code read for the
   order.
4. No residue: the store's collection and alias lists are equal to their pre-probe
   state and the throwaway projects are unregistered.
5. Three advisories: design doc, `schema=<n>` / `rows=absent` sentence,
   `rows_staleness_warning` gone with its test and no caller.
6. Regression: full suite, ruff, hygiene.

## Evidence

_PENDING — `probe_remove_project_live.txt` is the capture._
