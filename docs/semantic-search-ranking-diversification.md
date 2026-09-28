# Semantic search ranking diversification round

Date: 2026-09-23. Scope: `semantic_search` ranked purely by vector cosine
similarity; this round adds opt-in ranking modes and interface filters.

## Starting point (verified against current code)

- Contextual chunk headers at embed time were already shipped (commit
  `564ecaa`, `docs/semantic-search-contextual-headers.md`); query-side
  ranking was still single-signal cosine.
- The `symbol_type`/`lang` payload fields existed but had no query-side
  filter surface — only `file_filter` was plumbable.

## What was added

### 1. `ranking` option — three query-time modes

New module `code_indexer/ranking.py` (pure functions, unit-tested without
Qdrant/Ollama). `semantic_search` accepts `ranking`:

- **`vector`** (default) — pure cosine ranking; the candidate pool is
  over-fetched like the other modes (the data-file mitigation below needs
  candidates to promote), and the ranking itself is unchanged.
- **`metadata`** — small additive adjustments from payload facts already
  stored at index time:
  - `+0.02` (definition boost) when the chunk carries a symbol
    (definition-shaped) over bare reference/comment chunks;
  - `-0.05` (test-path penalty) for chunks under test/fixture/vendor path
    segments, waived when the query itself mentions tests.
- **`hybrid`** — weighted-sum fusion of the cosine score with a lightweight
  lexical token-overlap score over (symbol name, file path, snippet):
  `fused = cosine + 0.25 * lexical`. The lexical component rewards fraction
  of distinct query tokens present in the hit text plus a bonus when one
  appears in the symbol name itself.

  Weighted-sum was chosen over reciprocal rank fusion deliberately: this
  project's embeddings produce a tight cosine band (< 0.2 from top-1 to
  tail), where RRF damps a one-rank lexical gain into irrelevance — an RRF
  prototype measurably failed to promote exact-identifier matches past
  generic lookalikes and was replaced before commit.

In every mode the candidate pool is over-fetched (3× limit, min 24) before
the query-time ranking and truncated back to `limit` after; `metadata` and
`hybrid` results additionally carry `vector_score` and
`lexical_score`/`metadata_delta` for observability.

### 2. `symbol_type` / `language` filters

`semantic_search` accepts `symbol_type` (function|method|class|struct|enum|
namespace — payload exact match) and `language` (python|csharp|cpp|... —
payload exact match). Implemented as Qdrant payload filters in `Store.search`
so they cost nothing extra; results now also include `lang` in the stable
JSON field contract.

### 3. Two-stage rerank — evaluated, deferred

A second-pass rerank with a cross-encoder/Ollama pair-scorer was considered
and deferred: it costs one extra LLM scoring call per candidate per query
(hundreds of pairs at limit 30), which on this LAN setup adds seconds to the
hot path for a gain that the measured lexical fusion already captures at
zero latency. Revisit only if hybrid fusion proves insufficient on real
workloads.

### 4. Data-file dominance mitigation (all modes, including the default)

A follow-up round. Pure-data payload chunks (`json`/`yaml`/`toml`) carry no
code, but their self-descriptive keys match analysis-shaped query vocabulary
better than the code that produces the data, so they could occupy most of a
top-k window. Two query-time steps are applied to every result set
regardless of `--ranking`:

- **down-weight** — pure-data chunks lose a small constant (0.03) on the
  score; code chunks within that delta overtake them. The original cosine is
  preserved in `vector_score` and the applied delta recorded in
  `data_penalty`.
- **top-k share cap** — at most 40% of the `limit` window may be pure-data
  chunks (floor, minimum 1); any further pure-data chunks are moved behind
  the non-data hits. Nothing is dropped and data files stay searchable —
  they just cannot crowd code out of the window.

Both steps are waived when the query itself names a data format
(`json`/`yaml`/`toml`), i.e. when the caller is deliberately looking at a
data file.

Live check against the indexed C# project (same process, same index snapshot,
mitigation monkeypatched off vs on, `--limit 5`): "summarize the metrics
recorded in the latest validation run" went 4/5 → 1/5 data-file chunks with
the code that records the telemetry promoted to rank 1, and "explain the
telemetry trajectory recorded in the run data" went 3/5 → 1/5. For other
phrasings the window was already code-dominated and the mitigation changed
nothing (`what differs between two verification run manifests` 0/5 either
way) — the effect is query-dependent by nature, since the dominance itself
is.

The down-weight magnitude (0.03) and the window share (40%) are the two
knobs; both are module constants in `code_indexer/ranking.py`.

## Measurement

Harness: `scripts/ranking_eval.py` — 8 verified intents (5 vivarium-sim, 3
calc-engine), ground-truth symbols confirmed via `find-symbol`/`skeleton`
before scoring; rank of the designated chunk per mode, limit 30, live local
indexes, embedding latency amortized per query.

| mode     | top-1 | top-5 | top-10 | miss | avg ms |
|----------|-------|-------|--------|------|--------|
| vector   | 5/8   | 6/8   | 7/8    | 1    | ~48    |
| metadata | 6/8   | 7/8   | 7/8    | 1    | ~47    |
| hybrid   | 3/8   | 6/8   | 6/8    | 2    | ~48    |

Notable movements:

- "species vocabulary mentions as token boundary scan": a test with the
  intent words in its name led at rank 1 under vector; metadata demoted the
  test-path chunk and the production `SpeciesDefinition.MentionsAsToken`
  took rank 1.
- "pathfinding movement of pawns on the map": ground-truth
  `TravelerPolicy.Neighbor` at rank 7 under vector, rank 5 under metadata.
- "pawn suspicion decay witness level": test chunks polluting ranks 3-6
  under vector are pushed below the production file under metadata.

Why hybrid is not the default: on natural-language intents with many query
tokens the lexical component is noisy (fragments like "pawn" match dozens of
chunks; a few chunks winning the full token-fraction bonus outrank better
vector matches). It shows real value on exact-identifier-shaped queries
("retry on ECONNRESET") — keep it opt-in for those; `metadata` is the safe
quality win and the recommended non-default.

## Tests

- `tests/test_ranking.py` — tokenizer, lexical scoring, metadata deltas
  (definition boost, test-path penalty and its waiver), metadata re-rank
  ordering, hybrid fusion promotion/tie-stability/empty-pool, and the
  data-file mitigation (payload detection, down-weight, waiver, top-k share
  cap never dropping hits), all with synthetic hits.
- `tests/test_search_data_files.py` — the mitigation at the search seam with a
  stubbed store: over-fetch, code mixed into a data-heavy window, data files
  kept searchable, penalty recorded, waiver for data-targeted queries.
- `tests/test_concurrency.py` stub store extended for the new
  `symbol_type`/`language` parameters.

## Interface summary

CLI: `code-indexer semantic-search "query" [--ranking vector|metadata|hybrid]
[--symbol-type TYPE] [--language LANG]`.
MCP: `semantic_search(query, project?, limit=8, file_filter?,
symbol_type?, language?, ranking="vector", format="text")`.
Default behavior is unchanged for pre-round consumers (mode `vector`, no
filters); the JSON field contract gains a `lang` key.
