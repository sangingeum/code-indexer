# code-indexer

[![M8ven Verified](https://m8ven.ai/badge/mcp/sangingeum-code-indexer-1t93x3?variant=verified)](https://m8ven.ai/mcp/sangingeum/code-indexer)

Semantic code index over Ollama + Qdrant, with a one-shot CLI
(`code-indexer`, typer) and an MCP stdio server (`code-indexer-mcp`) that is a
**thin wrapper over that same CLI**: every MCP tool builds the argv of the
matching `code-indexer` subcommand and runs it, relaying its output — one code
path, so the two surfaces can never diverge. The index maps one or more local
project directories in Qdrant, using Ollama (`qwen3-embedding:8b`, 4096-dim)
for embeddings. Fully LAN-local; no cloud.

**Agents never index anything themselves.** A read command never refreshes the
index: `semantic-search` reports whether the index is `fresh`, `stale` or
`unknown`, and repairs it only when the call asks for `--refresh` (or when a
pass command runs). Chunks, hashes, and collections are never exposed.

**Full command reference:** [SKILL-CLI.md](SKILL-CLI.md) — the agent-facing
document with the decision tree, per-command options, and index-state
meanings. This README is the overview; each fact lives in exactly one place.

## Installation (CLI)

From the repo directory, install the CLI as an editable `uv` tool (on PATH
in `~/.local/bin`, edits to the checkout take effect immediately):

```bash
uv tool install -e .                       # core: no service, no MCP server
uv tool install -e ".[semantic]"           # + Ollama and Qdrant clients
uv tool install -e ".[mcp]"                # + the code-indexer-mcp server
```

Core installs and runs with no service of any kind. The semantic extension
(`OLLAMA_URL`, `QDRANT_URL`) is needed for indexing and semantic search; the
MCP server ships only with the `mcp` extra and is imported by that entry
point alone. Verify with `code-indexer list-projects`.

## Quick start

```bash
code-indexer add-project /path/to/repo        # register + initial index (foreground)
code-indexer semantic-search "auth token refresh" --project /path/to/repo --format compact
code-indexer overview --project /path/to/repo # project map for orientation
code-indexer index-status --project /path/to/repo  # indexing state
code-indexer watch /path/to/repo --background # keep the index fresh (opt-in)
```

`index-status` and the lifecycle commands (`reindex-project`,
`remove-project`, `lookup-project`, `index-more`) accept the project as
`--project` / `--name` or as a positional path. For `reindex-project`,
`remove-project` and `lookup-project` the flag wins when both are given;
`index-more` accepts `<project> <subpath>` or `--project <project> <subpath>`
and rejects the flag plus two positionals as ambiguous. The mutating commands
never guess a project from the cwd.

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
- **MCP server** (`code-indexer-mcp`, `code-indexer[mcp]` only): a
  narrowed, read-only view over the same CLI subprocess; all foreground, no
  store or manifest access of its own.

### CLI ↔ MCP parity

The MCP surface carries the read-only query shapes and nothing else. Every
tool is one CLI subcommand; `code-indexer-mcp --help` prints this list from
the server's own registry, so it cannot go stale.

| CLI subcommand | MCP tool |
|---|---|
| `list-projects` | `list_projects` |
| `overview` | `overview` |
| `skeleton` | `skeleton` |
| `find-symbol` | `find_symbol` |
| `find-references` | `find_references` |
| `find-callers` | `find_callers` |
| `get-code-context` | `get_code_context` |
| `semantic-search` (only with the semantic extra) | `semantic_search` |

Removed from the surface — **use the CLI subcommand of the same name**:
`add-project`, `index-status`, `lookup-project`, `reindex-project`,
`remove-project`, `changed-symbols`, `deps`, `find-callees`, `find-symbols`,
`find-definition` and `outline`. Writes are exactly what an MCP client should
not trigger by a call that carries no preview, and the removed reads
duplicate a kept tool's shape or the CLI.

**CLI-only** (no MCP tool): `index-more`, `doctor`, `watch`, `unwatch`,
`sweep-orphans`, `eval`, `eval-compare`.

Every tool is a read: `readOnlyHint=True` with `idempotentHint=True` is a
claim about what a client sees, and it holds because reads never repair the
index and the relay reports a failure as a failure. A successful call
returns the CLI's stdout as its content; a failing call returns the CLI's
stderr with `isError: true` and the numeric exit code plus its `ErrorType` in
the structured result. Nothing is synthesized over a nonzero exit.

MCP options are a subset of CLI flags; the tool schemas (see
`src/code_indexer/server.py`) are the source of truth for what each tool
accepts. There is no MCP `watch`/`unwatch`: a watcher makes no sense inside
an already long-lived server. The server resolves the CLI from
`CODE_INDEXER_BIN`, else `code-indexer` on `PATH`, else
`python -m code_indexer.cli`; `CODE_INDEXER_BIN` is the only env var the
server process reads itself — all other configuration flows through the CLI
subprocess's inherited environment.

**Usage record.** Every tool call appends one line to
`$INDEX_ROOT/mcp-usage.jsonl` (`~/.code-indexer/mcp-usage.jsonl` by
default): tool name, outcome, exit code, response bytes, duration and a
timestamp. Nothing else, and never the arguments — a query string may be
source code. The line is written by the MCP process, never by a CLI read
command, and a failed write does not fail the call.

## Index states

Canonical states, the same everywhere (CLI and MCP):

| State | Meaning | Agent action |
|---|---|---|
| `idle` | indexed, no pass running | normal use |
| `indexing` | a pass is running | wait/retry |
| `never-indexed` | registered but no completed pass (interrupted `add-project`) | do not trust empty results; tell the owner |
| `needs-reindex` | index built with a different embed model/dimension/text format/chunker/extraction version | do not run `reindex-project` yourself; report to the owner (queries fail with `ConfigError` until then) |
| `error` | last pass failed | run `code-indexer doctor` |

`index-status` reports the **pass state** as `state=`, and — when the index
was built by a different configuration — adds `fingerprint=needs-reindex
reason=...`, the same verdict `list-projects`/`doctor` give. A project that
owes a rebuild therefore never reads as plainly `idle`, and the two surfaces
never contradict each other. The manifest-only surfaces (`find-references`,
`find-callers`, `find-callees`, `deps`, `skeleton`, `outline`) still answer on
such an index, with one stderr warning when the stored rows may predate the
current extraction code; only `search` refuses. Reaching that verdict needs a
reachable embedder (a dimension probe), so when the embedder cannot be probed
`index-status` prints `fingerprint=unverified reason=embedder-unreachable`
instead of omitting the field — "cannot verify" is its own value, and a
missing `fingerprint=` field means the index matched.

## How indexing / staleness works

- **First index** (`add-project`, CLI and MCP alike) runs in the
  **foreground**: the call returns when indexing finishes. If it is
  interrupted, the project stays registered but reports
  `state=never-indexed` (`files=0 chunks=0 last_indexed=never`);
  `reindex-project` builds it.
- **Staleness is reported, never repaired by a read.** A read answers from
  the index as of `meta.last_indexed`, compares the indexed files with the
  tree (stat identity — `size`, `mtime_ns`, `inode`; content is hashed only
  for files whose stat changed), and reports `fresh`, `stale` (with reasons,
  capped at five) or `unknown` (path absent, no completed pass, or the scan
  exceeded its 5 s / 200,000-entry budget). JSON answers carry `stale`,
  `stale_known`, `stale_reasons`, `stale_reason_count`, `index_revision` and
  `last_indexed`; text mode prints exactly one stderr line:
  `Warning: index is stale (3 files changed since 2026-11-06T22:10:00Z: 2
  modified, 1 deleted); answers describe the index, not the tree. Refresh:
  code-indexer reindex-project <path>`. A fresh answer prints nothing extra;
  an unknown verdict prints one line saying the check did not run.
- **Repair is explicit.** `--refresh` is the only flag that turns a read
  into a write: it takes the per-project flock, runs one incremental pass and
  answers from the result. A held lock is `LockedError: indexing in
  progress` with exit 5 and no answer. `--skip-stale-check` is accepted by
  every command and is inert (the default is to report without repairing);
  `--fresh` still means "read the live file" on `get-code-context` and is
  inert elsewhere. The watcher and the three pass commands remain the other
  writers.
- **Exit codes**: `0` success (including zero hits — "no results" is an
  answer), `1` other runtime failure, `2` usage/argument error, `3` backend
  or extension unavailable, `4` the index requires a rebuild, `5` lock or
  concurrency conflict. A failed query prints `ErrorType: message` on stderr
  with stdout empty; an empty answer prints `no results` on stdout and exits
  0, so the two are never confused.
- **Query cost is bounded**: a search resolves ONE project by default (the
  containing project inferred from the invocation path), fans out only with
  `--all-projects`, makes one embedding attempt with a short timeout, and
  stops a fan-out at a 30 s wall (`skipped` with `reason=budget`,
  `partial: true`).
- **Incremental diff**: files are classified by **content hash** (sha256, not
  mtime — branch switches re-index correctly). Only chunks whose hash changed
  are re-embedded; point IDs are deterministic uuid5, so upserts are
  idempotent; deleted files are purged.
- **Chunking**: ~1000-char cap (`CHUNK_MAX_CHARS`); tree-sitter AST units,
  regex-window fallback. The embedded text carries a contextual header
  (`embed_text.py`, format `contextual-header-v1`).
- **Fingerprint**: the manifest records `embed_model`, `embed_dim`,
  `embed_text_version`, `chunker_version`, `extraction_version`. Any mismatch
  ⇒ `needs-reindex`
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
| v6 | ONE reference store: `symbol_refs` folded into `refs` (+ a `receiver` column); `csharp` joins the AST-references languages (`EXTRACTION_VERSION` 2) | automatic: `symbol_refs` dropped, `receiver` ALTERed; the extraction-version bump marks every index `needs-reindex` until `reindex-project` |
| — | FTS5 lexical table (`chunks_fts`, hybrid retrieval) | automatic backfill on next pass |

Current schema version: **6** (auto-migrates on open; `reindex-project`
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

- Searches misbehave / empty results → `doctor`, then `index-status --project <path>` (the positional `<path>` form still works).
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

- References are tree-sitter backed (`confidence=ast`) for the languages with
  a query (`python`/`typescript`/`tsx`/`javascript`/`c`/`cpp`/`go`/`csharp`)
  and textual (`confidence=heuristic`) for the rest; `find-references` labels
  every row with the confidence it carries.
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

The server ships in the `mcp` extra. One command runs the released server
without installing it:

```bash
uvx --from "code-indexer[mcp]" code-indexer-mcp
```

Configure the client to launch it:

```json
{
  "mcpServers": {
    "code-indexer": {
      "command": "uvx",
      "args": ["--from", "code-indexer[mcp]", "code-indexer-mcp"],
      "env": {
        "OLLAMA_URL": "http://192.168.X.X:11434",
        "QDRANT_URL": "http://192.168.X.X:6333"
      }
    }
  }
}
```

When installed as a tool (`uv tool install -e ".[mcp]"`), point `command` at
`code-indexer-mcp` directly (no `uvx` wrapper). **stdout is reserved for the
MCP transport** — all log output goes to stderr; never pipe server stdout
into anything that expects log lines. Listing tools does not require Ollama or
Qdrant to be reachable, and with the semantic extra absent the server starts
with the seven tools that need no embedding client.

## Development

```bash
uv sync --all-extras                     # dev env (.venv, incl. both extras)
uv run code-indexer-mcp                  # run the stdio MCP server
uv run code-indexer list-projects        # one-shot CLI (no daemon)
uv run pytest -m "not live"              # offline suite (CI runs this)
uv run pytest -m live                    # live-backend tests (needs Ollama/Qdrant up)
```

Offline tests use fake embedder/store seams (no backends required); live
tests are marked `live` and deselected by default. Docs drift is guarded by
`tests/test_docs.py`. Constraints: core pins `tree-sitter==0.26.0` and
`tree-sitter-language-pack==1.18.0` (older x86-64 CPUs without x86-64-v2;
pure/prebuilt wheels). The `semantic` extra pins `qdrant-client<1.15`, whose
resolution brings `numpy<2` (1.26.4); the `mcp` extra pins `mcp<2`.

## Upgrading between versions

Manifests auto-migrate (see Schema history). `reindex-project` is only
required when `index-status` reports `needs-reindex` (fingerprint mismatch)
or to eagerly backfill schema-v3/v5/v6 derived columns.