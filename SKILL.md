---
name: mcp-code-indexer
description: Automatic semantic code index over local project dirs (Ollama + Qdrant) via MCP. Use when the agent needs semantic search over a codebase — the indexer handles indexing/staleness itself.
---

# mcp-code-indexer

MCP stdio server that keeps automatic semantic indexes of registered local
project directories in Qdrant (embeddings via Ollama `qwen3-embedding:8b`,
4096-dim; tree-sitter AST chunking, regex-window fallback). LAN-local.

## When to use

- "Where is X implemented in this repo?" / "find code that does Y" — semantic
  code search across one or many projects, ranked with file:line, symbol, and
  snippet.
- Do NOT use for: prose/document memory (that is `mcp-ollama-qdrant`).

## Tools (stdio MCP, seven)

| Tool | Params | Returns |
|---|---|---|
| `add_project` | `path: str` (absolute dir), `name?: str` (custom collection name) | `registered <path> (slug <slug>); initial indexing started in background`. **Idempotent**: already-registered path returns `already registered — index status: path=… slug=… state=… files=… chunks=… last_indexed=…` without spawning a re-index. With `name`, the collection is `idx_<name>` (sanitized to `[A-Za-z0-9_-]`, 1-64 chars, collision-checked) instead of the auto hash slug `idx_{hash}`; omit for default. |
| `lookup_project` | `path: str` | Registration check + collection-name lookup in one call, side-effect free (never registers or indexes). Registered: `registered: [name=<custom>] path=… slug=… state=… files=… chunks=… last_indexed=… collection=idx_<slug>`. Unregistered: `not registered: <path>`. Normalizes paths (tilde, relative, trailing slash; symlinked paths matched via real path). **Prefer this over calling `add_project` just to test registration.** |
| `remove_project` | `path: str` | Confirmation that the Qdrant collection, manifest, and registry entry were deleted. **DESTRUCTIVE** — the whole index is dropped; re-adding starts a fresh full index. |
| `list_projects` | — | One line per project: path, slug, files, chunks, last_indexed, state. |
| `semantic_search` | `query: str`, `project?: str` (path, slug, or custom name; omit = all), `limit: int = 8`, `file_filter?: str` (glob/substring on path, e.g. `*.py`) | Score-sorted lines `[score] project::file:start-end (symbol)` + 200-char snippet. |
| `index_status` | `path: str` | `project=… state=… last_pass=… [error=…]`; also triggers the staleness check. |
| `reindex_project` | `path: str` | `full reindex queued` — full rebuild in background. |

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
- First index after `add_project` runs on a background thread; results are
  incomplete until `index_status` reports `state=idle` with a last_pass.
- **Edits re-index themselves**: registered roots are watched via inotify;
  ~`WATCH_DEBOUNCE` (3 s) after your last write, the server incrementally
  re-indexes changed files — no tool call needed. `semantic_search` results
  are therefore fresh even if nobody searched since the edit.
- Search without `project` searches ALL registered projects (slowest); pass
  `project` for a known repo.

## Configuration

CLI flags > env vars > defaults. Flags: `--ollama-url --qdrant-url
--embed-model --index-root`.

| Env var | Default |
|---|---|
| `OLLAMA_URL` | `http://192.168.X.X:11434` |
| `QDRANT_URL` | `http://192.168.X.X:6333` |
| `EMBED_MODEL` | `qwen3-embedding:8b` |
| `INDEX_ROOT` | `~/.mcp-code-indexer` |
| `STALE_TTL` | `60` |
| `WATCH_DEBOUNCE` | `3` (watcher quiet period before re-index) |
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
- Files >1 MB, binary/non-UTF-8, `.git`/`node_modules`/`venv`/`__pycache__`/
  `dist`/`build`/`target` are skipped; `.gitignore` and `.codeindexignore`
  are honored — don't expect hits in those.
- Chunking is content-hash-based, so branch switches re-index correctly
  (mtimes are ignored); point IDs are deterministic, so re-index upserts are
  idempotent.
- Multiple MCP client processes are safe (per-project lock files, WAL SQLite);
  two servers indexing the same project concurrently is wasteful, not corrupt.