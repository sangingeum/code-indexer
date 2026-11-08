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
- **Flags**: `--project` / `--name` <value>, `--vectors` <value> (default `auto`), `--repair-points`, `--skip-stale-check`, `--json`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `remove-project`

- **Class**: write
- **Arguments**: `path` (optional)
- **Flags**: `--project` / `--name` <value>, `--skip-stale-check`, `--json`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `sweep-orphans`

- **Class**: write (with --apply)
- **Arguments**: `path` (required)
- **Flags**: `--apply`, `--json`, `--format` <value>, `--skip-stale-check`, `--refresh`
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
- **Flags**: `--project` / `--name` <value>, `--json`, `--format` <value>, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `list-projects`

- **Class**: read
- **Arguments**: none
- **Flags**: `--json`, `--format` <value>, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `lookup-project`

- **Class**: read
- **Arguments**: `path` (optional)
- **Flags**: `--project` / `--name` <value>, `--json`, `--format` <value>, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `doctor`

- **Class**: read
- **Arguments**: none
- **Flags**: `--json`, `--format` <value>, `--skip-stale-check`, `--refresh`, `--probe-timeout` <value>
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).
<!-- generated:commands-indexing:end -->

## What each command writes, and where

These commands are not read answers, so they do not follow the read contract
in `docs/json-schema.md` (a read writes stderr only for the staleness
warning, the coverage line, the fingerprint sentence, the scoped-index note and
a graph answer's `note:` lines). The lines each one writes on success:

| command | **stdout** | **stderr** |
|---|---|---|
| `add-project`, `index-more`, `reindex-project`, `remove-project` | registration/result line, pass line and any `note:` line (`vectors=off`, `--allow-sensitive`, scoped indexing, `no indexing pass has completed`) | nothing on success |
| `sweep-orphans` | counts line and file list | the shared `Warning: index is stale: ...` line, whenever the freshness verdict is stale — the same line every other command that reads the index writes; `note: <n> path(s) with rows but no files row are still present on disk and were left alone: ...`; `note: dry run — nothing was deleted; pass --apply to remove these rows` |
| `watch` | start/stop and watched-project lines | `watch: skipping project <slug> (<path>): path is missing or unreadable`; `watch: already running (pid=..., pidfile=...)`; `watch: requested project(s) are NOT covered by the live watcher ...`; the holder-undetermined note |
| `unwatch` | the stopped pid and the projects it was serving | `unwatch: note: it did not cover every requested project` |

`eval --out` is the dev harness, not one of these: `wrote <path>` goes on
stderr beside its report on stdout.

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
- `index-more` accepts `<project> <subpath>` or `--project <project> <subpath>`,
  rejects the flag plus two positionals as ambiguous, and stores an absolute
  subpath relative to the project root -- one outside it is exit 2, and the root
  itself is `**`. Neither spelling infers a project from the working directory.
- `reindex-project` is a full rebuild, also foreground; a vectors-on rebuild
  embeds nothing its current rows already store, so it is cheaper than the first pass.

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

- `--duration T` exits 0 after T seconds (`0`/omitted = forever, honoured by
  `--background` too); SIGINT and SIGTERM exit 0.
- One watcher per index root: both modes take the same flock-guarded
  `<INDEX_ROOT>/watch.pid`; a second is refused while a live one holds it, and
  `--background` confirms the daemon before printing `watch daemon started`.
- No watchdog, or inotify descriptor exhaustion, degrades to quiet-period
  polling: correctness is never lost, only latency. A project whose root is
  missing is skipped with a warning; the watcher exits nonzero only if it covers none.
- Projects are repeatable path/slug/name arguments or `--all`, never both.
- `unwatch` SIGTERMs the live holder, waits for the flock (`--timeout`, default
  10 s) and reports the projects it served; the flock, never pid existence, is
  the liveness test. No watcher running is a clean no-op at exit 0; an unstoppable holder is exit 1.
- **Version bumps need an ordered restart.** A watcher re-stamps its own
  fingerprint every sweep, so one left running across an upgrade reverts
  `needs-reindex`: `unwatch --all`, `reindex-project <path>`, then `watch`.

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
`vectors_state=current|absent|stale|error` (stale is the read's identity
comparison, never a recorded write) -- plus `rows=current|absent`, the
parse-verdict counts (`parse=parsed:N,parse_error:N,parse_unavailable:N`),
and, when chunk errors are recorded, `error_count=N` (the total) beside `last_errors=` (at most three examples; neither field with no errors).

Both verdict pairs are flags a caller branches on, and the text form adds one
stderr line saying what the pair MEANS, from the same verdict object the
`vectors=`/`vectors_state=` fields come from; the ids below are
`MEANING_BRANCHES` and json-schema.md carries each row's field values.

- `row-keys-differ` (`fingerprint=needs-reindex`): the named row keys differ,
  so the row reads refuse until a rebuild; a stale vector side is named too,
  and with vectors off the sentence says the clearing pass is a row pass.
- `vectors-on-identity-matched` (`vectors=on`, no recorded identity key
  differs): every key a read can compare matched; the DIMENSION is the one it
  cannot compare, because learning it needs the service.
- `vectors-on-identity-differs` (`vectors=on`, a recorded identity key
  differs, `vectors_state=stale`): the sentence names the key that did not
  match and says `reindex-project` clears it -- never "every key matched".
- `vectors-on-marker-stale` (`vectors=on`, identity matches, the manifest
  records `stale` -- a marker this build never writes): the sentence says the
  MANIFEST's record is what is stale.
- `vectors-off-identity-differs` (`vectors=off`, a recorded identity
  differs): there is no vector side, so nothing about vectors is stale, no
  rebuild is prescribed, and the difference is inert until a vectors-on pass.
- `vectors-off-identity-matched` (`vectors=off`, identity matches): every
  comparable key matched and no rebuild is prescribed -- no vector side here.
- `no-verdict-recorded` (no `fingerprint` field): the comparison did not run,
  and the sentence says so -- silence would read as a pass.

`vectors_state` is the RECORDED marker; `stale` is written by no pass in this
build, and with vectors off it prints as-is -- "not `stale`" is not "matched".

JSON carries the same sentence as `fingerprint_meaning`; the field names and
the one-line shape are unchanged because the sentence rides stderr.

Every read answer's JSON, `index-status` included, carries the `coverage`
block: files in scope, parsed and unparsed counts, withheld files, languages
with no AST reader, freshness -- fields in [machine-output.md](machine-output.md).

`index-status` takes `--format text|json` (`--json` is the alias) and
`--format compact`, the SAME line the text form prints -- it is already
`key=value`. `doctor` and `list-projects` refuse `compact` with exit 2 like
every other read without a one-line-per-record form ([machine-output.md](machine-output.md)).

It also names what the sensitive layer withheld: a suffixed
`1 file skipped (sensitive content)` (or `sensitive filename`), followed by the
`--allow-sensitive` hint, and in `--json` the `withheld_count` / `withheld_files`
/ `withheld_summary` / `withheld_unexamined_count` fields. Those files have no
rows and are not stale reasons (see "Indexing scope and environment"); the count
on the line is this tree's, the last pass's own only in `sensitive_skipped`.

**Consumer rule:** `stale: false` beside a non-zero withheld count is not
"nothing was withheld": those files are neither stale nor indexed, so branch
on the withheld count.

`list-projects` gives the same summary for every registered project;
`lookup-project` answers only whether a path, slug or name is registered, and
indexes nothing. `doctor` runs the installation health checks (versions,
Ollama, Qdrant, `INDEX_ROOT` and disk, SQLite integrity, the watcher pidfile
and locks, per-project fingerprint status) and exits 1 when one fails.

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
vectors-on project stays on across the upgrade. `watch` follows the recorded
mode and never turns vectors on itself. Turning vectors on for a current
project reparses nothing and reads no source file: it embeds the stored chunk
text. A changed embedding identity is a NEW physical collection
(`idx_<slug>__<identity digest>`) that every vector-writing pass binds the
`idx_<slug>` alias to; writes name it, reads resolve the alias.

**Point identity.** A point id is positional (`uuid5(project|file|chunk_index)`), so every pass
re-addresses by content: a chunk whose text and symbol the file's points already carry keeps its
vector but is written at the id its CURRENT `chunk_index` names, with the current payload; a chunk
found nowhere is embedded; points past the new count are purged. Two rows with the same text and
symbol (a repeated declaration) share a vector but never a point. `reindex-project
--repair-points` runs that over every stored row and is idempotent: a second run on an agreeing
index writes no point, reports `files_committed 0` and leaves `index_revision` alone. It refuses
(exit 4) a row whose vector is nowhere, reports idle (exit 0) with the row count on a project that
holds no vectors, and keeps exit 2 for `--vectors off` passed WITH it.

**A repair embeds no chunk and needs no embedding service.** The dimension is already recorded
(`meta.embed_dim`, else the length of a vector the collection holds), so a repair dials no
embedder at all and works with the service down; with neither recorded it refuses (exit 4) naming
`reindex-project` rather than asking the service to guess. A repair that writes no point writes no
manifest, so `index_revision` and `last_indexed` stay put; a row left unrepairable is not a no-op
-- the vector state is recorded, then exit 4.
unrepairable is not a no-op -- the vector state is recorded, then exit 4.

The three version axes:

| axis | recorded in | a bump means |
|---|---|---|
| `extraction_version` | `meta` | stored rows are obsolete: one pass per project |
| `embedding_version` | `meta` | a NEW collection; rows and chunk text survive |
| `index_schema_version` | `meta` | the manifest migrates on the next WRITE open; the collection is untouched |

**A read never migrates the manifest.** Reads open it read-only -- no
journal-mode write, no DDL. An older manifest is reported as `schema=<n>` and
its row surfaces refuse with exit 4 until a pass runs.

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
| `STALE_TTL` | -- | retired: accepted and ignored -- setting it neither raises nor changes behaviour, because a read reports its verdict from the stat scan |

`.gitignore`, nested `.codeindexignore` files and the repository-local
`.git/info/exclude` (per repository, never a user-global file) are honoured;
`--include` scope globs narrow a project at registration, spelled relative to
its root; secret-bearing files are skipped unless `--allow-sensitive`; files
larger than `MAX_FILE_BYTES` (1 MB default) and binaries are skipped, and
`index-status` reports the skip counts.

`.gitignore` and `.codeindexignore` answer different questions -- git decides
what is committed, the index what is searchable. This repository excludes two
non-source trees in the root `.codeindexignore`: `runs/` (round captures, gate
output and command logs, kept in the working checkout, no longer tracked) and
`eval/` (the retrieval corpus -- questions shaped like the ones users type, so
indexing them ranks near-duplicate queries above the code they point at; this
one stays tracked). Anchored to the repository root (`/runs/`, `/eval/`), they
leave a same-named directory under `src/` indexed. Nested ignore files compose
as git does: each spec is read root-outwards relative to its own directory,
and the last matching rule decides -- a
deeper `!pattern` re-includes what a shallower file excluded, and an anchored
pattern (`/secret.txt`) anchors to the directory carrying it. The repo-local
`.git/info/exclude` joins there -- anchored at the repository root even when
only a subdirectory is scanned, read before every `.gitignore`, per repository
only -- so the scan keeps what `git check-ignore` does not ignore. Rows
already in a manifest leave on a reindex, not on the edit.

A file the sensitive scan withholds sits in the gap between scan set and
manifest: scanned, deliberately rowless, and **not** a staleness reason. The
diff re-applies the pass's own decision function (`sensitive.skip_verdict`,
over the same 256 KiB prefix) and reports such a path with the layer that
fired -- `1 file skipped (sensitive content)` on the `index-status` line, and
`withheld_count` / `withheld_files` / `withheld_summary` in `--json` -- while
the verdict stays `fresh`. A genuinely new non-sensitive file still reports
`added`, and a withheld file that changes stays withheld; under
`--allow-sensitive` nothing is withheld, so every rowless path is `added`,
exactly the row the next pass writes.

The check reads no more than the pass does: only rowless scanned paths are
opened, up to the same 256 KiB prefix, inside an 8 MiB budget per check
charged in bytes actually read (an approximation, not a measured threshold).
A path past the budget stays `added` and rides `withheld_unexamined_count`.

A rowless path the check cannot OPEN is a third state -- scan and diff are two
moments, so it reports `unreadable_count` / `unreadable_files` with `1 file
could not be read (permission or I/O error)`, never `added` or withheld.
