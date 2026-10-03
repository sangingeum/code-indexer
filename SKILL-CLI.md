---
name: code-indexer
description: "Use for shell code search: code-indexer CLI, one-shot."
version: 1.0.0
---

# code-indexer (CLI)

One-shot typer CLI over `code_indexer.core`. Binary: `code-indexer`; package
`code_indexer`. CLI-only, LAN-local, no daemon on the query path.

## When to use

- All semantic code navigation against registered repos: search, symbols,
  refs, line-ranged source context — from shell, scripts, or in-conversation.

## Decision tree (agent workflow)

```
Need to understand a repo?
├─ First contact / orientation
│   └─ code-indexer overview --project P        # languages, dirs, entry
│                                                # points, hotspots (capped)
│   └─ code-indexer skeleton --project P [PATH] # symbol map of the repo
│                                                # or one subtree
├─ Know the area, want structure of one file
│   └─ code-indexer outline FILE --project P    # declarations + signatures
│   └─ code-indexer find-symbol NAME            # locate a known symbol
├─ Need actual source
│   └─ code-indexer get-code-context FILE --start-line N --end-line M
│       (or --symbol Foo.bar)                   # ONLY the relevant lines —
│                                                # never read whole files
├─ Conceptual question, names UNKNOWN
│   └─ code-indexer semantic-search "how does X work?" \
│        --project P --format compact --limit 8
│   (default mode hybrid; rerank/heuristic is opt-in)
└─ After edits
    └─ code-indexer changed-symbols --project P [--impact]
        # read what changed before searching anything
```

**When NOT to use code-indexer:**

- Exact string/regex search → use `rg`/`grep`. Semantic search is for
  meaning ("where do we validate tokens?"), not for literal text.
- Listing files → `fd`/`ls`. The index is not a filesystem replacement.
- git history/blame → `git log`/`git blame`. changed-symbols reads the
  working diff, not history.
- Huge mechanical refactors → do them with edit tools; re-index afterwards
  (the watcher does it automatically).

**Token-saving defaults:** prefer `--format compact` for search output (one
line per hit), keep `--limit 8`, and cap long result sets with
`--max-tokens 400`. Read source only via `get-code-context` with explicit
line ranges or `--symbol`. Take `overview --max-lines 40` before any deep
dive on an unfamiliar repo.

**State meanings:** `stale: true` on a hit means the file changed after the
index pass — line numbers may have shifted; the CLI still returns the live
lines (with a stderr Warning in line mode). `needs-reindex` (index-status)
means the index configuration changed (model, format, chunker) — queries
against it fail with ConfigError until a reindex rebuilds it.

**Never run `reindex-project` unless explicitly told to** — it is a full
re-embed (minutes of Ollama time). The watcher keeps indexes fresh
incrementally; `needs-reindex` states are resolved by the owner's call.

## Automatic indexing (watch daemon — standard setup)

Projects are indexed **automatically** via the inotify watcher:

```bash
code-indexer watch /path/repo --background      # one project
code-indexer watch --all --background           # every registered project
```

`--background` daemonizes (PID file `<INDEX_ROOT>/watch.pid`, flock-guarded;
logs `<INDEX_ROOT>/watch.log`). File events trigger an incremental index pass
after a 3 s quiet period (bursts coalesce); a full staleness sweep runs every
300 s even with no events. Watchers self-heal; a stopped watcher leaves no
residue. Start a watcher for a project right after `add-project` — one-shot
commands still work without any watcher, but the watcher keeps the index
fresh so queries never pay the staleness pass.

Re-running `watch ... --background` while a live watcher already holds the
pidfile is idempotent: it prints
`watch: already running (pid=NNNN, pidfile=...)` plus the projects the live
watcher serves, and exits 0 when the requested project(s) are already covered
by that watcher (a watcher on a parent directory covers subprojects). If the
requested project(s) are NOT covered, it exits with a clearly-worded
nonzero status (3) telling you to stop the live watcher first. The message
"failed to start" never appears for this case — it is reserved for genuine
startup failures.

## Commands (each accepts --skip-stale-check)

```bash
uv run code-indexer <cmd>   # or installed console script
code-indexer add-project /path/repo [--name myproj] [--allow-sensitive]   # register + initial index (foreground in CLI)
code-indexer lookup-project /path/repo                # registration check, no side effects
code-indexer list-projects
code-indexer semantic-search "query" [--project P] [--limit 8] [--file-filter '*.py'] [--symbol-type class] [--language python] [--ranking vector|metadata|hybrid] [--per-file N] [--max-chars N | --max-tokens N] [--format text|compact|json] [--json]
code-indexer skeleton [--project P | --name P] [PATH_PREFIX] [--tree] [--no-signatures] [--limit N] [--json]
code-indexer map ...      # alias for skeleton
code-indexer outline [--project P | --name P] FILE [--docstrings] [--json]
code-indexer file-outline ...   # alias for outline
code-indexer find-symbol Name [--project P] [--type class]   # exact-first, capped substring fallback
code-indexer find-symbol [--project P] [--type class] [--file src/x.py] [--limit 25]   # browse mode (no name)
code-indexer find-definition Name [--project P]                      # exact match only
code-indexer find-references Name [--project P] [--relationship calls]  # all confidence=heuristic
code-indexer find-callers Name [--project P] [--depth 2] [--max-nodes 40]  # fan-in over AST refs/imports (ast vs heuristic confidence)
code-indexer find-callees Name [--project P] [--depth 2] [--max-nodes 40]  # fan-out
code-indexer deps PATH [--project P] [--direction in|out|both] [--depth 2] [--format tree|edges|json]  # import graph around a file
code-indexer changed-symbols [--project P] [--base HEAD] [--staged] [--impact] [--json]  # post-edit diff -> symbols (see decision tree)
code-indexer add-project PATH --include 'src/**'  # scoped indexing (repeatable; index-more extends)
code-indexer get-code-context src/f.hpp --start-line 40 --end-line 80   # or --symbol Foo::bar
code-indexer overview [--project P] [--path-prefix X] [--max-lines 60] [--json]
code-indexer find-callers NAME [--project P]   # graph navigation (see below)
code-indexer find-callees NAME [--project P]
code-indexer deps PATH [--project P] [--direction in|out|both]
code-indexer index-status /path/repo
code-indexer doctor [--json]              # health check; exit 1 on any fail
code-indexer reindex-project /path/repo   # full rebuild, foreground
code-indexer remove-project /path/repo    # DESTRUCTIVE: drops collection + manifest + registry entry
code-indexer watch /path/repo [--duration 300] [--background]   # optional inotify re-index daemon
code-indexer watch --all                  # watch every registered project
code-indexer unwatch /path/repo           # stop the watcher (reverse of watch)
code-indexer unwatch --all [--timeout 10] # stop it for every registered project
```

## Subcommand reference

- **add-project / lookup-project / list-projects** — registry management.
  `add-project` registers a repo and runs the initial index (foreground);
  `lookup-project` is a side-effect-free registration check. Sensitive
  files (.env*, keys, credentials*, and files matching high-confidence
  secret content patterns) are skipped by default and counted in
  index-status; `--allow-sensitive` stores the per-project override to
  index them too.
- **semantic-search** — natural-language search over indexed chunks.
  Best when you know *what the code does*, not what it's called. Follow up
  with `find-definition`/`get-code-context` for the precise lines.
  Optional `--ranking` diversifies the query-time ranking:
  `metadata` adds small score adjustments (definition boost, test-path
  penalty) on top of cosine; `hybrid` fuses cosine with a lexical
  token-overlap score (better for queries containing exact identifiers);
  default `vector` is pure cosine. In every mode pure-data chunks
  (`.json`/`.yaml`/`.toml`) are down-weighted and capped to at most 40% of
  the top-k window so they cannot crowd out code for analysis-shaped
  queries; they stay searchable, and the mitigation is waived when the
  query names a data format. `--symbol-type` and `--language` scope
  the search by payload filters without extra round trips.
  Result shaping: same-file overlapping/adjacent hits merge into their
  union range (best score, symbols listed together); `--per-file N` caps
  hits per file; `--max-chars`/`--max-tokens` trim lowest-ranked hits to a
  budget (trailing dropped-count line, `truncated`/`dropped` in JSON);
  `--format compact` prints one line per hit
  (`path:start-end  symbol  score`) — prefer compact + get-code-context
  for agent loops (855 B vs 2.2 KB measured). Multi-project searches fuse
  per-collection ranks with Reciprocal Rank Fusion (`rrf_score`,
  `vector_score` keeps the original cosine).
  **`--language` values (exhaustive)** — the stored `lang` payload, derived
  from the file extension at index time; filter matches it exactly:
  - AST-parsed via tree-sitter (structured chunks with symbol names):
    `python`, `javascript`, `typescript`, `rust`, `go`, `java`, `c`, `cpp`,
    `csharp`, `ruby`, `php`, `bash`, `lua`, `swift`, `kotlin`, `markdown`,
    `json`, `yaml`, `toml`, `html`, `css`.
    NOTE the one map mismatch: `.sh` files PARSE via the `bash` grammar but
    the stored payload value is `shell` — filter `.sh` with
    `--language shell`. C/C++/Java carry weak visibility (everything
    public) by design; other grammars carry real visibility.
  - Fallback-only (regex-window chunks, no AST symbols; stored as the raw
    extension string): any other extension — observed values `gitignore`,
    `lock`, `python-version`, `csx`, `text`. Still valid filter values.
- **skeleton** (alias `map`) — token-reduction workhorse. Dense structural
  map of the whole project or a `PATH_PREFIX` subtree: one file header per
  group, one symbol per line (`type name:start-end  signature`). No bodies,
  no prose — a whole-repo orientation layer in a few hundred–few thousand
  tokens. `--tree` instead prints a directory tree with per-dir symbol
  count + dominant language; `--no-signatures` drops signature text;
  `--limit N` caps symbol lines per file. Call this **before any
  exploratory reads** in an unfamiliar repo.
  Project auto-resolution: when `--project` is omitted, the positional
  `PATH_PREFIX` (then the cwd) is matched against registered projects —
  the most-specific containing project wins. So
  `code-indexer skeleton . --tree --limit 6` works from inside a
  registered repo even with multiple projects registered; `.`/absolute
  prefixes are normalized to project-relative. The old
  single-project/ambiguity error only fires when nothing matches.
- **outline** (alias `file-outline`) — one file's top-level declarations,
  signatures, and line ranges, one per line. Use to decide whether a full
  `get-code-context` call is worth it. `--docstrings` adds the first
  docstring/comment line per declaration (the only subcommand that reads
  source at query time).
- **find-symbol** — symbol lookup by exact name (capped substring
  fallback), or **browse mode** with no name: filter by `--type`,
  `--file`, `--limit` to list "what exists where". With schema v3, output
  includes the persisted signature.
- **find-definition** — exact-name definition location only.
- **find-references** — all references to a symbol, optionally filtered by
  `--relationship` (e.g. `calls`). Confidence is heuristic.
- **get-code-context** — the only way to read actual source: a line range
  (`--start-line/--end-line`) or the lines around a symbol
  (`--symbol Foo::bar`). Never read whole files; this answers "show me the
  code" questions.
- **index-status / doctor / reindex-project / remove-project / watch / unwatch** — index
  lifecycle. Reports `idle | indexing | never-indexed | error | needs-reindex`:
  `never-indexed` is a registered project whose manifest has no completed
  pass (an interrupted/killed `add-project`) — no index exists yet, so do not
  trust empty results from it; `reindex-project` builds it. `reindex-project`
  is a full rebuild into a temporary collection, swapped in on completion
  (also populates schema-v3 signature/visibility columns on old manifests).
  `needs-reindex` means the index was built with a different embed
  model/dimension/text format/chunker version than the current configuration:
  queries fail with `ConfigError` naming the difference until
  `reindex-project` runs; `--skip-stale-check` does not bypass it;
  `remove-project` is
  destructive; `watch` is the background freshness daemon; `unwatch` is the
  reverse of `watch` (see below).
- **doctor** — installation health check, one line per check
  (`ok|warn|fail <name>: <detail>`, exit 1 on any fail; warnings do not
  fail). Covers runtime versions, Ollama (reachable/model/dimension probe),
  Qdrant (reachable/version/client-vs-server compatibility, collections vs
  registry, dims), INDEX_ROOT writability + disk, SQLite integrity, watch
  pidfile + locks, and per-project fingerprint status. Dev/ops surface —
  CLI-only, no MCP tool; agents use index-status instead. Run it when
  searches misbehave or after changing OLLAMA_URL/QDRANT_URL/EMBED_MODEL.

## Token reduction (schema v3)

Signatures + visibility are extracted at index time (schema v3;
`signature`/`visibility` columns on `symbols`; v2 manifests ALTER-migrate,
old rows keep NULL — `reindex-project` fills them). `skeleton`/`outline`/
`find-symbol` are manifest-only at query time: zero re-parsing. Dense
output: one symbol per line, no blank lines; `--json` for structured
consumers. `outline --docstrings` is the only on-demand source read.
C/C++ visibility is weak by design (everything public — access sections
are not tracked); do not oversell it.

## watch details

- `--duration T` bounds its life: exits 0 after T seconds (`0`/omitted =
  forever; honored by `--background` too). SIGINT/SIGTERM exit 0.
- Degradation: no watchdog installed or inotify watch-descriptor
  exhaustion -> quiet-period polling fallback (hash scan per project per
  quiet tick). Correctness is never lost, only latency.
- `--foreground` (default) is the plain inherited-stdio behavior, and takes
  the SAME flock-guarded `<INDEX_ROOT>/watch.pid` as `--background`, so "one
  watcher per index root" holds in both modes.
- Projects: repeatable path/slug/name args, or `--all` (round-robin).
  Never pass both.
- A second watcher is refused while a live one holds the PID-file flock.
- `unwatch [PATH...|--all] [--timeout S]` is the reverse: it SIGTERMs the
  live watcher holding `<INDEX_ROOT>/watch.pid` (either mode), waits for the
  flock to be released (default 10 s), and reports the projects that watcher
  was serving. The flock — never pid existence — is the liveness test, so a
  stale pidfile left by a killed watcher is cleaned up silently. No watcher
  running is a clean no-op (one-line message, exit 0); exit 1 only when a
  live holder could not be stopped (`--timeout` elapsed, or its pid is
  unreadable). Arguments mirror `watch`: paths or `--all`, never both.

## --name alias

`find-symbol`, `find-definition`, `find-references`, `get-code-context`
accept BOTH `--project X` and `--name X` for the project argument (path,
slug, or registered custom name) — typer dual option names.

## Key rules

- `--skip-stale-check` skips the staleness probe / incremental pass (fast,
  may serve slightly stale results). Default behavior: search/status trigger
  a re-scan if STALE_TTL (60 s) elapsed; concurrent invocations serialize via
  a per-project flock — exactly one index pass runs, others print
  "<slug>: indexing in progress". `--fresh` forces the scan now (ignore
  STALE_TTL for this call).
- `get-code-context` checks the live file hash against the manifest:
  `--symbol` mode re-resolves the symbol on the live file; line-range mode on
  a changed file returns the requested lines plus a one-line `Warning: ...`
  on stderr (exit 0) and `stale: true` in JSON. Search hits carry `file_hash`
  and `indexed_at`.
- Workflow: `semantic-search` -> `find-symbol`/`find-definition` ->
  `get-code-context --symbol`. Never read whole files.
- First index of a big repo is slow (~1.2 docs/s GPU-hosted); check
  `index-status` before trusting thin results.
- Never manage index state yourself (manifests, chunk hashes, collections) —
  the tool owns them.
- Env: `OLLAMA_URL`, `QDRANT_URL`, `EMBED_MODEL`, `INDEX_ROOT`
  (default `~/.code-indexer`), `STALE_TTL`, `WATCH_DEBOUNCE`
  (`watch` quiet period, default 3 s; alias `WATCH_QUIET_PERIOD`),
  `WATCH_SWEEP_INTERVAL` (periodic full sweep, default 300 s),
  `OLLAMA_TIMEOUT` (Ollama connect/read timeout, default 120 s; transient
  failures retry with exponential backoff + jitter, max 5; fatal errors —
  e.g. model not found — fail fast with a ConfigError),
  `EMBED_CONCURRENCY` (default 1; >1 pipelines parsing while embedding),
  `QUERY_INSTRUCTION` (query-side embedding instruction, ON by default
  after an eval win; `QUERY_INSTRUCTION=""` disables; documents are never
  instructed — toggling needs no reindex),
  `EMBED_CACHE` (`1` = content-addressed embedding cache at
  `<INDEX_ROOT>/embed_cache.db`; unchanged chunks/queries skip Ollama),
  `EMBED_CACHE_MAX_GB` (default 2; LRU eviction by last_used).

## Gotchas

- `remove-project` deletes the whole index; re-adding re-indexes from scratch.
- Existing `idx_*` collections are reused if the registry is retained; a fresh
  registry re-add re-attaches to the same collection and re-indexes (chunk-hash
  diff means only changed chunks are re-embedded; deterministic uuid5 point IDs
  make re-upserts idempotent).
- `.gitignore`/`.codeindexignore` are honored; files >1 MB and binaries skipped.
- Branch switches re-index correctly (content-hash diff, mtime ignored).
