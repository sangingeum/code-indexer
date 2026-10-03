# Step 0 Audit — code-indexer improvement plan (2026-10-03)

Read-only audit of the repo against the improvement plan §3 (Step 0, items 1–9).
No behavior changes; this file is the deliverable and is intentionally left
uncommitted. All numbers were produced by real runs on this host (2026-10-03).

## 1. Entry points (plan CI-01)

- `[project.scripts]` defines `code-indexer = code_indexer.cli:main` and
  `code-indexer-mcp = code_indexer.server:main` (pyproject.toml).
- **README's MCP client config is confirmed WRONG**: README.md lines 346–356
  tell MCP clients to launch `uv --directory <path> run code-indexer` — the
  CLI, which does not speak the MCP stdio protocol. The correct command is
  `run code-indexer-mcp`.
- Real handshake test (tiny script over stdio JSON-RPC against
  `uv run code-indexer-mcp`):
  - `initialize` → serverInfo `{'name': 'code-indexer', 'version': '1.30.0'}`,
    protocolVersion `2024-11-05`; `tools/list` returned **14 tools**
    (lookup_project, add_project, remove_project, list_projects,
    semantic_search, index_status, reindex_project, find_symbol, find_symbols,
    skeleton, outline, find_definition, find_references, get_code_context).
  - Plan says "the 15 documented tools" — the actual count is **14**. Fix the
    plan/README wording, not the code.
  - stderr carried 2 lines during the handshake (logs go to stderr; stdout is
    clean MCP transport) — consistent with README's claim, worth asserting in
    the CI-01 smoke test.

## 2. Existing token-saving logic (plan [VERIFY] items)

EXISTS (implemented, tested, documented):
- **Ranking diversification** — `src/code_indexer/ranking.py`:
  `metadata_rerank` (definition boost +0.02, test/vendor path penalty 0.05),
  `hybrid_fuse` (lexical token-overlap fusion, weight 0.25) over a 3×-fetched
  candidate pool. Design: `docs/semantic-search-ranking-diversification.md`.
- **Pure-data chunk down-weighting** — `ranking.downweight_data_files`
  (−0.03 for json/yaml/toml chunks) + `cap_data_file_share` (≤40 % of the
  top-k window, floor 1), waived when the query names a data format.
  Always applied in all ranking modes (`core.search_one`). 16 tests in
  `tests/test_ranking.py`, 5 in `tests/test_search_data_files.py`.
- **Contextual embedding headers** — `embed_text.py` (path + symbol header,
  version-tagged `contextual-header-v1`). Design:
  `docs/semantic-search-contextual-headers.md`.
- `skeleton`/`outline`/`find-symbols --max-symbols-per-file` for cheap
  navigation output.

ONLY PLANNED (docs, not code):
- `docs/token-reduction-plan.md` lists `exports`/`public-api` command and
  `compress-context` (AST-minified chunk bodies) — not implemented.
- Per-file result caps on semantic-search (`--per-file`) — NOT implemented;
  what exists is the data-file share cap and metadata penalties, not a
  generic per-file diversification. CI-13's `[VERIFY: may exist as
  diversification]` resolves to: **does not exist** (the ranking modules
  above are the nearest neighbors).

## 3. Embedding text

- **Documents (chunks):** `embed_text(file, chunk)` = project-relative path,
  then `symbol: <qualified symbol>` when the chunk carries one, then the raw
  chunk text — joined with `\n`. Sent to Ollama `client.embed(model=…,
  input=batch, keep_alive="10m")`. No instruction prefix on documents.
- **Queries:** `core.search_one` embeds the raw query string verbatim —
  **no `Instruct:`/`Query:` prefix** is used. CI-14's query instruction is
  genuinely absent.
- Header is minimal (path + symbol only). CI-10's richer header (lang,
  signature, docstring) would be a `contextual-header-v2` bump; the version
  guard for that already exists (see §4).

## 4. Fingerprints

- Manifest `meta` table (schema v3) records: `schema_version`, `last_full_scan`,
  `branch`, `last_indexed`, `chunk_hashes` (cache), `embed_format`
  (`contextual-header-v1`).
- **NOT recorded:** embed model name, embed dimension, chunker version.
- What happens today if `EMBED_MODEL` changes to another 4096-dim model:
  nothing — the collection dimension matches, chunks hash identically (the
  cache key is the *chunk text* hash, not model+text), so stale vectors are
  silently reused and search quietly degrades. `EMBED_FORMAT` change IS
  guarded (full rebuild, `indexer.py` lines 67–97), so the CI-02 gap is
  exactly model/dim/chunker-version. Qdrant side: no model/dim metadata on
  the collection either (creation only sets size/distance).

## 5. Stale behavior — LIVE TEST (reproduced)

Procedure: baseline `semantic-search` → inserted 3 comment lines above
`DATA_FILE_PENALTY` in `src/code_indexer/ranking.py` within STALE_TTL (60 s) →
`semantic-search` again → `get-code-context` at the returned range.

Result: the hit `src/code_indexer/ranking.py:182-203 (downweight_data_files)`
was returned with the SAME stale range after the edit (the STALE_TTL probe
had already run seconds before). `get-code-context --start-line 182
--end-line 187` returned the `data_file_waived` tail plus blank lines —
the real `def downweight_data_files` had shifted to 185 — **with no warning
line and exit 0**. File was restored immediately after the test
(`git status` clean). CI-03 is confirmed as a real, reproducible bug.

## 6. Language coverage matrix (item 6)

Empirical probe: `EXT_LANG` in `ts_chunker.py` maps 27 extensions → 21
languages; each was parsed with a representative snippet
(tree-sitter-language-pack 1.18.0, tree-sitter 0.26.0). References/imports
are ALWAYS the regex pass in `ts_chunker.extract_refs` (textual, unbound,
`confidence=heuristic` for every language — there are no per-language
`.scm` queries).

| lang | AST chunking | symbols | signature | visibility | references |
|---|---|---|---|---|---|
| python | ast | ast | ast | ast (underscore rule) | heuristic |
| javascript (+jsx) | ast | ast | ast | heuristic only | heuristic |
| typescript (+tsx) | ast | ast | ast | heuristic | heuristic |
| rust | ast | ast | ast | ast (`pub` check) | heuristic |
| go | ast | ast | ast | heuristic | heuristic |
| java | ast | ast | ast | heuristic (default public) | heuristic |
| c | ast | ast | ast | heuristic (default public) | heuristic |
| cpp | ast | ast | ast | heuristic | heuristic |
| csharp | ast | ast | ast (name-field exact) | heuristic | heuristic |
| kotlin | ast | ast | ast | heuristic | heuristic |
| lua | ast | ast | ast | heuristic | heuristic |
| php | ast | ast | ast | heuristic | heuristic |
| swift | ast | ast | ast | heuristic | heuristic |
| bash | ast | symbol extraction WEAK (symbol=None in probe) | ast | heuristic | heuristic |
| ruby | **regex fallback** (grammar parse produced no AST chunks in probe) | regex guess (no type) | regex first-line | regex | heuristic |
| css, html, markdown, json, yaml, toml | regex window (by design — data/markup) | none | none | — | heuristic |

Notes for CI-25:
- bash AST chunking technically engages but yields no symbol — needs a probe
  fixture fix or bash demoted.
- ruby silently falls back to the regex chunker despite being mapped — the
  fallback is graceful, but `index-status` does not surface
  `languages_without_ast` yet.
- Visibility is real (ast) only for python/rust (+csharp name extraction);
  everything else defaults to public via the heuristic.

## 7. Cost baselines (this host, 2026-10-03)

- **First index of this repo** (fresh copy at /tmp, 54 files): 32.4 s wall
  for 588 chunks (~18 chunks/s; ~0.6 s/chunk — the qwen3-embedding:8b call
  dominates). Peak RSS ~304 MB.
- **Incremental pass** (1 file deleted, 1 added): 31.7 s / 579 embedded,
  0 reused — re-embeds everything with a changed chunk, per-chunk granularity
  means a full pass of an unchanged file still re-checks but reuses via
  chunk_hashes cache (0 reused here because the probe copy differs).
- **semantic-search latency (cold CLI, installed tool):** 1.11 s and 1.01 s
  (two different queries, single project, limit 8). With
  `--skip-stale-check`: 0.99 s. CLI startup+Qdrant round-trip ≈ 1 s
  regardless.
- **skeleton** one file (`core.py`): 0.97 s, 2.5 KB output. Directory
  skeleton on a 593-file project (vivarium-sim, one large file): 1.01 s,
  **488 KB** output for a single big file — the per-file path can be huge;
  directory-level output 52 KB.
- **outline** (`store.py`): 0.93 s, 811 B.
- **semantic-search --limit 8** output: text 2.2 KB, JSON 4.7 KB.
- Not measured: a ~5k-file repo first index (no such repo handy; vivarium-sim
  at 593 files took well under a minute in past rounds). Do this once when
  CI-04 lands.

## 8. Versions

- `qdrant-client` **1.14.3** (pinned `<1.15` — satisfies CI-11B's ≥1.10
  requirement for sparse vectors / `query_points`).
- Qdrant **server 1.19.1** at `http://192.168.1.105:6333`. The client emits a
  compatibility warning (1.14.3 vs 1.19.1, minor delta > 1). CI-11B is
  feasible client-side; consider bumping the pin or `check_compatibility`
  handling in `doctor` (CI-05).
- Ollama host serves `qwen3-embedding:8b` (7.6B, Q4_K_M, 4096-dim).
- tree-sitter 0.26.0, tree-sitter-language-pack 1.18.0, watchdog is a main
  dependency, Python pinned 3.12 (note: plan says 3.11; the repo moved to
  3.12 — plan's CI matrix should say 3.11+3.12 or just 3.12).

## 9. Test suite

`uv run pytest tests/ -q -W error`: **182 passed, 0 failed, 45.3 s**.

Gaps observed (map to plan items):
- No MCP stdio handshake smoke test against the real server process (CI-01);
  `test_mcp_server.py` (40 tests) exercises the wrapper functions, not a
  live stdio session.
- No fingerprint/mismatch test — nothing covers EMBED_MODEL change (CI-02).
- Stale line-range safety is only covered indirectly
  (`test_query_staleness.py`, 4 tests); no test of get-code-context on a
  shifted range (CI-03).
- Embedder resilience: no timeout/batch-bisect/resume tests (CI-04).
- No `doctor` (command doesn't exist; CI-05).
- No eval harness (CI-16), no FTS5/hybrid (CI-11A), no reranker interface
  test (CI-12), no output-budget tests (CI-13).
- Language fixtures: only `test_csharp_symbols.py` + `test_codeintel.py`
  cover non-python languages; no per-language snapshot matrix (CI-25).
- All tests are mock/offline — no `@pytest.mark.live` marker convention yet
  (CI-28).
- CI runs `-W error` (warn-clean bar) — good; keep it.

## Summary for the implementer

Already implemented (skip/adjust plan items):
- Ranking diversification + data-file down-weight/cap (CI-12 heuristic
  reranker partially exists; reuse `ranking.py`).
- Contextual embedding text v1 with version guard (CI-10's header exists in
  minimal form; richer header = v2 bump, mechanism ready).
- Chunk-hash reuse cache (partial CI-14 cache: per-manifest, not
  content-addressed across projects).
- MCP tool annotations on all 14 tools.

Confirmed missing / must build: README entry-point fix (CI-01, 14 not 15
tools), fingerprint table (CI-02), stale-safe ranges (CI-03 — bug
reproduced), embedder resilience (CI-04), doctor (CI-05), eval harness
(CI-16), query instruction (CI-14), FTS5 hybrid (CI-11), per-file caps and
budget shaping (CI-13), overview/changed-symbols/dep-graph (CI-20/21/22),
language matrix doc (CI-25).