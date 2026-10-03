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
code-indexer unwatch /path/to/repo                  # stop the watcher (reverse of watch)
code-indexer unwatch --all [--timeout 10]           # stop it for all registered projects
```

Every subcommand accepts `--skip-stale-check` to skip the staleness probe /
incremental index pass on that invocation (startup-cost opt-out; there is no
daemon **on the default path**). Every MCP tool is a thin wrapper over the
matching CLI subcommand — same behavior, same formatting, same foreground
semantics — so this CLI section doubles as the MCP tool reference.

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

Regardless of the mode, pure-data payload chunks (`.json`/`.yaml`/`.toml`)
are down-weighted and capped to at most 40% of the top-k window, so they
cannot crowd out the code that produces the data for analysis-shaped
queries; they stay searchable and keep their place when the query names a
data format (`docs/semantic-search-ranking-diversification.md` §4).

`--symbol-type` and `--language` (e.g. `--symbol-type class`,
`--language python`) scope the search by exact payload filter — no extra
round trips on multi-language repos. The default call remains identical
to the pre-round behavior for every existing field; the only change to
the JSON contract is the addition of the `lang` key.

### Embedding-text format guard

Stored vectors are only comparable when they come from the same
embedding-text construction. The manifest records that construction in the
`embed_format` meta key (the value is defined next to `embed_text()` in
`src/code_indexer/embed_text.py`); an indexing pass whose recorded value
differs from the code's current one — or that finds none recorded — behaves
like a full rebuild: the chunk-hash cache is cleared and every chunk is
re-embedded, then the new value is recorded. Bump the constant in the same
change that alters `embed_text()`.

Indexes built before contextual headers hold bare-text vectors until a pass
re-embeds them (any indexing pass does; `reindex-project` forces it now).

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

## watch

`code-indexer watch [PATH...] | --all [--duration T] [--background]` runs a
long-lived **event-based** watcher: project roots are watched recursively
with Linux inotify (the `watchdog` Observer library — a main dependency;
only `code-indexer watch` imports it).
A file event schedules an incremental pass after a quiet period of
`WATCH_DEBOUNCE` seconds (default 3 — the old poll tick is now the
debounce; the env alias `WATCH_QUIET_PERIOD` is accepted and wins). A
burst of events coalesces into at most one pass per quiet period, and an
event burst that changes no content costs a hash scan only — zero
embedding, zero Qdrant traffic.

- **Self-heal sweep**: a full staleness pass for every watched project runs
  every `WATCH_SWEEP_INTERVAL` seconds (default **300 s**) even with zero
  events, healing anything inotify missed.
- **Degradation**: if inotify watch descriptors are exhausted (OSError
  scheduling the recursive watches), the watcher falls back to
  quiet-period polling (one hash scan per project per quiet tick) —
  correctness is never lost, only latency. (Watchdog itself is a main
  dependency, so the "not installed" fallback no longer occurs in
  practice.)
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
  stdio and normal output and takes the **same** pidfile, so "one watcher
  per index root" holds in both modes. A stopped watcher leaves no live
  lock or PID residue (the pidfile is unlinked only after the flock is
  released).
- **Stopping a watcher**: `code-indexer unwatch [PATH...|--all]
  [--timeout S]` is the reverse of `watch`. It SIGTERMs the live watcher
  holding the index root's pidfile — a `--background` daemon or a foreground
  watcher alike — waits for the flock to be released (default 10 s), and
  reports the projects that watcher was serving. Arguments mirror `watch`
  (paths or `--all`, never both). The flock, never pid existence, is the
  liveness test, so a stale pidfile left by a killed watcher is cleaned up
  silently. No watcher running is a clean no-op: one line and exit 0 (exit
  1 only when a live holder could not be stopped).
- Opt-in and never a prerequisite: without a watcher, one-shot commands
  behave exactly as before (STALE_TTL probe per invocation).

## Tools (MCP)

The MCP server (`code-indexer-mcp`) is a **thin wrapper over the CLI**: each
tool below builds the argv of the matching `code-indexer` subcommand and runs
it, relaying its output (stdout and stderr; a nonzero CLI exit surfaces as the
CLI's own `error: ...` text). There is no second implementation, so the tool
set can never drift from the CLI. The CLI is resolved from
`CODE_INDEXER_BIN`, else `code-indexer` on `PATH`, else
`python -m code_indexer.cli`; the subprocess inherits the server's environment
(`INDEX_ROOT`, `OLLAMA_URL`, `QDRANT_URL`, `EMBED_MODEL`, ...).

**Everything runs in the foreground**, exactly like the CLI: `add_project` and
`reindex_project` block until their index pass finishes (no background
threads, no hidden work).

| Tool | CLI subcommand | Description |
|---|---|---|
| `add_project(path, name?)` | `add-project` | Register a project directory (idempotent) and run the initial index pass **in the foreground**. Re-adding an already-registered path reports its status and does NOT re-index; only nonexistent paths error. Optional `name` picks the collection name (`idx_<name>`, sanitized to `[A-Za-z0-9_-]`, 1-64 chars, collision-checked) instead of the auto hash slug. |
| `lookup_project(path)` | `lookup-project` | Registration check with no side effects: prints the CLI's one-line status (`registered path=... slug=... state=... files=... chunks=... last_indexed=... collection=idx_<slug>`) or `not registered: <path>`. Normalizes paths (tilde, relative, trailing slash; symlink matched via real path). |
| `remove_project(path)` | `remove-project` | Deregister and **delete** the Qdrant collection, SQLite manifest, and registry entry. |
| `list_projects()` | `list-projects` | Registered projects with file/chunk counts, last-indexed time, and state. |
| `semantic_search(query, project?, limit=8, file_filter?, symbol_type?, language?, ranking?, format?)` | `semantic-search` | The hot path. Runs the staleness check first (only when the index is actually stale, quietly); `project=None` searches all registered projects. Returns file paths, line ranges, symbols, scores, snippets. `ranking`: `vector` (pure cosine, default) \| `metadata` (small definition boost / test-path penalty adjustments) \| `hybrid` (cosine fused with lexical token overlap — better for exact-identifier queries). `symbol_type`/`language` scope results by payload filter; `format='json'` selects the CLI's JSON contract. |
| `index_status(path)` | `index-status` | `idle \| indexing \| never-indexed \| error` + last-pass progress. `never-indexed` marks a registered project whose manifest records no completed pass (an interrupted or killed `add-project`) — the registry entry exists but there is no index. Like the CLI, this is **informational only** — it does not trigger a re-index. |
| `reindex_project(path)` | `reindex-project` | Force a full rebuild, **in the foreground** (blocks until finished). |
| `find_symbol(name, project?, symbol_type?)` | `find-symbol` | Look up symbols by name in the manifest symbol index (no semantic search). Exact AST-first, capped substring fallback. `symbol_type`: function\|method\|class\|struct\|enum\|namespace. |
| `find_symbols(project?, symbol_type?, file?, limit=25, format?)` | `find-symbol` (browse) | No name: filter the manifest symbol index by type/file, capped at `limit` (default 25). |
| `skeleton(project?, path_prefix?, limit?, format?)` | `skeleton` | Whole-project or per-subtree structural map from the manifest only: files with symbol lines and signatures. |
| `outline(file, project?, docstrings?, format?)` | `outline` | One file: declarations, signatures, optional one-line docstrings. |
| `find_definition(name, project?)` | `find-definition` | Where a symbol is declared (exact name match only, no substring). |
| `find_references(name, project?, relationship?, limit=25)` | `find-references` | Textual references TO a symbol (all confidence=heuristic). `relationship`: calls\|inherits\|includes\|references. |
| `get_code_context(file, project?, start_line?, end_line?, symbol?, context_lines?)` | `get-code-context` | Retrieve ONLY the relevant source lines — by line range (`start_line`+`end_line`) or via a symbol (`symbol`), padded by `context_lines`. |

MCP `project` parameters accept a path, slug, or registered custom name
(equivalent to the CLI's `--project X` / `--name X`). There is no MCP `watch`
or `unwatch` tool: a watcher makes no sense inside an MCP server that is
already
long-lived — `watch` is CLI-only.

## How indexing / staleness works

- **First index** (`add-project`, and the `add_project` tool) runs in the
  **foreground**: the command returns when indexing finishes (no background
  thread). `index-status` reports the current state at any time; add
  `--refresh` to force a staleness pass. If the initial pass does not
  complete (the invocation is interrupted or killed), the project stays
  registered but reports `state=never-indexed` with `files=0 chunks=0
  last_indexed=never` — it is never presented as a normal idle/indexed
  project. `reindex-project` builds the index; `add-project` on that path
  prints the same state plus a hint.
- **Staleness check** (on `semantic-search`): if the project hasn't been
  scanned within `STALE_TTL` seconds (default 60), the command re-scans file
  hashes and incrementally re-indexes only what changed; `index-status` is
  informational unless given `--refresh`. Answers are never served from a
  stale index by more than one scan interval.
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
├── watch.pid              # flock-guarded watcher PID file (both modes)
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
uv sync                                  # install deps (.venv, incl. watchdog)
uv run code-indexer-mcp                  # run the stdio MCP server
uv run code-indexer list-projects        # one-shot CLI (no daemon)
uv run pytest tests/ -q -W error         # full suite, warn-clean (CI runs this)
# (old dev/audit scripts under scripts/ were removed with the rename; the
# live e2e coverage now lives in tests/, scripts/live_smoke.py in vector-memory)
```

Python 3.11. Constraints: pins `numpy<2` (1.26.4), `qdrant-client<1.15`,
`mcp<2`, `tree-sitter==0.26.0`, `tree-sitter-language-pack==1.18.0`
(older x86-64 CPUs without x86-64-v2; all pure/prebuilt wheels).