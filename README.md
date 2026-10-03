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
code-indexer doctor [--json]
code-indexer remove-project /path/to/repo
code-indexer watch /path/to/repo [--duration 300] [--background]   # optional inotify watcher
code-indexer watch --all                            # watch all registered projects
code-indexer unwatch /path/to/repo                  # stop the watcher (reverse of watch)
code-indexer unwatch --all [--timeout 10]           # stop it for all registered projects
```

Every subcommand accepts `--skip-stale-check` to skip the staleness probe /
incremental index pass on that invocation, and `--fresh` to force the scan
**now** (ignore `STALE_TTL` for this call). The staleness scan uses a stat
fast-path: files whose `(size, mtime_ns, inode)` match the manifest are
treated as unchanged without re-hashing; set `PARANOID_HASH=1` to disable the
fast-path (for filesystems/tools that preserve mtime, e.g. `rsync -t`,
`cp -p`) and force full content hashes. There is no
daemon **on the default path**). Every MCP tool is a thin wrapper over the
matching CLI subcommand — same behavior, same formatting, same foreground
semantics — so this CLI section doubles as the MCP tool reference.

`find-symbol`, `find-definition`, `find-references`, and `get-code-context`
accept both `--project X` and `--name X` for the project argument (path,
slug, or registered custom name).

### Sensitive-content exclusion (privacy)

By default, indexing skips secret-bearing files at two layers, and the skip
is counted in `index-status` (`sensitive=N files skipped ...`) rather than
silent:

- **Filename globs** (built-in, applied in addition to
  `.gitignore`/`.codeindexignore`): `.env*`, `*.pem`, `*.key`, `id_rsa*`,
  `*.p12`, `*.kdbx`, `credentials*`, `secrets*.json|yaml|yml|toml`,
  `*.tfstate`.
- **Content scan**: files whose text carries a high-confidence secret
  pattern are skipped whole — AWS access keys (`AKIA…`), PEM private-key
  blocks, GitHub tokens (`gh[pousr]_…`), JWT-like triples, Slack tokens
  (`xox[baprs]-…`). Whole-file skipping is deliberate: chunking a
  secret-bearing file would still leak its surrounding context.

Override per project: `code-indexer add-project /path --allow-sensitive`
stores the choice in the project manifest and applies to all later passes
(including `reindex-project`). Revoking the override (by clearing
`allow_sensitive` in the manifest) makes the next pass purge previously
indexed secret files. Never enable it on a project whose secrets you are not
willing to have embedded in the local index.

### doctor (health check)

`code-indexer doctor [--json]` verifies the installation end to end and
prints one line per check — `ok|warn|fail <name>: <detail>` — exiting 1 when
any check FAILS (warnings do not fail). Checks: Python version; uv on PATH;
Ollama reachable (`GET /api/tags`), configured model present, embedding
dimension probe; Qdrant reachable, server version, qdrant-client vs server
compatibility (warn-level: major versions should match and the minor delta
must not exceed 1), collection list vs registry (missing projects FAIL,
`idx_*` collections with no registry entry WARN as orphans — e.g. leftover
rebuild temps), collection dims vs the live dimension probe; INDEX_ROOT
writable and free disk (warn < 1 GiB); SQLite `PRAGMA integrity_check` on
the registry and every manifest; watch pidfile liveness (stale pidfile
warns, suggests `unwatch`) and lock-file holding (held locks are OK — an
index pass is simply running); per-project index fingerprint status
(`needs-reindex` details from the fingerprint guard). `--json` emits
`{"checks": [{status, name, detail}...], "healthy": bool}`. Dev/ops surface:
deliberately CLI-only, no MCP tool — agents use `index-status` for
per-project state. Network calls go only to the configured Ollama/Qdrant
URLs.

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

#### Index fingerprint (schema v4)

Every manifest records the full embedding/chunking configuration that built
it: `embed_model`, `embed_dim` (probed from the configured model),
`embed_text_version` (the `embed_format` construction string), and
`chunker_version` (`CHUNKER_VERSION` in `src/code_indexer/ts_chunker.py`).
Opening a project compares the recorded fingerprint against the current
configuration:

- **Match** — queries and passes run normally.
- **Mismatch** (e.g. `EMBED_MODEL` changed, even to another 4096-dim model) —
  the project reports `state=needs-reindex` in `index-status`/`list-projects`
  with a `reason=` detail, and `semantic-search` refuses with
  `ConfigError: index for <path> was built with ...; run: code-indexer
  reindex-project <path>` instead of silently querying incompatible vectors.
  `--skip-stale-check` does **not** bypass this gate.
- **Legacy manifest** (no fingerprint recorded — anything pre-v4) — treated
  as unknown, not mismatched: the next indexing pass backfills the current
  config with a one-line log notice; no forced reindex.

`reindex-project` clears the state by rebuilding into a temporary collection
(`idx_<slug>__new`) and swapping it in via a collection alias on completion —
the old index stays searchable until the swap. A crash mid-rebuild leaves the
`__new` collection behind; the next attempt recreates it.

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

### Retrieval evaluation harness (`eval` / `eval-compare`)

`code-indexer eval --project P --queries eval/queries.jsonl [--k 1,3,5,10]
[--mode dense|hybrid] [--json] [--out runs/<name>.json]` runs a labeled
query set against a project's index and reports recall@k, MRR, nDCG@10,
mean/p95 latency, and approximate output size (chars/4 ≈ tokens). A hit is
correct when the file matches and (no symbol named, or the symbol matches,
or the line range overlaps the labeled symbol). `eval-compare runs/a.json
runs/b.json` prints a metric delta table plus a per-query win/loss list —
the workflow is change → eval → compare → paste the table in the PR.

This is the retrieval-quality gate: no change to chunking, embedding text,
fusion, or ranking merges without a before/after eval table.

Notes:

- **CLI-only by design** — a dev tool for the maintainer's quality gate, not
  an agent surface; it is deliberately not exposed as an MCP tool.
- The `hybrid` mode value is accepted for forward compatibility but currently
  aliases the dense pipeline; the lexical (FTS5) index is not built yet.
- `eval/queries.jsonl` format: one JSON object per line
  `{"id", "query", "relevant": [{"file", "symbol"?}], "tags"}`. The committed
  seed set (42 queries: conceptual / exact-identifier / filename /
  how-does-x-work) targets this repository itself; run it with
  `uv run code-indexer eval --project . --queries eval/queries.jsonl`.
- Deterministic for a fixed index; latency is reported per query but excluded
  from ranking metrics. Tests run fully offline against fake embedder/store
  seams (no Ollama/Qdrant required).

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
| `index_status(path)` | `index-status` | `idle \| indexing \| never-indexed \| error \| needs-reindex` + last-pass progress. `never-indexed` marks a registered project whose manifest records no completed pass (an interrupted or killed `add-project`) — the registry entry exists but there is no index. Like the CLI, this is **informational only** — it does not trigger a re-index. `needs-reindex` marks an index built with a different embedding model, dimension, text format, or chunker version than the current configuration (with a `reason=` detail); queries against it fail with a `ConfigError` until `reindex_project` rebuilds. |
| `reindex_project(path)` | `reindex-project` | Force a full rebuild, **in the foreground** (blocks until finished). The rebuild fills a temporary collection first and swaps it in on completion, so the old index stays searchable until the swap; clears a `needs-reindex` state. |
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
  stale index by more than one scan interval. The scan uses a stat fast-path
  (`size, mtime_ns, inode` match ⇒ no re-hash; `PARANOID_HASH=1` disables it).
- **Stale-safe line ranges** (`get-code-context`): search hits carry
  `file_hash` (short 8-hex) and `indexed_at`. Before serving a line range,
  the current file hash is compared with the manifest. If the file changed
  since indexing: in `--symbol` mode the symbol is re-resolved against the
  live file with tree-sitter and fresh lines are returned (JSON field
  `re_resolved: true`); in line-range mode the requested lines are returned
  as-is with one `Warning: file changed since last index; line numbers may be
  shifted` line on stderr (exit code stays 0) and `stale: true` in JSON
  output. This warning line is the documented exception to "stderr empty on
  success".
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
| `OLLAMA_TIMEOUT` | `120` | Ollama connect/read timeout (s); retries use exponential backoff + jitter, max 5 |
| `EMBED_CONCURRENCY` | `1` | >1 pipelines tree-sitter parsing/hashing in a thread pool while the embed call is in flight |

CLI flags: `--ollama-url`, `--qdrant-url`, `--embed-model`, `--index-root`.

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

Two things to know about the server process:

- **stdout is reserved for the MCP transport.** All log output goes to stderr;
  never pipe server stdout into anything that expects log lines.
- The server exposes **14 tools** (the table below). Tool listing does not
  require Ollama or Qdrant to be reachable — a stdio `initialize` +
  `tools/list` handshake succeeds even with both backends down. If installing
  from a checkout without a `uv` context, point `command` at the installed
  `code-indexer-mcp` executable directly.

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