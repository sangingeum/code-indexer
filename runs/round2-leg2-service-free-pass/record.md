# Round 2 leg 2 — a pass that needs no service

Card: `<card-id>` (round 2, leg 2). Governing: ADR-0001 sections 2-5,
ADR-0003 (read contract), the overhaul plan's round 2 acceptance.

Baseline on dispatch: `52a2975`, 651 passed / 2 deselected `-W error`, ruff
clean, hygiene `scanned=547 violations=0`.

## Slices landed

| slice | commit | gate |
|---|---|---|
| `chunks` population and per-file purge | `0102410` | G |
| vectors-off pass, embedding from `chunks`, `--vectors`, index-status fields | `54a7214` | G |
| three axes, policy matrix, per-side staleness, the two refusals | `9193abe` | G |
| tests, docs, this record | see the docs commit | G |

## The chunks table as written

`chunks(file, chunk_index, start_line, end_line, symbol, symbol_type,
chunk_hash, text)`, keyed `(file, chunk_index)`, no DDL change from revision 7.
`text` is the raw chunk text (the contextual header is composed at embed
time), `chunk_hash` the sha256 the chunker already computed. Written by the
same pass that reads the file, in the same commit family as the file row;
`delete_files` and the orphan sweep purge chunk rows with the file.

## Service-free pass, measured

Throwaway root, `OLLAMA_URL` and `QDRANT_URL` both `http://127.0.0.1:9`
(closed port):

* `add-project` exits 0. Manifest: files=2, symbols=4, refs=3, imports=1,
  chunks=4, chunks_fts=4, index_errors=0.
* meta: `schema_version=7`, `index_schema_version=7`, `extraction_version=5`,
  `chunker_version=1`, `vectors=off`, `vectors_state=absent`,
  `embedding_version` absent (the vector side was never touched).
* `index-status --json` -> `{rows: current, vectors: off, vectors_state: absent}`,
  `schema=1`; the text line prints all three.

## Axes and policy matrix, as implemented

| axis | recorded | bump invalidates |
|---|---|---|
| `extraction_version` | integer in `meta` (5) | rows: one pass re-parses, the collection is untouched |
| `embedding_version` | one string `model/dim/format` | the collection: a new identity-named collection is built from stored chunks, aliased in, old dropped |
| `index_schema_version` | integer in `meta` (7) | nothing but the manifest: migrated on open |

Collection names carry the identity digest (`idx_<slug>__<8 hex>`); the slug
stays the alias every read and write uses. A changed identity clears the
chunk-hash cache so nothing is reused across models.

## Per-side fingerprint

`fingerprint_status` returns the row verdict (`ok`, `state`, `reason`) and a
vector verdict (`vectors: current|stale|absent`, `vectors_reason`). A row
surface refuses only when the row axes differ; an embedding-only mismatch
leaves the rows answerable and reports `vectors_state=stale`.

`semantic-search` on `vectors=off` raises `ConfigError` naming
`--vectors on`, exits 4 (the rebuild class ADR-0003 names "vectors absent"),
stdout empty. Dense/hybrid refuse; `--mode lexical` still answers from FTS.
An embedding-identity mismatch refuses through the existing gate with the same
code, naming `reindex-project`.

## Verification

* `uv run pytest tests/ -q -W error` -> 661 passed, 2 deselected.
* `uv run pytest tests/test_vectors_off.py tests/test_vectors_on_reads_no_sources.py -q`
  -> 3 passed.
* `uv run pytest tests/test_refusal_needs_reindex.py -q` -> 27 passed.
* `uvx ruff check src tests scripts` -> clean.
* hygiene -> `scanned=552 violations=0`.

## Findings

* The house `code-indexer` reads refuse on this checkout: the live index was
  written by extraction 4 and leg 1 moved the version to 5, so exploration
  went through direct file reads. That is the refusal matrix working; the
  round's one operational reindex happens after the gate.
* No schema revision was needed: revision 7 already had the `chunks` table and
  the axes' meta keys. Still one revision, one reparse for the round.
* No live collection was rebuilt; every swap was exercised against stub
  stores only.

## Deferred

Nothing in this leg's scope is deferred. Structural filters, plan commands and
provider work are the following rounds' by the plan.
