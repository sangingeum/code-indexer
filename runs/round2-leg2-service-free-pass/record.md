# Round 2 leg 2 — a pass that needs no service

Card: `<card-id>` (round 2, leg 2). Governing: ADR-0001 sections 2-5,
ADR-0003 (read contract), the overhaul plan's round 2 acceptance.

Baseline on dispatch: `52a2975`, 651 passed / 2 deselected `-W error`, ruff
clean, hygiene `scanned=547 violations=0`.

## Slices

1. `chunks` population and purge — landed, commit `0102410`.
2. Vectors-off pass, embedding from `chunks`, `--vectors`, index-status
   fields — landed (commit below).
3. Three version axes and the policy matrix — in progress.
4. Per-side fingerprint and the two refusals — pending.
5. Tests, docs, this record — pending.

## Measurements so far

* Throwaway root, `OLLAMA_URL`/`QDRANT_URL` both pointed at a closed local port:
  `add-project` exits 0, records `vectors=off`/`vectors_state=absent`, and
  writes files=2, symbols=4, refs=1, chunks=4, chunks_fts=4, index_errors=0.
* `index-status --json` on that project: `rows=current`, `vectors=off`,
  `vectors_state=absent`, `schema=1`.
* Full suite after slice 2: 652 passed, 2 deselected, `-W error`; ruff clean;
  hygiene violations=0.

## Open items

* Per-side fingerprint (rows side = chunker/extraction; vectors side = the one
  composed embedding identity) and the two `semantic-search` refusals.
* Policy matrix tests: an embedding bump builds a new collection and leaves
  rows; a schema bump leaves the collection; an extraction bump invalidates
  rows.
* `tests/test_vectors_off.py`, `tests/test_vectors_on_reads_no_sources.py`.
* Docs: SKILL-CLI.md, docs/json-schema.md, README, CHANGELOG.
