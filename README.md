# mcp-code-indexer

An MCP stdio server that keeps an automatic semantic index of one or more
local project directories in Qdrant, using Ollama (`qwen3-embedding:8b`,
4096-dim) for embeddings. Fully LAN-local; no cloud.

**Agents never index anything themselves.** They only call `semantic_search`
(which transparently runs a staleness check + incremental indexing first) plus
a few admin tools. Chunks, hashes, and collections are never exposed.

## Tools

| Tool | Description |
|---|---|
| `add_project(path, name)` | Register a project directory; creates the Qdrant collection and starts a full initial index in the background. **Idempotent**: re-adding an already-registered path returns the current index status summary (state, files, chunks, last_indexed) and does NOT spawn a re-index — only nonexistent paths error. Optional `name` picks the collection name yourself (`idx_<name>`, sanitized to `[A-Za-z0-9_-]`, 1-64 chars, collision-checked) instead of the auto hash slug. |
| `lookup_project(path)` | Check registration without side effects: returns one line with path, slug, custom name, Qdrant collection name (`idx_<slug>`), state, files, chunks, last_indexed — or `not registered: <path>`. Normalizes paths (tilde, relative, trailing slash; symlink matched via real path). Use this instead of guessing via `add_project` idempotency. |
| `remove_project(path)` | Deregister and **delete** the Qdrant collection, SQLite manifest, and registry entry. |
| `list_projects()` | Registered projects with file/chunk counts, last-indexed time, and state. |
| `semantic_search(query, project?, limit=8, file_filter?)` | The hot path. Always runs a staleness check first; `project=None` searches all registered projects. `project` accepts a path, a slug, or a registered custom name. Returns file paths, line ranges, symbols, scores, snippets. |
| `index_status(path)` | `idle \| indexing \| error` + last-pass progress. |
| `reindex_project(path)` | Force a full rebuild. |

## How indexing / staleness works

- **First index** (`add_project`) runs on a background thread; the tool
  returns immediately. Use `index_status` to wait for completion.
- **Staleness check** (on every `semantic_search` / `index_status`): if the
  project hasn't been scanned within `STALE_TTL` seconds (default 60), the
  server re-scans file hashes and incrementally re-indexes only what changed.
  Answers are never served from a stale index by more than one scan interval.
- **Incremental diff**: files are classified `unchanged` / `changed` / `added`
  / `deleted` by **content hash** (sha256 — not mtime, which lies after branch
  switches). Only chunks whose own hash changed get re-embedded; point IDs are
  deterministic (`uuid5(project|file|chunk_index)`), so upserts are idempotent.
  Deleted files are purged from Qdrant by payload filter and dropped from the
  SQLite manifest.
- **Chunking**: tree-sitter AST units (function/class-level, with symbol
  names) via `tree-sitter-language-pack`, falling back to a ~80-line regex
  window chunker with 10-line overlap when a language is unsupported or the
  parse fails. Chunks are capped at ~1000 chars.
- **Filtering**: honors `.gitignore` and `.codeindexignore` (nested, per-
  directory), skips `.git`/`node_modules`/`venv`/`__pycache__`/`dist`/`build`
  /`target`, files > 1 MB, and binary/non-UTF-8 files.
- **Concurrency**: multiple MCP client processes (multiple agents) are safe —
  per-project `O_EXCL` lock files (stale locks stolen after 30 min), WAL-mode
  SQLite, idempotent point IDs. Two servers indexing the same project at once
  is wasteful, not corrupting.

## State layout

```
$INDEX_ROOT/               (default ~/.mcp-code-indexer)
├── registry.db            # path -> slug mapping (SQLite, WAL)
├── <slug>.lock            # per-project lock
└── <slug>/manifest.db     # per-project file manifest (SQLite, WAL)
```

Qdrant holds one collection per project: `idx_{slug}` where slug is an 8-hex
hash of the absolute path — or `idx_<name>` when the project was registered
with a custom `name`.

## Configuration

Resolution order: CLI flags > environment variables > defaults.

| Env var | Default | Description |
|---|---|---|
| `OLLAMA_URL` | `http://192.168.X.X:11434` | Ollama base URL |
| `QDRANT_URL` | `http://192.168.X.X:6333` | Qdrant base URL |
| `EMBED_MODEL` | `qwen3-embedding:8b` | Embedding model |
| `INDEX_ROOT` | `~/.mcp-code-indexer` | State directory |
| `STALE_TTL` | `60` | Seconds between staleness re-scans |
| `EMBED_BATCH` | `48` | Texts per Ollama embed request |
| `UPSERT_BATCH` | `256` | Points per Qdrant upsert |
| `MAX_FILE_BYTES` | `1048576` | Skip files larger than this |

CLI flags: `--ollama-url`, `--qdrant-url`, `--embed-model`, `--index-root`.

## MCP client config (Claude Desktop / Hermes / any stdio MCP client)

```json
{
  "mcpServers": {
    "mcp-code-indexer": {
      "command": "uv",
      "args": [
        "--directory", "/path/to/mcp-code-indexer",
        "run", "mcp-code-indexer"
      ],
      "env": {
        "OLLAMA_URL": "http://192.168.X.X:11434",
        "QDRANT_URL": "http://192.168.X.X:6333"
      }
    }
  }
}
```

## Performance note

Embedding throughput depends on the Ollama host: measured **~1.2 docs/s with
GPU passthrough** (8B model, 4096-dim, batch 48) vs ~0.17 docs/s CPU-only.
The **first index of a large repo is the slow part** (thousands of chunks);
incremental updates
only re-embed changed chunks, so a typical edit session re-indexes in
seconds-to-minutes. Qdrant upsert throughput is ~550 pts/s. Embeddings are
batched (one HTTP round trip per 48 inputs) with keep_alive to avoid model
unload between batches.

## Development

```bash
uv sync                                  # install deps (.venv)
uv run mcp-code-indexer                  # run the stdio server
uv run pytest                            # unit tests
uv run python scripts/benchmark.py       # M0 embed-throughput benchmark
uv run python scripts/stdio_probe.py     # stdio handshake + tools/list probe
uv run python scripts/e2e.py             # end-to-end test (real repo, real backends)
uv run python scripts/concurrency_smoke.py
```

Python 3.11. Constraints: pins `numpy<2` (1.26.4), `qdrant-client<1.15`,
`mcp<2`, `tree-sitter==0.26.0`, `tree-sitter-language-pack==1.18.0`
(older x86-64 CPUs without x86-64-v2; all pure/prebuilt wheels).