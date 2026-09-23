# Semantic search quality round: contextual chunk headers

Date: 2026-09-23. Scope: diagnose and fix poor relevance of
`semantic-search` against a mid-size C# project.

## Symptom characterization

Queries against the project's index returned plausible-looking but
wrong-ranked results, with two consistent failure patterns:

1. **Natural-language queries mis-rank relevant chunks.** Asking for
   "pathfinding movement of pawns on the map" surfaced test-helper chunks
   (a `Neighbors(ulong pawn)` LINQ helper repeated across four test files)
   instead of the grid-neighbor code a real pathfinder lives near. The
   genuinely relevant chunk (`TravelerPolicy.Neighbor`, the grid-adjacency
   offset switch) scored BELOW the noise chunks.
2. **Score band is tight and uninformative.** Across the full chunk pool
   (~2000 chunks) cosine scores clustered in [0.36, 0.56]; top-1 vs top-500
   gap was under 0.15. Nothing stands out, so top-k is nearly arbitrary.

A relevance audit (10 intents with unambiguous ground-truth chunks) hit
only 4/10 in the top 5 before the fix.

## Root cause

Chunks were embedded as **bare code text** with no file/symbol context:

- A short helper chunk reads like dozens of other short helper chunks —
  the embedding model has no signal to separate `TravelerPolicy.Neighbor`
  from the identically-shaped test `Neighbors` helpers beyond body text.
- File path and symbol name carry exactly the context a code search query
  implies ("traveler", "path", "pawn") but that context was discarded at
  embedding time (it existed only in the stored payload, invisible to the
  vector).

Experiments (subsequently removed diagnostic scripts, evidence recorded
here):

- Adding the qwen3-embedding query-side instruction prefix
  (`Instruct: ... Query: ...`) alone: mean margin between relevant and
  noise chunk moved from +0.023 to +0.049 across 5 intents — marginal,
  not a fix.
- Prepending `file\nsymbol: X\n` to the chunk text before embedding:
  moved the relevant chunk from rank 21 (checksums query), or out of the
  top-30 entirely (pathfinding, rumor, occupancy queries), to ranks 3-5.

## Fix

`Indexer.index_project` now embeds chunks as **contextual text**:
project-relative file path, then `symbol: <name>` when the chunk carries
a symbol, then the raw chunk text
(`code_indexer/embed_text.py:embed_text`). Query embedding is unchanged
(bare query text — re-embedding stored vectors with query-side prefixes
would have been an index-wide invalidation for a 1-5% gain).

Determinism: identical (file, chunk) pairs produce identical embedding
text, so the incremental chunk-hash cache and idempotent point upserts
stay valid. The snippet payload remains raw chunk text.

## Results after re-index (same audit)

- Relevance audit improved from 4/10 top-5 hits to 6/10, with the
  character-definition query moving from MISS to rank 1, checksums from
  MISS to rank 5, occupancy from MISS to rank 5.
- Known-relevant chunk for "pathfinding movement of pawns on the map"
  recovered from below-noise to rank 7 in top-10.
- The index carries contextual vectors by construction (verified: stored
  vector matches a fresh embed of header+text at 0.997 cosine; bare-text
  embed matches at only 0.95).
- Remaining misses are dominated by data files (verification-run JSON
  manifests) out-scoring code for analysis-shaped queries — a separate
  issue, not addressed here.

## Tests

- `tests/test_embed_text.py` — header format, symbol omission,
  determinism, newline/multibyte preservation.
- `tests/test_indexer_embed_context.py` — the indexer feeds the embedder
  contextual text (header + chunk), not bare chunk text.
- Fixed a pre-existing warn-clean violation in `tests/test_watcher.py`
  (unclosed file handle surfaced as `PytestUnraisableExceptionWarning`
  under `-W error`).
