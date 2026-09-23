# code-indexer

Semantic code index over Ollama + Qdrant, with **two entry points over the
same core** (`code_indexer.core`): an MCP stdio server (`code-indexer-mcp`)
for agent use, and a one-shot CLI (`code-indexer`) for shell/scripts. The MCP
server keeps an automatic semantic index of one or more local project
directories in Qdrant, using Ollama (`qwen3-embedding:8b`, 4096-dim) for
embeddings. Fully LAN-local; no cloud.

**Agents never index anything themselves.** They only call `semantic_search`
(which transparently runs a staleness check + incremental indexing first) plus
a few admin tools. Chunks, hashes, and collections are never exposed.

## Installation (CLI)

From the repo directory, install both executables as editable `uv` tools (on
PATH in `~/.local/bin`, edits to the checkout take effect immediately):

```bash
uv tool install -e .
```

This installs `code-indexer` (and the optional `code-indexer-mcp` server
executable). Verify with `code-indexer list-projects`.

## CLI

```bash
code-indexer add-project /path/to/repo [--name myproject]
code-indexer lookup-project /path/to/repo
code-indexer list-projects
code-indexer semantic-search "auth token refresh" --project /path/to/repo --limit 8 [--file-filter '*.py'] [--symbol-type class] [--language python] [--ranking vector|metadata|hybrid] [--json]
code-indexer skeleton [--project P | --name P] [PATH_PREFIX] [--tree] [--no-signatures] [--limit N] [--json]
code-indexer map ...      # alias for skeleton
code-indexer outline [--project P | --name P] FILE [--docstrings] [--json]
code-indexer file-outline ...   # alias for outline
code-indexer find-symbol FileTransferSession [--symbol-type class]
code-indexer find-definition main
code-indexer find-references QTimer --relationship calls
code-indexer get-code-context src/session.hpp --start-line 40 --end-line 80
code-indexer index-status /path/to/repo
code-indexer reindex-project /path/to/repo
code-indexer remove-project /path/to/repo
code-indexer watch /path/to/repo [--duration 300] [--background]   # optional inotify watcher
code-indexer watch --all                            # watch all registered projects
```

Every subcommand accepts `--skip-stale-check` to skip the staleness probe /
incremental index pass on that invocation (startup-cost opt-out; there is no
daemon **on the default path**). CLI subcommands and MCP tools map 1:1 to core
operations.

`find-symbol`, `find-definition`, `find-references`, and `get-code-context`
accept both `--project X` and `--name X` for the project argument (path,
slug, or registered custom name).

### Ranking diversification (semantic-search)

`semantic-search` defaults to pure cosine ranking (`--ranking vector`).
Two opt-in modes re-rank a wider candidate pool at query time
(`docs/semantic-search-ranking-diversification.md` for the design and
measured results):

- `--ranking metadata` — small additive adjustments from index-time payload
  facts: a definition boost for chunks carrying a symbol and a mild penalty
  for test/fixture/vendor paths (waived when the query targets tests).
  Measured: improved top-1 5/8 -> 6/8 and top-5 6/8 -> 7/8 on the ranking
  evaluation intents with no added latency.
- `--ranking hybrid` — fuses the cosine score with a lightweight lexical
  token-overlap score (`fused = cosine + 0.25 * lexical` over symbol, path,
  and snippet). Intended for queries that contain exact identifiers; it is
  noisier on pure natural-language intents, so it stays opt-in.

`--symbol-type` and `--language` (e.g. `--symbol-type class`,
`--language python`) scope the search by exact payload filter — no extra
round trips on multi-language repos. The default call remains identical
to the pre-round behavior for every existing field; the only change to
the JSON contract is the addition of the `lang` key.

#### Supported languages (exhaustive)

The `lang` payload value is derived from the file extension at index time
(`--language` matches it exactly). The exhaustive list of `--language`
values the filter accepts:

**Parsed + chunked by tree-sitter AST** (the tree-sitter-language-pack
grammar resolves; structured chunks with symbol names):

| `--language` value | File extensions | Notes |
|---|---|---|
| `python` | `.py` | |
| `javascript` | `.js`, `.jsx` | |
| `typescript` | `.ts`, `.tsx` | |
| `rust` | `.rs` | |
| `go` | `.go` | |
| `java` | `.java` | default-internal visibility (see below) |
| `c` | `.c`, `.h` | weak visibility by design (everything public) |
| `cpp` | `.cpp`, `.cc`, `.hpp` | default-internal visibility (see below) |
| `csharp` | `.cs` | |
| `ruby` | `.rb` | |
| `php` | `.php` | |
| `bash` | `.sh` | parsed via the pack's `bash` grammar; the stored `lang` payload value for `.sh` files is `shell` (the indexer's manifest map) — filter with `--language shell` |
| `lua` | `.lua` | |
| `swift` | `.swift` | |
| `kotlin` | `.kt` | |
| `markdown` | `.md` | |
| `json` | `.json` | |
| `yaml` | `.yaml`, `.yml` | |
| `toml` | `.toml` | |
| `html` | `.html` | |
| `css` | `.css` | |

**Fallback-only values** (no tree-sitter grammar resolves for the
extension, or the extension is absent from the AST map; chunks come from
the regex-window fallback chunker — searchable, but without AST symbol
extraction): any extension not in the AST map above is stored verbatim as
the extension string (observed in real indexes: `gitignore` for
`.gitignore`, `lock` for `.lock`, `python-version` for `.python-version`,
`csx` for `.csx`, `text` for `.txt` and extensionless files). These are
still valid `--language` filter values — they just carry no AST structure.

**Visibility caveats**: C/C++/Java default to weak visibility (everything
public; access sections are not tracked) — do not oversell it. Python,
C#, and the other grammars carry real visibility.

### Token-reduction subcommands (schema v3)

`skeleton` (alias `map`) prints a whole-project or per-subtree structural
map from the manifest only — one file per group, one symbol per line with
lines and the schema-v3 signature (`--no-signatures` for the densest
output; `--limit N` caps symbols per file). `outline` (alias
`file-outline`) prints one file's declarations, signatures, and (with
`--docstrings`) one docstring line per declaration — the only on-demand
source read in the toolset. `find-symbol` gained browse mode: omit NAME and
filter with `--type`/`--file`, capped at `--limit` (default 25); output
includes the signature when stored. All are manifest-only at query time —
zero re-parsing. Signatures/visibility are extracted at index time (schema
v3); pre-v3 manifests are migrated automatically (old rows keep NULL
signature/visibility and `reindex-project` fills them).

## watch (optional, opt-in)

`code-indexer watch [PATH...] | --all [--duration T] [--background]` runs a
long-lived **event-based** watcher: project roots are watched recursively
with Linux inotify (the `watchdog` Observer library, opt-in `watch`
dependency-group — the MCP server and one-shot commands never import it).
A file event schedules an incremental pass after a quiet period of
`WATCH_DEBOUNCE` seconds (default 3 — the old poll tick is now the
debounce; the env alias `WATCH_QUIET_PERIOD` is accepted and wins). A
burst of events coalesces into at most one pass per quiet period, and an
event burst that changes no content costs a hash scan only — zero
embedding, zero Qdrant traffic.

- **Self-heal sweep**: a full staleness pass for every watched project runs
  every `WATCH_SWEEP_INTERVAL` seconds (default **300 s**) even with zero
  events, healing anything inotify missed.
- **Degradation**: without watchdog installed, or if inotify watch
  descriptors are exhausted (OSError scheduling the recursive watches), the
  watcher falls back to quiet-period polling (one hash scan per project per
  quiet tick) — correctness is never lost, only latency.
- **Self-write suppression**: events under the index root, `.git` paths,
  and editor temp files (`.swp`, `~`, `.tmp`, ...) are filtered; the
  watcher's own manifest/registry writes never trigger a pass.
- **Moved/deleted dirs**: directory delete/move events dirty the project
  (a wholesale file-set change), so deletions are purged on the next pass.
- `--duration T` bounds the watcher's life (exit 0 after T seconds; `0` or
  omitted = forever; applies to background mode too). SIGINT/SIGTERM exit
  0; the kernel flock is released automatically.
- Multiple projects are served round-robin: repeatable path/slug/name args,
  or `--all` for every registered project (registry order). Never combine
  paths with `--all`.
- **`--background`** daemonizes (double-fork + setsid), writes the daemon
  PID to `<INDEX_ROOT>/watch.pid` guarded by an `flock` on that file (a
  second watcher is refused while a live one holds it — the flock, not the
  pid, is the liveness test), and redirects stdout/stderr to
  `<INDEX_ROOT>/watch.log`. `--foreground` (default) keeps the inherited
  stdio and normal output and does not take the pidfile. A stopped watcher
  leaves no live lock or PID residue (the pidfile is unlinked only after
  the flock is released).
- Opt-in and never a prerequisite: without a watcher, one-shot commands
  behave exactly as before (STALE_TTL probe per invocation).

## Tools (MCP)

| Tool | Description |
|---|---|
| `add_project(path, name)` | Register a project directory; creates the Qdrant collection and starts a full initial index in the background. **Idempotent**: re-adding an already-registered path returns the current index status summary (state, files, chunks, last_indexed) and does NOT spawn a re-index — only nonexistent paths error. Optional `name` picks the collection name yourself (`idx_<name>`, sanitized to `[A-Za-z0-9_-]`, 1-64 chars, collision-checked) instead of the auto hash slug. |
| `lookup_project(path)` | Check registration without side effects: returns one line with path, slug, custom name, Qdrant collection name (`idx_<slug>`), state, files, chunks, last_indexed — or `not registered: <path>`. Normalizes paths (tilde, relative, trailing slash; symlink matched via real path). Use this instead of guessing via `add_project` idempotency. |
| `remove_project(path)` | Deregister and **delete** the Qdrant collection, SQLite manifest, and registry entry. |
| `list_projects()` | Registered projects with file/chunk counts, last-indexed time, and state. |
| `semantic_search(query, project?, limit=8, file_filter?, symbol_type?, language?, ranking?, format?)` | The hot path. Always runs a staleness check first; `project=None` searches all registered projects. `project` accepts a path, a slug, or a registered custom name. Returns file paths, line ranges, symbols, scores, snippets. `ranking`: `vector` (pure cosine, default) \| `metadata` (small definition boost / test-path penalty adjustments) \| `hybrid` (cosine fused with lexical token overlap — better for exact-identifier queries). `symbol_type`/`language` scope results by payload filter. |
| `index_status(path)` | `idle \| indexing \| error` + last-pass progress. |
| `reindex_project(path)` | Force a full rebuild. |
| `find_symbol(name, project?, symbol_type?)` | Look up symbols by name in the manifest symbol index (no semantic search). Exact AST-first, capped substring fallback. `symbol_type`: function\|method\|class\|struct\|enum\|namespace. |
| `find_symbols(project?, symbol_type?, file?, limit=25, format?)` | Browse mode (no name): filter the manifest symbol index by type/file, capped. Replaces the pruned list-symbols proposal. |
| `skeleton(project?, path_prefix?, limit?, format?)` | Whole-project or per-subtree structural map from the manifest only (design §2.1): files with symbol lines and signatures. |
| `outline(file, project?, docstrings?, format?)` | One file: declarations, signatures, optional one-line docstrings (design §2.2). |
| `find_definition(name, project?)` | Where a symbol is declared (exact name match only, no substring). |
| `find_references(name, project?, relationship?, limit=25)` | Textual references TO a symbol (all confidence=heuristic). `relationship`: calls\|inherits\|includes\|references. |
| `get_code_context(file, project?, start_line?, end_line?, symbol?, context_lines?)` | Retrieve ONLY the relevant source lines — by line range (`start_line`+`end_line`) or via a symbol (`symbol`), padded by `context_lines`. |

MCP `project` parameters accept a path, slug, or registered custom name
(equivalent to the CLI's `--project X` / `--name X`). There is no MCP `watch`
tool: a watcher makes no sense inside an MCP server that is already
long-lived — `watch` is CLI-only.

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
- **Concurrency**: multiple processes (multiple agents, CLI + server) are
  safe — per-project `flock` lock files (`fcntl.flock(LOCK_EX|LOCK_NB)`;
  BlockingIOError reports "indexing in progress"; the kernel releases the
  lock automatically when a process dies, so there is no stale-lock stealing
  and lock files are permanent), WAL-mode SQLite with `busy_timeout`,
  idempotent point IDs. Two processes indexing the same project at once is
  wasteful, not corrupting: the flock serializes them to exactly one pass.

## Upgrading from mcp-code-indexer

Upgrading from the old `mcp-code-indexer` repo/server: run `code-indexer add`
for each project; existing `idx_*` collections are reused, no reindex
required. The default state directory moved from `~/.mcp-code-indexer` to
`~/.code-indexer` — the new registry starts empty (an undocumented reset, not
a migration), so re-registering with the **same custom names** (`--name`) that
the old projects used reattaches to the already-existing Qdrant collections.
Re-run `reindex-project` once per old project to populate its symbol index
(pre-schema-v2 manifests have no symbol rows).

## State layout

```
$INDEX_ROOT/               (default ~/.code-indexer)
├── registry.db            # path -> slug mapping (SQLite, WAL)
├── <slug>.lock            # per-project lock
├── <slug>/manifest.db     # per-project file manifest (SQLite, WAL)
├── watch.pid              # flock-guarded watcher PID file (--background only)
└── watch.log              # watcher daemon stdout/stderr (--background only)
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
| `INDEX_ROOT` | `~/.code-indexer` | State directory |
| `STALE_TTL` | `60` | Seconds between staleness re-scans |
| `WATCH_DEBOUNCE` | `3` | `watch` quiet period (s) between an event burst and its pass |
| `WATCH_QUIET_PERIOD` | — | alias for `WATCH_DEBOUNCE` (wins when both set) |
| `WATCH_SWEEP_INTERVAL` | `300` | `watch` periodic full staleness sweep (s) |
| `EMBED_BATCH` | `48` | Texts per Ollama embed request |
| `UPSERT_BATCH` | `256` | Points per Qdrant upsert |
| `MAX_FILE_BYTES` | `1048576` | Skip files larger than this |

CLI flags: `--ollama-url`, `--qdrant-url`, `--embed-model`, `--index-root`.

## MCP client config (Claude Desktop / Hermes / any stdio MCP client)

```json
{
  "mcpServers": {
    "code-indexer": {
      "command": "uv",
      "args": [
        "--directory", "/path/to/code-indexer",
        "run", "code-indexer"
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
uv run code-indexer-mcp                  # run the stdio MCP server
uv run code-indexer list-projects        # one-shot CLI (no daemon)
uv run pytest                            # unit + concurrency tests
# (old dev/audit scripts under scripts/ were removed with the rename; the
# live e2e coverage now lives in tests/, scripts/live_smoke.py in vector-memory)
```

Python 3.11. Constraints: pins `numpy<2` (1.26.4), `qdrant-client<1.15`,
`mcp<2`, `tree-sitter==0.26.0`, `tree-sitter-language-pack==1.18.0`
(older x86-64 CPUs without x86-64-v2; all pure/prebuilt wheels).