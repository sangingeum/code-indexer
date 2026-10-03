# code-indexer

[![M8ven Verified](https://m8ven.ai/badge/mcp/sangingeum-code-indexer-1t93x3?variant=verified)](https://m8ven.ai/mcp/sangingeum/code-indexer)

Semantic code index over Ollama + Qdrant, with a one-shot CLI
(`code-indexer`, typer) and an MCP stdio server (`code-indexer-mcp`) that is a
**thin wrapper over that same CLI**: every MCP tool builds the argv of the
matching `code-indexer` subcommand and runs it, relaying its output — one code
path, so the two surfaces can never diverge. The index maps one or more local
project directories in Qdrant, using Ollama (`qwen3-embedding:8b`, 4096-dim)
for embeddings. Fully LAN-local; no cloud.

**Agents never index anything themselves.** They only call `semantic-search`
(which transparently runs a staleness check + incremental indexing first) plus
a few admin subcommands. Chunks, hashes, and collections are never exposed.

**Full command reference:** [SKILL-CLI.md](SKILL-CLI.md) — the agent-facing
document with the decision tree, per-command options, and index-state
meanings. This README is the overview; each fact lives in exactly one place.

## Installation (CLI)

From the repo directory, install both executables as editable `uv` tools (on
PATH in `~/.local/bin`, edits to the checkout take effect immediately):

```bash
uv tool install -e .
```

This installs `code-indexer` (and the optional `code-indexer-mcp` server
executable). Verify with `code-indexer list-projects`.

## Quick start

```bash
code-indexer add-project /path/to/repo        # register + initial index (foreground)
code-indexer semantic-search "auth token refresh" --project /path/to/repo --format compact
code-indexer overview --project /path/to/repo # project map for orientation
code-indexer index-status /path/to/repo       # indexing state
code-indexer watch /path/to/repo --background # keep the index fresh (opt-in)
```

The agent workflow (overview → skeleton → search → get-code-context) is in
[SKILL-CLI.md](SKILL-CLI.md).

## Architecture

- **Index**: tree-sitter AST chunks (function/class-level, symbol names,
  schema v3 signatures/visibility) with contextual headers, embedded via
  Ollama into one Qdrant collection per project (`idx_<slug>` / `idx_<name>`).
- **Manifest** (SQLite, WAL): files, chunks, content hashes, symbol index,
  FTS5 lexical table, index fingerprint.
- **CLI** (`code-indexer`): every operation, one-shot, foreground; per-project
  flock serializes concurrent passes.
- **MCP server** (`code-indexer-mcp`): thin wrapper over the same CLI
  subprocess; 18 tools, all foreground.

### CLI ↔ MCP parity

| CLI subcommand | MCP tool |
|---|---|
| `add-project` | `add_project` |
| `lookup-project` | `lookup_project` |
| `remove-project` | `remove_project` |
| `list-projects` | `list_projects` |
| `semantic-search` | `semantic_search` |
| `index-status` | `index_status` |
| `reindex-project` | `reindex_project` |
| `find-symbol` / browse mode | `find_symbol` / `find_symbols` |
| `skeleton` (alias `map`) | `skeleton` |
| `outline` (alias `file-outline`) | `outline` |
| `find-definition` | `find_definition` |
| `find-references` | `find_references` |
| `find-callers` | `find_callers` |
| `find-callees` | `find_callees` |
| `deps` | `deps` |
| `changed-symbols` | `changed_symbols` |
| `get-code-context` | `get_code_context` |
| `overview`, `index-more`, `doctor`, `watch`, `unwatch`, `eval`, `eval-compare` | **CLI-only** (no MCP tool) |

MCP options are a subset of CLI flags; the MCP tool schemas (see
`src/code_indexer/server.py`) are the source of truth for what each tool
accepts — e.g. `semantic_search` exposes `project`, `limit`, `file_filter`,
`symbol_type`, `language`, `ranking`, `format`, `fresh`, `per_file`,
`max_tokens`, `rerank`, `mode`. There is no MCP `watch`/`unwatch`: a watcher
makes no sense inside an already long-lived server.

## Index states

Canonical states, the same everywhere (CLI and MCP):

| State | Meaning | Agent action |
|---|---|---|
| `idle` | indexed, no pass running | normal use |
| `indexing` | a pass is running | wait/retry |
| `never-indexed` | registered but no completed pass (interrupted `add-project`) | do not trust empty results; tell the owner |
| `needs-reindex` | index built with a different embed model/dimension/text format/chunker | do not run `reindex-project` yourself; report to the owner (queries fail with `ConfigError` until then) |
| `error` | last pass failed | run `code-indexer doctor` |

## How indexing / staleness works

- **First index** (`add-project`, CLI and MCP alike) runs in the
  **foreground**: the call returns when indexing finishes. If it is
  interrupted, the project stays registered but reports
  `state=never-indexed` (`files=0 chunks=0 last_indexed=never`);
  `reindex-project` builds it.
- **Staleness**: searches/status trigger a re-scan when `STALE_TTL` (60 s)
  elapsed. The scan uses a stat fast-path (`size, mtime_ns, inode` match ⇒ no
  re-hash; `PARANOID_HASH=1` disables it). `--fresh` forces the scan now;
  `--skip-stale-check` skips the probe. Hits carry `file_hash` and
  `indexed_at`; `get-code-context` re-resolves `--symbol` on the live file or
  returns line ranges with a stderr warning and `stale: true` in JSON when
  the file changed.
- **Incremental diff**: files are classified by **content hash** (sha256, not
  mtime — branch switches re-index correctly). Only chunks whose hash changed
  are re-embedded; point IDs are deterministic uuid5, so upserts are
  idempotent; deleted files are purged.
- **Chunking**: ~1000-char cap (`CHUNK_MAX_CHARS`); tree-sitter AST units,
  regex-window fallback. The embedded text carries a contextual header
  (`embed_text.py`, format `contextual-header-v1`).
- **Fingerprint**: the manifest records `embed_model`, `embed_dim`,
  `embed_text_version`, `chunker_version`. Any mismatch ⇒ `needs-reindex`
  (see table above). `--skip-stale-check` does not bypass this gate.
- **Filtering**: `.gitignore`/`.codeindexignore` (nested), `--include` scope
  globs, secret-bearing file skips (filename globs + high-confidence content
  patterns; `--allow-sensitive` per-project override), files > 1 MB
  (`MAX_FILE_BYTES`), binaries; pure-data chunks are down-weighted in
  ranking.
- **Concurrency**: per-project `flock` + WAL SQLite; two processes indexing
  one project serialize into exactly one pass ("indexing in progress").

## Schema history

| Version | What changed | Migration |
|---|---|---|
| v3 | `signature`/`visibility` columns on `symbols` | automatic ALTER; old rows NULL until `reindex-project` |
| v4 | index fingerprint + stat fast-path columns on `files` | automatic ALTER; fingerprint backfills on next pass |
| v5 | `files.loc`/`files.language` for `overview`; graph tables (refs/imports for `find-callers`/`find-callees`/`deps`) | automatic ALTER; overview/graph data populate on next reindex |
| — | FTS5 lexical table (`chunks_fts`, hybrid retrieval) | automatic backfill on next pass |

Current schema version: **5** (auto-migrates on open; `reindex-project`
backfills derived columns eagerly).

## Configuration

Resolution order: CLI flags > environment variables > defaults. Full list
with defaults and one-line descriptions: [SKILL-CLI.md §Key rules](SKILL-CLI.md#key-rules).
Note: `OLLAMA_URL`/`QDRANT_URL` defaults are LAN placeholders and must be set.

## Platform support

Linux (inotify via watchdog) is the supported platform. Watcher degradation
to quiet-period polling happens on inotify exhaustion; Windows is unsupported
(the locking uses `fcntl`). The index itself is pure SQLite/Qdrant and is
platform-neutral; only `watch`/locking are POSIX-bound.

## Troubleshooting

Run `code-indexer doctor` — one line per check (`ok|warn|fail <name>:
<detail>`), exit 1 on any fail. Covers runtime versions, Ollama
(reachable/model/dimension), Qdrant (reachable/version/collections/dims),
INDEX_ROOT writability + disk, SQLite integrity, watcher pidfile/locks, and
per-project fingerprint status. Symptom → action:

- Searches misbehave / empty results → `doctor`, then `index-status <path>`.
- `ConfigError: index ... was built with ...` → `needs-reindex`; owner runs
  `reindex-project`.
- `never-indexed` after a killed `add-project` → `reindex-project`.
- Watcher dead → `code-indexer unwatch --all`, then re-run `watch --background`.

## Security / privacy

Local-only by design (LAN Ollama/Qdrant). Sensitive files (`.env*`, keys,
credentials*, high-confidence secret content) are skipped by default and the
skip is counted in `index-status`; `--allow-sensitive` is a stored per-project
override. `.gitignore`/`.codeindexignore` are honored.

## Limitations

- References are heuristic (textual) unless backed by the AST refs/import
  tables; `find-references` output is always `confidence=heuristic`.
- C/C++/Java visibility is weak by design (everything public).
- The language matrix and per-language caveats live in
  [SKILL-CLI.md §semantic-search](SKILL-CLI.md#subcommand-reference) and
  [docs/languages.md](docs/languages.md).

## Performance

Embedding throughput depends on the Ollama host: measured **~1.2 docs/s with
GPU passthrough** (8B model, 4096-dim, batch 48) vs ~0.17 docs/s CPU-only.
The first index of a large repo is the slow part; incremental updates
re-embed only changed chunks. Qdrant upserts ~550 pts/s. Baseline numbers are
2026-09 measurements on the owner's LAN host; re-measure with
`code-indexer eval` (see [SKILL-CLI.md](SKILL-CLI.md)) before quoting new ones.

## MCP client config (Claude Desktop / Hermes / any stdio MCP client)

```json
{
  "mcpServers": {
    "code-indexer": {
      "command": "uv",
      "args": [
        "--directory", "/path/to/code-indexer",
        "run", "code-indexer-mcp"
      ],
      "env": {
        "OLLAMA_URL": "http://192.168.X.X:11434",
        "QDRANT_URL": "http://192.168.X.X:6333"
      }
    }
  }
}
```

When installed as a tool, point `command` at `code-indexer-mcp` directly (no
`uv` wrapper). **stdout is reserved for the MCP transport** — all log output
goes to stderr; never pipe server stdout into anything that expects log
lines. Tool listing does not require Ollama or Qdrant to be reachable.

## Development

```bash
uv sync                                  # install deps (.venv, incl. watchdog)
uv run code-indexer-mcp                  # run the stdio MCP server
uv run code-indexer list-projects        # one-shot CLI (no daemon)
uv run pytest -m "not live"              # offline suite (CI runs this)
uv run pytest -m live                    # live-backend tests (needs Ollama/Qdrant up)
```

Offline tests use fake embedder/store seams (no backends required); live
tests are marked `live` and deselected by default. Docs drift is guarded by
`tests/test_docs.py`. Constraints: pins `numpy<2` (1.26.4), `qdrant-client<1.15`,
`mcp<2`, `tree-sitter==0.26.0`, `tree-sitter-language-pack==1.18.0`
(older x86-64 CPUs without x86-64-v2; all pure/prebuilt wheels).

## Upgrading between versions

Manifests auto-migrate (see Schema history). `reindex-project` is only
required when `index-status` reports `needs-reindex` (fingerprint mismatch)
or to eagerly backfill schema-v3/v5 derived columns.