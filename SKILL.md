---
name: code-indexer-mcp
description: "Use for agent semantic code search via code-indexer MCP tools."
---

# code-indexer-mcp

MCP stdio server (entry point `code-indexer-mcp`, run with
`uv run code-indexer-mcp` from `/home/keum/dev/athena/code-indexer/`) over the
same core as the `code-indexer` one-shot CLI. Keeps automatic semantic indexes
of registered local
project directories in Qdrant (embeddings via Ollama `qwen3-embedding:8b`,
4096-dim; tree-sitter AST chunking, regex-window fallback). LAN-local.

## When to use

- "Where is X implemented in this repo?" / "find code that does Y" — semantic
  code search across one or many projects, ranked with file:line, symbol, and
  snippet.
- Do NOT use for: prose/document memory (that is `vector-memory`).

## Tools (stdio MCP, ten)

| Tool | Params | Returns |
|---|---|---|
| `add_project` | `path: str` (absolute dir), `name?: str` (custom collection name) | `registered <path> (slug <slug>); initial indexing started in background`. **Idempotent**: already-registered path returns `already registered — index status: path=… slug=… state=… files=… chunks=… last_indexed=…` without spawning a re-index. With `name`, the collection is `idx_<name>` (sanitized to `[A-Za-z0-9_-]`, 1-64 chars, collision-checked) instead of the auto hash slug `idx_{hash}`; omit for default. |
| `lookup_project` | `path: str` | Registration check + collection-name lookup in one call, side-effect free (never registers or indexes). Registered: `registered: [name=<custom>] path=… slug=… state=… files=… chunks=… last_indexed=… collection=idx_<slug>`. Unregistered: `not registered: <path>`. Normalizes paths (tilde, relative, trailing slash; symlinked paths matched via real path). **Prefer this over calling `add_project` just to test registration.** |
| `remove_project` | `path: str` | Confirmation that the Qdrant collection, manifest, and registry entry were deleted. **DESTRUCTIVE** — the whole index is dropped; re-adding starts a fresh full index. |
| `list_projects` | — | One line per project: path, slug, files, chunks, last_indexed, state. |
| `semantic_search` | `query: str`, `project?: str` (path, slug, or custom name; omit = all), `limit: int = 8`, `file_filter?: str` (glob/substring on path, e.g. `*.py`) | Score-sorted lines `[score] project::file:start-end (symbol)` + 200-char snippet. |
| `index_status` | `path: str` | `project=… state=… last_pass=… [error=…]`; also triggers the staleness check. |
| `reindex_project` | `path: str` | `full reindex queued` — full rebuild in background. |
| `find_symbol` | `name: str`, `project?: str`, `substring?: bool = false` | Symbol-index lookup (SQLite manifest): `[confidence] file:start-end name (type)`, exact name match (`COLLATE NOCASE`) by default, capped labeled substring fallback if nothing matches exactly. `confidence=exact` = extracted from tree-sitter AST; `heuristic` = regex-chunked file. |
| `find_definition` | `name: str`, `project?: str` | Exact-match symbols only (no substring fallback). |
| `get_code_context` | `file: str`, `start_line?: int`, `end_line?: int`, `symbol?: str`, `project?: str`, `context_lines?: int = 0` | **Returns only the requested source lines — prefer this over reading whole files.** Two forms: line-range (`start_line`/`end_line`) or symbol= (resolves via the symbol index; all matches returned, capped 5). Path is resolved against the registered project root; escapes (`../`) are rejected. Serves from disk — may drift if the file changed since the last index pass. |
| `find_references` | `name: str`, `project?: str`, `relationship?: str` (`calls`\|`includes`\|...) | Lines `file:line (src_symbol → target, relationship, confidence)`. One `symbol_refs` table; call edges are textual and honestly labeled `heuristic` — navigation aid, not static analysis. |

## Code navigation workflow (token-efficient — use it)

```
semantic_search()  →  find_symbol/find_definition  →  get_code_context(symbol=)
```

Never read a whole file to inspect one symbol; `get_code_context` returns just
the relevant range. Note: pre-schema-v2 manifests have no symbol rows until the
next incremental pass or one `reindex_project` — an empty `find_symbol` on an
old project returns a hint saying so.

## Custom collection names

`add_project(path, name="myproject")` names the collection yourself instead
of the auto `idx_{hash}` slug: sanitized to `[A-Za-z0-9_-]` (1-64 chars,
others → `_`), stored in the registry, collision-checked. This makes a
project's collection predictable and shareable — e.g. `semantic_search`
also accepts `project` as the slug or custom name, not just the path.
Default (no `name`) remains the deterministic hash slug; existing
registrations keep theirs (additive, migration-safe registry change).

## When indexing is in progress (IMPORTANT — read before trusting search results)

- **Call `index_status` after `add_project`, and before trusting any "no
  results" answer on a large or freshly-added repo.** First indexing runs on
  a background thread and can take minutes-to-hours on a big codebase.
- **While `index_status` reports `state=indexing`, search results are
  PARTIAL** — only chunks already indexed at that moment are searched. An
  empty or thin result set during indexing does NOT mean the code isn't
  there; it means it isn't indexed yet.
- **Empty result on a freshly-added large repo = not an answer.** Poll
  `index_status` until `state=idle` (with a `last_pass` timestamp) before
  concluding "not found" or re-searching. Don't fall back to grep/reading
  files based on a search that raced the indexer.
- **Unsupported languages fall back to regex chunking.** Languages without a
  tree-sitter parser are chunked with a fixed-size regex window instead of
  AST-aware boundaries — hits are still valid, but `symbol` metadata may be
  coarser or missing for those files.

## Core behavior rules for agents

- **The indexer indexes itself; you never manage chunks.** Never manually save
  project details, chunk data, hashes, or collection contents — the server
  owns manifests (`INDEX_ROOT/<slug>/manifest.db`), the registry, point IDs
  (deterministic uuid5), and the `idx_{slug}` collection. Saving project state
  yourself duplicates server state and goes stale.
- `semantic_search` and `index_status` transparently run a staleness check:
  if the project wasn't scanned within `STALE_TTL` (default 60 s), changed
  files are incrementally re-indexed first. A search can therefore block a few
  seconds while a re-scan runs; long cold-start embeds happen only when
  content actually changed.
- Search without `project` searches ALL registered projects (slowest); pass
  `project` for a known repo. There is no background watcher: staleness is
  enforced by the STALE_TTL probe on every search/status call (CLI one-shot
  process model — no daemon).
- Run the server with `uv run code-indexer-mcp` from the repo directory
  (`/home/keum/dev/athena/code-indexer/`); the one-shot CLI binary is
  `code-indexer` (see the `code-indexer` skill).

## Configuration

CLI flags > env vars > defaults. Flags: `--ollama-url --qdrant-url
--embed-model --index-root`.

| Env var | Default |
|---|---|
| `OLLAMA_URL` | `http://192.168.X.X:11434` |
| `QDRANT_URL` | `http://192.168.X.X:6333` |
| `EMBED_MODEL` | `qwen3-embedding:8b` |
| `INDEX_ROOT` | `~/.code-indexer` |
| `STALE_TTL` | `60` |
| `EMBED_BATCH` / `UPSERT_BATCH` / `MAX_FILE_BYTES` | 48 / 256 / 1048576 |

## Gotchas

- **First-index latency depends on the Ollama host**: measured ~1.2 docs/s
  (GPU passthrough, 8B model, 4096-dim, batch 48) vs ~0.17 docs/s CPU-only —
  a large repo's initial index can still take a while on CPU. Incremental
  updates after that are seconds-to-minutes per edit session.
- **add_project is idempotent** — calling it twice returns the current index
  status summary; only genuinely nonexistent paths error. To merely *check*
  registration (no side effects), use `lookup_project` instead.
- **remove_project is destructive**: drops the Qdrant collection + manifest +
  registry entry. Re-adding later re-indexes from scratch.
- Existing `idx_*` collections are reused if the registry is retained; a fresh
  registry re-add re-attaches to the same collection and re-indexes (chunk-hash
  diff means only changed chunks are re-embedded; deterministic uuid5 point IDs
  make re-upserts idempotent).
- Files >1 MB, binary/non-UTF-8, `.git`/`node_modules`/`venv`/`__pycache__`/
  `dist`/`build`/`target` are skipped; `.gitignore` and `.codeindexignore`
  are honored — don't expect hits in those.
- Chunking is content-hash-based, so branch switches re-index correctly
  (mtimes are ignored); point IDs are deterministic, so re-index upserts are
  idempotent.
- Multiple MCP client processes are safe (per-project flock locks, WAL
  SQLite with busy_timeout); two processes indexing the same project
  concurrently is wasteful, not corrupt — the flock serializes to one pass.
- Upgrading from old `mcp-code-indexer` registrations: run `code-indexer add`
  for each project; existing `idx_*` collections are reused, no reindex
  required (state dir moved to `~/.code-indexer`).