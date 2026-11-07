# indexing -- building, repairing and watching an index

Governed by: ADR-0003 section 1 (write commands and the `--refresh` rule),
section 5 (exit codes); ADR-0009 section 2 (this file's contents and budget);
ADR-0008 (product naming). Open this reference when the task is to build or
repair an index, or to ask what state one is in.

An index pass needs no service: `add-project` writes files, symbols,
references, imports and chunk text and records `vectors=off` unless asked
otherwise. The same is true of every pass command below.

## Commands

<!-- generated:commands-indexing:begin -->
### `add-project`

- **Class**: write
- **Arguments**: `path` (required)
- **Flags**: `--name` <value>, `--allow-sensitive`, `--include` <value>, `--priority` <value>, `--vectors` <value> (default `auto`), `--skip-stale-check`, `--json`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `index-more`

- **Class**: write
- **Arguments**: `first` (optional), `second` (optional)
- **Flags**: `--project` / `--name` <value>, `--vectors` <value> (default `auto`), `--skip-stale-check`, `--json`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `reindex-project`

- **Class**: write
- **Arguments**: `path` (optional)
- **Flags**: `--project` / `--name` <value>, `--vectors` <value> (default `auto`), `--skip-stale-check`, `--json`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `remove-project`

- **Class**: write
- **Arguments**: `path` (optional)
- **Flags**: `--project` / `--name` <value>, `--skip-stale-check`, `--json`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `sweep-orphans`

- **Class**: write (with --apply)
- **Arguments**: `path` (required)
- **Flags**: `--apply`, `--json`, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `watch`

- **Class**: write
- **Arguments**: `paths` (optional)
- **Flags**: `--all`, `--duration` <value>, `--background`, `--foreground`, `--skip-stale-check`
- **JSON**: text only, no `--json`.

### `unwatch`

- **Class**: write
- **Arguments**: `paths` (optional)
- **Flags**: `--all`, `--timeout` <value> (default `10.0`), `--skip-stale-check`
- **JSON**: text only, no `--json`.

### `index-status`

- **Class**: read
- **Arguments**: `path` (optional)
- **Flags**: `--project` / `--name` <value>, `--json`, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `list-projects`

- **Class**: read
- **Arguments**: none
- **Flags**: `--json`, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `lookup-project`

- **Class**: read
- **Arguments**: `path` (optional)
- **Flags**: `--project` / `--name` <value>, `--json`, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `doctor`

- **Class**: read
- **Arguments**: none
- **Flags**: `--json`, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).
<!-- generated:commands-indexing:end -->

## Building and extending

```bash
# register and index a repository in the foreground (no service needed)
code-indexer add-project /path/to/repo

# register it with a custom collection name instead of the hash slug
code-indexer add-project /path/to/repo --name myproj --vectors on

# index a subtree the first pass skipped (ignore rules, --include scope)
code-indexer index-more /path/to/repo vendor/generated
code-indexer index-more --project myproj docs

# rebuild from scratch -- the answer to needs-reindex or never-indexed
code-indexer reindex-project /path/to/repo
```

- `add-project` refuses a second registration of the same path
  (`ArgumentError`, exit 2) rather than merging it; use `index-more` to widen
  an existing project.
- The first pass runs in the **foreground** and returns when it finishes. An
  interrupted `add-project` leaves the project registered and reporting
  `state=never-indexed`; `reindex-project` builds it.
- `index-more` accepts `<project> <subpath>` or `--project <project>
  <subpath>` and rejects the flag plus two positionals as ambiguous. Neither
  spelling infers a project from the working directory.
- `reindex-project` is a full rebuild, also foreground. It re-embeds from the
  embedding cache where it can, so a vectors-on rebuild is cheaper than the
  first pass.

## Removing and sweeping

```bash
# DESTRUCTIVE: drops the collection, the manifest and the registry entry
code-indexer remove-project /path/to/repo

# list residue rows for files that left the tree (read: dry run)
code-indexer sweep-orphans /path/to/repo

# delete those rows
code-indexer sweep-orphans /path/to/repo --apply
```

`remove-project` deletes the whole index; re-adding re-indexes from scratch.
`sweep-orphans` is a read in its default dry-run shape and a write only with
`--apply`: `sweep-orphans --apply` is the write invocation, which is why the
command carries both the read and the write class.

## Watching

```bash
# keep every registered project fresh, in the background
code-indexer watch --all --background

# watch one project for five minutes, in the foreground
code-indexer watch /path/to/repo --duration 300

# stop it (either mode), then restart on the new code after an upgrade
code-indexer unwatch --all
```

- `--duration T` bounds the watcher's life: it exits 0 after T seconds
  (`0`/omitted means forever, honoured by `--background` too); SIGINT and
  SIGTERM exit 0.
- One watcher per index root: both modes take the same flock-guarded
  `<INDEX_ROOT>/watch.pid`, and a second watcher is refused while a live one
  holds it. `--background` prints `watch daemon started` only after
  re-confirming the daemon is alive and still holding the pidfile.
- No watchdog, or inotify descriptor exhaustion, degrades to quiet-period
  polling (a hash scan per project per quiet tick). Correctness is never
  lost, only latency. A registered project whose root is missing or
  unreadable is skipped with one warning naming it; the watcher exits
  nonzero only when it covers no project.
- Projects are repeatable path/slug/name arguments or `--all`, never both.
- `unwatch` SIGTERMs the live holder, waits for the flock to be released
  (`--timeout`, default 10 s) and reports the projects it was serving. The
  flock, never pid existence, is the liveness test, so a stale pidfile left
  by a killed watcher is cleaned up silently. No watcher running is a clean
  no-op at exit 0; a live holder that could not be stopped is exit 1.
- **Version bumps need an ordered restart.** A watcher re-stamps its own
  fingerprint on every sweep, so a watcher left running across an upgrade
  silently reverts `needs-reindex`. After upgrading: `unwatch --all`,
  `reindex-project <path>` per project, then `watch --all --background`.

## Asking what state an index is in

```bash
code-indexer index-status --project /path/to/repo
code-indexer index-status /path/to/repo --json
code-indexer list-projects
code-indexer lookup-project /path/to/repo --json
code-indexer doctor
```

`index-status` reports the **pass state** as `state=`:

| state | meaning | what to do |
|---|---|---|
| `idle` | indexed, no pass running | normal use |
| `indexing` | a pass is running | wait, then retry |
| `never-indexed` | registered, no completed pass | do not trust empty answers; `reindex-project` |
| `needs-reindex` | built by a different configuration | report to the owner; the row surfaces refuse with exit 4 |
| `error` | the last pass failed | `code-indexer doctor` |

It also reports both halves of the vector story -- `vectors=on|off` and
`vectors_state=current|absent|stale|error`, where `stale` means the recorded
embedding identity is not the one this build would write -- plus
`rows=current|absent` and the parse-verdict counts
(`parse=parsed:N,parse_error:N,parse_unavailable:N`).

`list-projects` gives the same summary for every registered project.
`lookup-project` answers only whether a path, slug or name is registered, and
indexes nothing. `doctor` runs the installation health checks (runtime
versions, Ollama, Qdrant, `INDEX_ROOT` writability and disk, SQLite
integrity, the watcher pidfile and locks, per-project fingerprint status) and
exits 1 when any check fails.

## Project selection

- `--project X` is accepted by every project-scoped command, with `X` a
  path, a slug or a registered custom name.
- `--name X` is a second spelling of the same option on `skeleton`/`map`,
  `outline`/`file-outline`, `find-symbol`, `find-definition`,
  `find-references` and `get-code-context`.
- Lifecycle commands (`reindex-project`, `remove-project`, `lookup-project`,
  `index-more`) and `index-status` accept the historical positional path too.
  For `reindex-project`, `remove-project` and `lookup-project` the flag wins
  when both are given.
- The mutating commands (`reindex-project`, `remove-project`, `index-more`)
  never infer a project from the working directory: with neither spelling
  they stop and list the registered projects.

## Vectors are an option, not a precondition

| flag | `add-project` | `index-more`, `reindex-project` |
|---|---|---|
| `--vectors auto` (default) | a NEW project records `off` | the project's recorded mode |
| `--vectors on` | embed and upsert as it parses | embed from stored chunk text |
| `--vectors off` | rows only, no service | rows only, no service |

`auto` never silently enables embedding on a fresh install, and an existing
vectors-on project stays on across an upgrade. `watch` follows the recorded
mode and never turns vectors on by itself. Turning vectors on for a current
project reparses nothing and reads no source file: it embeds the stored chunk
text. A changed embedding identity is a NEW physical collection
(`idx_<slug>__<identity digest>`), and every vector-writing pass binds the
stable `idx_<slug>` alias to it; writes name the physical collection, reads
resolve the alias.

The three version axes:

| axis | recorded in | a bump means |
|---|---|---|
| `extraction_version` | `meta` | stored rows are obsolete: one pass per project |
| `embedding_version` | `meta` | a NEW collection; rows and chunk text survive |
| `index_schema_version` | `meta` | the manifest migrates on the next WRITE open; the collection is untouched |

**A read never migrates the manifest.** Reads open it read-only -- no
journal-mode write, no DDL -- so a read cannot destroy anything. An older
manifest is reported as `schema=<n>` and its row surfaces refuse with exit 4
until a pass runs.

## Configuration

Resolution order: CLI flags > environment variables > defaults. The defaults
for `OLLAMA_URL` and `QDRANT_URL` are LAN placeholders and must be set.

| Variable | Default | Effect |
|---|---|---|
| `INDEX_ROOT` | `~/.code-indexer` | registry, manifests, embedding cache, watcher pidfile |
| `OLLAMA_URL` | LAN placeholder | embedding service |
| `QDRANT_URL` | LAN placeholder | vector store |
| `EMBED_MODEL` | `qwen3-embedding:8b` | embed model; a change is a new embedding identity |
| `EMBED_BATCH` | 48 | texts per Ollama embed request |
| `UPSERT_BATCH` | 256 | points per Qdrant upsert |
| `EMBED_CONCURRENCY` | 1 | passes that parse while another embeds |
| `OLLAMA_TIMEOUT` | 120 s | connect/read timeout; transient failures retry with backoff, max 5 |
| `EMBED_CACHE` | `1` | content-addressed embedding cache at `<INDEX_ROOT>/embed_cache.db` |
| `EMBED_CACHE_MAX_GB` | 2 | cache cap, LRU eviction by last use |
| `MAX_FILE_BYTES` | 1048576 | larger files are skipped |
| `CHUNK_MAX_CHARS` | 1000 | chunk size cap |
| `WATCH_DEBOUNCE` | 3 s | `watch` quiet period (`WATCH_QUIET_PERIOD` is its alias and wins when both are set) |
| `WATCH_QUIET_PERIOD` | -- | alias of `WATCH_DEBOUNCE` |
| `WATCH_SWEEP_INTERVAL` | 300 s | periodic full sweep while watching |
| `QUERY_INSTRUCTION` | on | query-side embedding instruction; documents are never instructed, so toggling needs no reindex |
| `PARANOID_HASH` | off | `1` disables the stat fast path and hashes every file |
| `VERBOSE` | off | verbose diagnostics on stderr |
| `STALE_TTL` | -- | retired: still parsed for compatibility and consumed by nothing, because a read reports its verdict from the stat scan |


`.gitignore` and nested `.codeindexignore` files are honoured; `--include`
scope globs narrow a project at registration; secret-bearing files (filename
globs plus high-confidence content patterns) are skipped unless
`--allow-sensitive`; files larger than `MAX_FILE_BYTES` (1 MB default) and
binaries are skipped. The skip counts are reported by `index-status`.
