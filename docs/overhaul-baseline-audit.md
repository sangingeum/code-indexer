# Baseline audit: the current implementation against the overhaul proposal

Scope: what `code-indexer` is and does today, measured, with `path:line`
evidence for every claim. This document decides nothing and changes nothing.
Contested choices are collected in "Questions for the decision leg".

Base revision: tip `ef2be78` (`main`). The working tree carried untracked
`runs/` evidence only; no tracked file was modified by this audit.

Terminology: "manifest" is the per-project SQLite file under the index root;
"index root" is `INDEX_ROOT` (default `~/.code-indexer`, written here as
`<index-root>`); "repository" is `<repo>`, this checkout. Absolute host paths
are deliberately not reproduced.

## Measurement artifacts

Every number below is the output of a command run for this audit. Raw outputs
are untracked, under `runs/milestone0/`:

| Artifact | Produced by |
|---|---|
| `commands.json`, `skipflag.json` | `runs/milestone0/dump_commands.py`, `dump_skipflag.py` |
| `manifest-inventory.txt` | `runs/milestone0/inspect_manifest.py` (SQLite opened `mode=ro`) |
| `offline-probe.json` | `runs/milestone0/probe_offline.py` (two-pass service-unreachable probe) |
| `index-without-vectors.txt` | `runs/milestone0/probe_index_without_vectors.py` (throwaway index root) |
| `importtime.txt`, `import-wall.txt` | `python -X importtime -m code_indexer.cli --help`; `measure_import.py` |
| `pytest.txt`, `collect.txt` | `uv run pytest tests/ -q -W error`; `--collect-only` |
| `mcp-tools.json`, `mcp-tools-summary.txt` | `runs/milestone0/dump_mcp_tools.py` |
| `help-sizes.json`, `help-sizes.txt` | `runs/milestone0/measure_help.py` |
| `module-map.txt` | `runs/milestone0/module_map.py` |
| `test-inventory.txt` | `runs/milestone0/test_inventory.py` |

## 1. Command inventory

The CLI is one typer app in `src/code_indexer/cli.py` with **27 registered
commands, 2 of them hidden aliases** (`map` for `skeleton`, `file-outline` for
`outline`): `cli.py:1334-1335` and `:1376-1377` declare both names on one
function body with `hidden=True`. Measured by importing the app and walking
`typer.main.get_command(app).commands` (`runs/milestone0/commands.json`).

Global option objects reused by many commands: `--skip-stale-check`
(`cli.py:121-123`), `--refresh` (`:125-127`), `--fresh` (`:129-132`).

Runtime classification is **measured**, not inferred: the "exit" and "conns"
columns come from running each command with `QDRANT_URL`/`OLLAMA_URL` pointed
at an unroutable address and, in a second pass, at a local TCP sink that
counts accepted connections (`runs/milestone0/offline-probe.json`).

| Command | Purpose | Output modes | Runtime requirement (measured) | Can write the index on this call | Evidence |
|---|---|---|---|---|---|
| `add-project PATH` | Register a repo and run the first index pass | text only | Qdrant (collection create) **and** Ollama (dimension, embeddings) | always | `cli.py:383-442`; `indexer.py:105-106` |
| `index-more PROJECT SUBPATH` | Append a subtree to the indexed scope and index it | text only | Qdrant + Ollama | always | `cli.py:445-492` |
| `remove-project` | Deregister and delete collection, manifest, registry row | text only | Qdrant (drop collection) | always (destructive) | `cli.py:495-528` |
| `reindex-project` | Full rebuild into a temp collection, then swap | text only | Qdrant + Ollama | always | `cli.py:858-877` |
| `watch` / `unwatch` | inotify freshness daemon; its reverse | text only | Qdrant + Ollama on any changed file | always (watch loops index passes) | `cli.py:880-1036`, `:1039-1121` |
| `sweep-orphans` | Clear residue rows for deleted files; dry-run by default | text, `--json`, `--apply` | manifest only | only with `--apply` | `cli.py:803-821`; `manifest.py:213-231` |
| `list-projects` | Per-project index summary | text only | manifest + Qdrant (collection listing) + Ollama (fingerprint probe) | no | `cli.py:531-550`; measured: blocked >75 s, 14 connect attempts |
| `lookup-project` | Registration check | text only | registry + Ollama (fingerprint probe) | no | `cli.py:553-577`; measured: exit 0, 34.0 s, 5 conns |
| `index-status` | Pass state, fingerprint state, scope coverage | text only | manifest + Ollama (dimension probe) | only with `--refresh` (`cli.py:722`) | `cli.py:683-800`; measured: exit 0, 34.4 s, 5 conns, prints `fingerprint=unverified reason=embedder-unreachable` |
| `doctor` | Installation health check | text, `--json`; exit 1 on any fail | Ollama + Qdrant + index root + SQLite + pidfile | no | `cli.py:135-164`; `doctor.py`; measured: exit 1, 39.9 s, 7 conns |
| `overview` | Project map: languages, dirs, entry points, hotspots | text, `--json`, `--max-lines` | manifest + reads `pyproject.toml`/`package.json`/`CMakeLists.txt` from disk | no (no probe on its path) | `cli.py:167-194`; `overview.py:113,130,144`; measured: exit 0, 0 conns |
| `skeleton` (`map`) | Whole-repo or subtree symbol map | text, `--json`, `--tree`, `--no-signatures`, `--limit` | manifest only | yes, unless `--skip-stale-check` | `cli.py:1334-1373`; `core.py:1003-1004`; measured: 0 conns |
| `outline` (`file-outline`) | One file's declarations and signatures | text, `--json`, `--docstrings` | manifest; `--docstrings` also reads source (`core.py:1118-1128`) | yes, unless `--skip-stale-check` | `cli.py:1376-1403`; `core.py:1107-1108`; measured: 0 conns |
| `find-symbol [NAME]` | Exact-first symbol lookup; browse mode with no name | text, `--json`, `--type`/`--symbol-type`, `--file`, `--substring`, `--limit` | manifest only | yes, unless `--skip-stale-check` | `cli.py:1406-1478`; probe at `:1434` |
| `find-definition NAME` | Exact-name definitions, every row | text only | manifest only | yes, unless `--skip-stale-check` | `cli.py:1481-1508`; `:1493` |
| `find-references NAME` | Reference rows targeting a name | text only; `--relationship`, `--limit` (default 25) | manifest only | yes, unless `--skip-stale-check` | `cli.py:1556-1627`; `:1592` |
| `find-callers NAME` | Fan-in BFS over `refs` | text, `--json`, `--depth`, `--max-nodes` | manifest only | no (no probe on this path) | `cli.py:256-291` |
| `find-callees NAME` | Fan-out BFS over `refs` | text, `--json`, `--depth`, `--max-nodes` | manifest only | no | `cli.py:294-316` |
| `deps PATH` | Import graph around a file | tree, `--format tree\|edges\|json`, `--direction`, `--depth`, `--max-nodes` | manifest only | no | `cli.py:344-380` |
| `get-code-context FILE` | Exact source lines by range or `--symbol` | text, `--json` (carries `stale`) | manifest + live file read | only with `--fresh` (`cli.py:1536`) | `cli.py:1511-1553`; measured: 0 conns |
| `semantic-search QUERY` | Embedding / lexical / fused retrieval | text, `--format compact`, `--format json`/`--json`, `--limit`, `--mode`, `--ranking`, `--rerank`, `--max-chars`, `--max-tokens`, `--per-file` | **Ollama + Qdrant** in default `--mode hybrid`; `--mode lexical` needs neither | yes, unless `--skip-stale-check` | `cli.py:580-680`; `:644`; `core.py:681` |
| `changed-symbols` | Git/worktree diff mapped to symbols; `--impact` adds callers + candidate tests | text, `--json`, `--base`/`--head`/`--staged`/`--include-untracked`, `--impact`, `--depth`, `--max-nodes` | git + live file parse; no manifest, no network | no | `cli.py:197-237`; `changed.py:29-63`; measured: exit 0, 0 conns, `no changes` |
| `eval` | Retrieval-quality harness over a query file | text; `--k`, `--mode`, `--rerank`, `--out` | Ollama + Qdrant | yes via `core.search` | `cli.py:1660-1693`; measured: exit 1, 67.2 s, 10 conns |
| `eval-compare BASE CANDIDATE` | Diff two eval reports | text | local JSON files only | no | `cli.py:1696-1712` |

### Which commands pass `--skip-stale-check` through to the core

Measured by AST walk (`runs/milestone0/skipflag.json`): **every** command that
builds a core passes the flag — `_get_core(skip_stale_check)` (`cli.py:51-57`)
or `_core_entry(project, skip_stale_check)` (`cli.py:240-242`). The flag is
stored on the instance (`core.py:101`) and the single veto is in
`Core.maybe_refresh` (`core.py:232`): `if self.skip_stale_check: return
{"state": "fresh"}`. So the flag is a real no-write guarantee wherever it
reaches a probe — but it only *matters* where a probe is on the path.

Commands whose read path can mutate the index when the flag is **not** given:

| Command | Probe call site |
|---|---|
| `semantic-search` | `cli.py:644`, then `core.search` -> `core.py:681` |
| `skeleton` / `map` | `core.py:1003-1004` |
| `outline` / `file-outline` | `core.py:1107-1108` |
| `find-symbol` | `cli.py:1434` |
| `find-definition` | `cli.py:1493` |
| `find-references` | `cli.py:1592` |
| `get-code-context` | `cli.py:1536`, only when `--fresh` is given |
| `index-status` | `cli.py:722`, only when `--refresh` is given |
| `eval` | `core.search` -> `core.py:681` |

The remaining read commands (`overview`, `find-callers`, `find-callees`,
`deps`, `changed-symbols`, `list-projects`, `lookup-project`, `doctor`,
`eval-compare`) never call the probe, so for them the flag is inert. The
mutating commands (`add-project`, `index-more`, `remove-project`,
`reindex-project`, `watch`, `sweep-orphans --apply`) write regardless.

`--fresh` is accepted by exactly 7 commands: `semantic-search`, `skeleton`,
`outline`, `find-symbol`, `find-definition`, `get-code-context`,
`find-references`. `--refresh` by 2: `semantic-search`, `index-status`
(`runs/milestone0/commands.json`).

## 2. Module map

`src/code_indexer/` holds **33 modules, 10,649 lines** (`wc -l`; imports
extracted by `runs/milestone0/module_map.py`).

| Module | Lines | Owns | Imports from inside the package |
|---|---|---|---|
| `cli.py` | 1716 | the whole typer surface, output rendering, the process-wide `Core` cache, watcher lifecycle | `core`, `doctor`, `evalharness`, `manifest`, `registry`, `graph`, `overview`, `changed`, `watcher`, `langmatrix`, `scanner._matches_include`, `errors` |
| `core.py` | 1358 | `Core` facade: config, registry, embedder, store, indexer, staleness probe, search, skeleton/outline/context, fingerprints | `config`, `embed_cache`, `embedder`, `indexer`, `locks`, `manifest`, `registry`, `fingerprint`, `reranker`, `ranking`, `ts_chunker` |
| `manifest.py` | 642 | SQLite schema and every manifest read/write (files, meta, symbols, refs, imports, index_errors, FTS bridge) | `fts` |
| `watcher.py` | 574 | inotify daemon, `PidFileLock`, polling fallback | none |
| `server.py` | 567 | MCP stdio server: argv building and relay only | `logsetup` |
| `indexer.py` | 551 | scan -> hash diff -> chunk -> embed -> upsert/purge, manifest commits | `config`, `scanner`, `chunker`, `ts_chunker`, `graph_extract`, `embed_text`, `embedder`, `fingerprint`, `manifest`, `store`, `sensitive` |
| `graph.py` | 529 | fan-in/fan-out BFS, `deps` walk, tree/JSON rendering, cap notes | `manifest` |
| `doctor.py` | 482 | health checks (versions, Ollama, Qdrant, disk, SQLite, pidfile, fingerprints) | `config`, `fingerprint`, `watcher` |
| `graph_extract.py` | 419 | tree-sitter and textual reference/import extraction; `EXTRACTION_VERSION` | none |
| `changed.py` | 391 | git diff -> changed symbols, `--impact`, rendering | `ts_chunker`, `indexer._lang_from_ext`, `manifest` |
| `ts_chunker.py` | 389 | tree-sitter chunking, signatures, visibility; `CHUNKER_VERSION` | `chunker` |
| `ranking.py` | 384 | score adjustment, data-file mitigation, hit merging/shaping | none |
| `evalharness.py` | 266 | eval runner and report comparison | `core` |
| `overview.py` | 261 | project overview projection and rendering | `manifest` |
| `scanner.py` | 240 | `.gitignore`/scope scan, stat fast-path input | none |
| `embedder.py` | 227 | Ollama client, batching, retries, embed cache, query instruction | `embed_cache`, `embed_text`, `fingerprint` |
| `fingerprint.py` | 190 | recorded-vs-current fingerprint, mismatch messages | `embed_text`, `graph_extract`, `ts_chunker` |
| `store.py` | 180 | Qdrant adapter: collections, aliases, upsert, purge, search | none |
| `chunker.py` | 174 | chunk model and regex fallback chunking | none |
| `langmatrix.py` | 169 | language-to-grammar matrix, `languages_without_ast` | `ts_chunker`, `graph_extract` |
| `registry.py` | 158 | project registry (`registry.db`) | none |
| `fts.py` | 131 | FTS5 table, tokenizer, bm25 query builder | none |
| `reranker.py` | 114 | heuristic no-op / token-overlap reranker (no model) | `ranking` |
| `errors.py` | 109 | failure taxonomy and exit codes | `fingerprint` |
| `embed_cache.py` | 103 | content-addressed embedding cache (SQLite) | none |
| `sensitive.py` | 90 | secret-bearing file detection | none |
| `locks.py` | 53 | per-project `flock` | none |
| `logsetup.py` | 48 | logging configuration | none |
| `embed_text.py` | 45 | embedded-text construction; `EMBED_FORMAT` | `chunker` |
| `__init__.py` | 10 | re-exports the MCP entry point | `server` |
| `__main__.py` | 5 | `python -m code_indexer` | `server` |

**The three biggest, and what a CLI/core split would have to move.**

- `cli.py` (1,716 lines) is the largest file and is not thin: it owns output
  policy (which graph notes go to stdout versus stderr, `cli.py:319-335`),
  watch-daemon lifecycle and signal handling (`cli.py:1012-1037`), background
  spawning and pidfile confirmation (`cli.py:1258-1330`), and the process-wide
  `Core` cache (`cli.py:51-57`). A split would move: (a) core construction and
  caching into a factory shared with MCP; (b) the watcher lifecycle
  (`_select_watchable`, `_make_engine`, `_watch_background`, `_daemon_confirmed`,
  `_covers`, `_report_already_running`, `cli.py:1124-1330`) into `watcher.py`
  or a service layer — today a headless consumer must import `cli.py` to spawn
  a watcher; (c) the `format_*` renderers (`core.py:1064`, `:1324`,
  `graph.render_tree`) out of the core, since they are presentation.
- `core.py` (1,358 lines) is the real API but a god-facade: `__init__`
  constructs five collaborators eagerly (`core.py:98-112`), including the
  embedder and the Qdrant store. A core that does not require those services
  cannot reuse this constructor as-is.
- `manifest.py` (642 lines) is self-contained apart from `fts` and is the one
  module every read surface depends on. It would need no change for a
  "no services" mode: its reads are pure SQLite.
- Hidden coupling worth naming: `__init__.py:8` does `from .server import
  main`, so **importing the package imports the MCP server**, which imports
  `mcp.server.fastmcp`. That is why the measured startup bill in section 4
  includes `mcp`.

## 3. Index schema inventory

Versions: `SCHEMA_VERSION = "6"` (`manifest.py:41`), `EXTRACTION_VERSION = 4`
(`graph_extract.py:95`), `CHUNKER_VERSION = 1` (`ts_chunker.py:27`),
`EMBED_FORMAT = "contextual-header-v1"` (`embed_text.py:31`).

DDL as declared (`manifest.py:43-91`):

```sql
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    size INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok'
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS symbols (
    file TEXT NOT NULL,
    name TEXT NOT NULL,
    symbol_type TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'regex',
    PRIMARY KEY (file, name, start_line)
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_file_start ON symbols(file, start_line);
CREATE TABLE IF NOT EXISTS imports (
    file TEXT NOT NULL,
    raw_target TEXT NOT NULL,
    resolved_file TEXT,          -- best-effort project-relative resolution
    kind TEXT NOT NULL,          -- import|include|require|use|...
    line INTEGER NOT NULL,
    PRIMARY KEY (file, raw_target, line)
);
CREATE TABLE IF NOT EXISTS refs (
    file TEXT NOT NULL,
    line INTEGER NOT NULL,
    from_symbol TEXT,            -- NULL when the reference is module-level
    to_name TEXT NOT NULL,
    relationship TEXT NOT NULL,  -- call|type|base_class|member|includes|identifier
    confidence TEXT NOT NULL,    -- ast | heuristic
    receiver TEXT,               -- member-access receiver as written, else NULL
    PRIMARY KEY (file, line, to_name, relationship)
);
CREATE INDEX IF NOT EXISTS idx_refs_to_name ON refs(to_name);
CREATE TABLE IF NOT EXISTS index_errors (
    file TEXT NOT NULL,
    chunk_index INTEGER,
    error TEXT NOT NULL,
    at REAL NOT NULL,
    PRIMARY KEY (file, chunk_index)
);
```

Columns added by ALTER on open: `symbols.signature`, `symbols.visibility`
(`manifest.py:157-166`); `files.mtime_ns`, `files.inode` (`:170-177`);
`files.loc`, `files.language` (`:178-183`); `refs.receiver` (`:190-195`); and
`DROP TABLE IF EXISTS symbol_refs` (`:196`).

The FTS table is created lazily, not in `_SCHEMA` — `fts.py:79-83`:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content, symbol, path, tokenize='unicode61')
```

### Live manifest as measured

`<index-root>/code-indexer/manifest.db`: 21,557,248 bytes, `journal_mode=wal`.
Objects: `files`, `meta`, `symbols`, `imports`, `refs`, `index_errors`,
`chunks_fts` plus its five FTS5 shadow tables, and the three declared indexes
`idx_symbols_name`, `idx_symbols_file_start`, `idx_refs_to_name`.

Row counts: `files` 445, `symbols` 1,733, `refs` 113,751, `imports` 1,406,
`index_errors` 0, `chunks_fts` 2,198, `meta` 11.

`meta` keys present: `branch`, `chunk_hashes`, `chunker_version`, `embed_dim`,
`embed_format`, `embed_model`, `embed_text_version`, `extraction_version`,
`last_full_scan`, `last_indexed`, `schema_version`. Values: model
`qwen3-embedding:8b`, dim `4096`, `embed_text_version` and `embed_format` both
`contextual-header-v1`, `chunker_version` `1`, `extraction_version` `4`,
`schema_version` `6`. Note that `chunk_hashes` is a serialized multi-line blob
stored in a single meta row.

Stored symbol rows by source: `ast` 1,589, `regex` 144. Reference rows by
`(relationship, confidence)`: `identifier/heuristic` 101,197, `call/ast`
8,698, `type/ast` 1,600, `call/heuristic` 1,331, `includes/ast` 902,
`base_class/ast` 15, `includes/heuristic` 8. Every `imports` row carries
`kind='import'` (1,406) and `resolved_file` is nullable and often NULL.

### What identity does a symbol carry today

**There is no stable symbol id.** A symbol is identified by the primary key
`(file, name, start_line)` (`manifest.py:62`), and reads project
`(file, name, symbol_type, start_line, end_line, source)` plus optional
`signature` / `visibility`. There is:

- no surrogate id and no content hash on the row;
- no fully-qualified name and no parent/container column;
- no language column on `symbols` (language lives on `files.language`);
- no parser or index version on the row (versions exist once per manifest, in
  `meta`);
- nothing that survives a line shift: the same declaration moved down one line
  is a different key.

Consequences visible in the DDL: `end_line` is not part of the key, so two
rows cannot differ only by end line, and a definition that starts on the same
line as another row with the same name (for example a decorator row and a
declaration row, which `core.py:58-74` explicitly merges for display) collides
by construction.

**A `refs` row carries no target identity** — only the bare name in `to_name`
(`manifest.py:78`). A reference therefore cannot be tied to a particular
definition: with two `run()` methods in a repository, `to_name='run'` is
ambiguous and unresolvable. What a row does carry: `confidence`
(`ast`|`heuristic`), `relationship`
(`call|type|base_class|member|includes|identifier`), `receiver` (the receiver
expression as written, or NULL), `from_symbol` (enclosing symbol, nullable),
and the file/line of the reference. There is no index-revision stamp on the
row and no stored resolved target.

## 4. Semantic/vector dependency map

Imports, module-level versus function-level (exhaustive `rg` over `src/`):

| Package | Where | Level | Line |
|---|---|---|---|
| `ollama` | `embedder.py` | module | `:20` |
| `ollama` | `doctor.py` | module | `:24` |
| `qdrant_client` (`QdrantClient`) | `store.py` | module | `:13` |
| `qdrant_client.models` (13 names) | `store.py` | module | `:14-25` |
| `qdrant_client.models.PointStruct` | `indexer.py` | function | `:403` |
| `numpy` | nowhere in `src/` | — | only the pin in `pyproject.toml` and a docstring mention (`ts_chunker.py:4`) |
| reranker / model packages | none — `reranker.py` is pure-Python token overlap, no model, no download | `reranker.py:1-19` | — |

`mcp` is imported at module level by `server.py:83-84`, and `server.py` is
imported by `code_indexer/__init__.py:8`, so **every** CLI invocation imports
`mcp` whether or not MCP is used.

Construction is eager even where the service is not needed: `Core.__init__`
(`core.py:98-112`) builds `Embedder` (which builds `ollama.Client`) and
`Store` (which builds `QdrantClient(url=..., timeout=60,
check_compatibility=False)`, `store.py:36-38`) on every command, including
`overview` and `changed-symbols`. Construction alone does not dial out — which
is why the manifest-only commands below stay at zero connections — but the
objects are always present.

### Measured: which commands survive with the services unreachable

Source: `runs/milestone0/offline-probe.json`. Every run passed
`--skip-stale-check` (so none could write). "exit" is the unroutable-address
pass (`http://127.0.0.1:1`); "conns" is the number of TCP connections actually
accepted by the probe sink in the second pass.

| Command | exit | conns | wall | Note |
|---|---|---|---|---|
| `overview` | 0 | 0 | 0.95 s | full answer |
| `skeleton --limit 3` | 0 | 0 | 0.92 s | full answer |
| `outline src/code_indexer/cli.py` | 0 | 0 | 0.95 s | full answer |
| `find-symbol Core` | 0 | 0 | 0.92 s | full answer |
| `find-definition Core` | 0 | 0 | 0.91 s | full answer |
| `find-references Core --limit 3` | 0 | 0 | 0.93 s | full answer |
| `find-callers Core --max-nodes 5` | 0 | 0 | 0.99 s | full answer |
| `find-callees Core --max-nodes 5` | 0 | 0 | 0.92 s | full answer |
| `deps src/code_indexer/core.py --max-nodes 5` | 0 | 0 | 0.96 s | full answer |
| `get-code-context ... --start-line 1 --end-line 3` | 0 | 0 | 0.92 s | full answer |
| `changed-symbols` | 0 | 0 | 0.96 s | prints `no changes` |
| `semantic-search ... --mode lexical` | 0 | 0 | 0.94 s | FTS5 bm25 only |
| `index-status` | 0 | 5 | 34.4 s | prints `fingerprint=unverified reason=embedder-unreachable` |
| `lookup-project` | 0 | 5 | 34.0 s | correct answer, but 34 s and 5 Ollama attempts |
| `list-projects` | timeout | 14 | >75 s | never returned while Qdrant was unreachable (harness cap 75 s) |
| `doctor` | 1 | 7 | 39.9 s | fails as designed |
| `semantic-search "..."` (default hybrid) | 0 | 10 | 67.0 s | **exit 0 with `error: search failed on every project (... Ollama embed failed after 5 retries)` on stdout** |
| `eval --k 1` | 1 | 10 | 67.2 s | fails |

Two results contradict the documented contract and should be read as such:

- `lookup-project` and `list-projects` are documented as registry-only,
  write-free surfaces (`server.py:130-136`, `:196-206`), yet both reach Ollama:
  they render `Core.status_summary` -> `Core.fingerprint_status` ->
  `Embedder.dimension()` (`core.py:369-383`; `embedder.py:87-100`). The claim
  "reads only the registry; never runs the staleness probe, so it cannot
  write" is true about *writes* and false about *service independence*.
- A semantically failed search exits **0** (both passes), because the
  per-project failure is folded into the answer string instead of raised.

### Import cost

`python -X importtime -m code_indexer.cli --help` (full log:
`runs/milestone0/importtime.txt`), cumulative microseconds at the top of the
tree:

| Import | Cumulative |
|---|---|
| `code_indexer.core` | 457 ms |
| `code_indexer.indexer` | 397 ms |
| `code_indexer.store` | 389 ms |
| `qdrant_client` | 389 ms (`qdrant_client.http` alone 282 ms) |
| `code_indexer` (package `__init__`) | 314 ms |
| `code_indexer.server` | 314 ms (`mcp` 284 ms) |

Wall time of `python -m code_indexer.cli --help`, 5 runs: median **0.931 s**
(min 0.924 s, max 0.992 s) — `runs/milestone0/import-wall.txt`. Roughly a
third of that is `qdrant_client` and a third is `mcp`, neither of which any
manifest-only command needs.

### Can a repository be indexed today with vectors off?

**No. Embedding is mandatory for every index pass, and so is Qdrant.**

Static: `indexer.index_project` cannot start without both — `indexer.py:105-106`:

```python
if not self.store.collection_exists(collection):
    self.store.create_collection(collection, self.embedder.dimension())
```

It asks Qdrant first and then asks the embedder for its dimension (a live
Ollama request, `embedder.py:87-100`) before a single file is scanned. No
configuration key, flag or environment variable disables embedding: the
config surface carries `EMBED_MODEL`, `EMBED_BATCH`, `EMBED_CACHE`,
`EMBED_CONCURRENCY`, `EMBED_CACHE_MAX_GB`, `QUERY_INSTRUCTION`, and nothing
resembling "vectors off" (`config.py`; `SKILL-CLI.md` Key rules).

Measured, on a throwaway index root and a two-file throwaway project (nothing
touched the live index): `code-indexer add-project` with `QDRANT_URL` and
`OLLAMA_URL` at an unroutable address exits **1** with
`InternalError: <slug>: indexing error: [Errno 111] Connection refused`, and
`index-status` then reports `state=never-indexed` with no completed pass.
Transcript: `runs/milestone0/index-without-vectors.txt`.

The one genuinely offline *query* path that exists is
`semantic-search --mode lexical` (0 connections, 0.94 s), which reads
`chunks_fts` — a table that can only be populated by a pass which required
both services.

## 5. Test inventory

At tip `ef2be78`, `uv run pytest tests/ -q -W error`:

```
564 passed, 2 deselected in 82.93s
```

(`runs/milestone0/pytest.txt`). Collection: 566 tests in 48 modules, 2
deselected by the default `-m 'not live'` (`runs/milestone0/collect.txt`).

Seam usage is per-module, not shared: **19 test modules define their own
`StubEmbedder` / `StubStore` (or `FakeEmbedder` / `FakeStore`) class** and
inject it into `Core`; `tests/conftest.py` holds only the `live` marker
(`conftest.py:15`), no fixture and no shared stub. Modules that define a seam
class: `test_changed_symbols`, `test_codeintel`, `test_concurrency`,
`test_csharp_references`, `test_csharp_symbols`, `test_doctor`,
`test_embed_cost`, `test_embed_format`, `test_evalharness`, `test_fingerprint`,
`test_fts_hybrid`, `test_graph`, `test_indexer_embed_context`,
`test_langmatrix`, `test_lifecycle_selector`, `test_orphan_sweep`,
`test_output_contract`, `test_overview`, `test_project_state`,
`test_result_shaping`, `test_scoped_indexing`, `test_search_data_files`,
`test_search_isolation`, `test_sensitive_exclusion`,
`test_stale_safe_context`, `test_staleness_reporting`, `test_subcommands`,
`test_symbol_resolution`, `test_unwatch`, `test_watch`, `test_watcher`.

Needs a live service: exactly one module — `tests/test_live_backends.py`
(2 tests, both `pytest.mark.live`, `test_live_backends.py:34`), deselected in
the default run. No other module is marked live.

Coverage of the surfaces this overhaul touches (collected test counts,
`runs/milestone0/collect.txt`):

| Surface | Modules (collected tests) |
|---|---|
| Graph (callers / callees / deps / refs) | `test_graph` (45), `test_reference_surfaces` (11), `test_manifest_store_reads` (8), `test_csharp_references` (7), `test_staleness_reporting` (7) |
| FTS / hybrid retrieval | `test_fts_hybrid` (9), `test_ranking` (16), `test_stale_safe_context` (9), `test_search_isolation` (6), `test_search_data_files` (5), `test_evalharness` (14) |
| Changed symbols | `test_changed_symbols` (14) |
| CLI contract | `test_output_contract` (20), `test_flag_contract` (7), `test_subcommands` (24), `test_lifecycle_selector` (27), `test_docs` (13, docs-to-registry drift) |
| MCP | `test_mcp_server` (40), `test_mcp_stdio_handshake` (2) |
| Manifest / schema | `test_units` (6), `test_codeintel` (10), `test_csharp_symbols` (5), `test_fingerprint` (11) |
| Staleness / lifecycle | `test_project_state` (8), `test_query_staleness` (4), `test_concurrency` (6), `test_unwatch` (16), `test_watch` (9), `test_watcher` (21) |

No test covers vector-independent indexing, an edit plan, a diff preview, or
any LSP path — none of those exist.

## 6. MCP surface assessment (measured)

Dumped by importing the module and serializing its tool list
(`runs/milestone0/mcp-tools.json`, from `dump_mcp_tools.py`):

- **18 tools**, 22,491 bytes of serialized `{name, description, inputSchema}`
  JSON, approximately **5,622 tokens** at 4 bytes per token. Descriptions
  alone are 9,587 bytes; input schemas 7,620 bytes.
- Largest descriptions: `semantic_search` 1,514 bytes (13 parameters),
  `get_code_context` 825, `add_project` 753, `changed_symbols` 704.
- `server.py:34` says "all 14 tools" — the module docstring is stale by four.

Exposed (18): `add_project`, `changed_symbols`, `deps`, `find_callees`,
`find_callers`, `find_definition`, `find_references`, `find_symbol`,
`find_symbols`, `get_code_context`, `index_status`, `list_projects`,
`lookup_project`, `outline`, `reindex_project`, `remove_project`,
`semantic_search`, `skeleton`.

Not exposed (9 commands and both aliases): `overview`, `index-more`,
`sweep-orphans`, `doctor`, `watch`, `unwatch`, `eval`, `eval-compare`, `map`,
`file-outline`. `overview` is the notable omission: it is the first command in
the CLI decision tree and has no MCP equivalent.

Logic in `server.py` beyond argv building and relaying:

- CLI resolution — `CODE_INDEXER_BIN` -> `shutil.which("code-indexer")` ->
  `sys.executable -m code_indexer.cli` (`server.py:92-102`).
- Output merging — `stdout + stderr` are concatenated, stripped, and a nonzero
  exit with empty output is replaced by a synthesized message
  (`server.py:105-118`). The CLI's stream distinction and exit code are lost
  at this boundary.
- Annotation policy — every tool declares all four MCP hints explicitly
  (`server.py:41-60`), and several hint values are justified by prose about
  CLI behaviour rather than by the CLI's own contract.

No registry, manifest or store access happens in `server.py`; the isolation is
real, and `tests/test_mcp_server.py:132`
(`test_server_is_thread_free_and_core_free`) enforces it.

Do household agents get told to call the MCP tools rather than the CLI?
Evidence only: the installed household skill at
`~/.hermes/skills/code-indexer/SKILL.md` is byte-identical to the repo's
`SKILL-CLI.md` (sha256 `7158c0a2e17376a77c0cb41ff3c9d3e99d72cef36feec6cccb33e87f805ec3da`,
613 lines each) and mentions MCP four times, every one of them a "this
command has no MCP tool" note (`SKILL-CLI.md:429, 442, 518, 520`). Its
decision tree (`SKILL-CLI.md:14-45`) is entirely CLI. `README.md` mentions MCP
19 times, including the parity table and the client-config block.

## 7. Skill / help surface

| Surface | Lines | Bytes | Est. tokens (/4) | Headings |
|---|---|---|---|---|
| `SKILL-CLI.md` (installed as the household skill, byte-identical) | 613 | 39,797 | 9,949 | 20 |
| `README.md` | 270 | 13,372 | 3,343 | 17 |
| `AGENT.md` | 73 | 4,283 | 1,070 | 5 |

`SKILL-CLI.md` section headings: When to use; Decision tree (agent workflow);
Automatic indexing (watch daemon); Commands; Subcommand reference; Token
reduction (schema v3); watch details; Project selection; Store reads (no
silent caps); Key rules; Gotchas — the "Commands" section also carries nine
in-block labels (registry, orientation, lookup, reading, search, edits,
lifecycle, dev utilities). `README.md` headings: Installation (CLI); Quick
start; Architecture; CLI to MCP parity; Index states; How indexing /
staleness works; Schema history; Configuration; Platform support;
Troubleshooting; Security / privacy; Limitations; Performance; MCP client
config; Development; Upgrading between versions.

CLI help size (`runs/milestone0/help-sizes.txt`):

- top-level `code-indexer --help`: **19,709 bytes**, 226 lines, ~4,927 tokens;
- all 27 per-command `--help` pages summed: **79,084 bytes**;
- largest per-command pages: `watch` 7,376 B, `semantic-search` 6,473 B,
  `find-references` 3,980 B, `index-status` 3,978 B, `find-callers` 3,632 B;
- largest docstrings-as-help: `watch` 3,516 B, `find-callers` 1,526 B,
  `find-references` 1,403 B, `index-status` 1,199 B; smallest are
  `find-definition` 56 B and `outline`/`file-outline` 71 B.

One command's help page already costs more than the whole `AGENT.md`
(`watch` 7,376 B versus 4,283 B).

## 8. Gap matrix

States: **present**, **partial** (exists with a named limitation), **absent**.
"Proposal assumption not true here" names what the proposal presumes that the
measurement contradicts.

| Capability | State | Evidence | Proposal assumption not true here |
|---|---|---|---|
| Repository overview and discovery (responsibility matrix: code-indexer owns) | present | `overview`, `skeleton`, `overview.py` | none |
| AST parsing and symbol extraction | present, limited kinds | `ts_chunker.py:65-66` maps method/function/class/struct/enum/interface/namespace; properties and fields have no row | §5.1 asks for fields, properties, constants, operators "where useful" — not extracted today |
| Symbol identity, stable and disambiguating | **absent** | PK `(file, name, start_line)`, `manifest.py:62`; no id, qualified name, container, language or version on the row | §5.2's identity list; identity does not survive a line shift here |
| Ambiguity handling in lookup | partial | `find-symbol` returns every exact-name row (`cli.py:1438-1445`); `definitions_of` is uncapped (`manifest.py:330-343`) — ambiguous names are *listed*, never *resolved*; graph walks match the trailing name (`graph.py:188-204`) | §5.2 wants candidates with disambiguating metadata; the only metadata is file, line, kind, signature |
| Lexical search | present | FTS5 `chunks_fts` (`fts.py:79-83`); `semantic-search --mode lexical`, measured 0.94 s offline | §5.4 treats lexical search as core; today it is a *mode of the semantic command*, not a command |
| Structural search (kind, name pattern, container, visibility, base type, has-call) | **absent** | the only filters are `--type/--symbol-type`, `--file`, `--substring`, `--limit` on `find-symbol` (`cli.py:1406-1425`); no `--inherits`, no `--has-call`, no name wildcard | §5.4's example CLI does not exist; `base_class` rows *are* stored (15 on the live index) but cannot be queried structurally |
| Definition and reference navigation | present, name-keyed | `find-definition`, `find-references`; `refs.to_name` is a bare string (`manifest.py:78`) | §5.3 wants target symbol identity on each relationship; there is none |
| Call and dependency graph | present, textual and unbound | `graph.py` BFS with caps and honest truncation notes; `imports` for `deps` | §5.3's "resolution method/backend, index revision" columns do not exist — only `confidence ast\|heuristic` |
| Provenance and confidence on relationships | partial | `refs.confidence` (`ast`/`heuristic`), `relationship`, `receiver` (`manifest.py:79-81`); live mix: 101,197 heuristic identifier rows versus 8,698 ast call rows | §5.1 requires separating observed syntax from inference; that happens per whole file, not per relationship |
| Focused context retrieval | present, one symbol | `get-code-context` (range or `--symbol`, `--context-lines`, `stale`) | §5.5 asks for parent container, references, callers/callees, related types, candidate tests; only source lines are returned |
| Incremental indexing and invalidation | present | content-hash diff plus stat fast-path (`scanner.py`, `indexer.py`), per-project flock (`locks.py`), swap rebuild (`core.py:435-452`) | §5.6's added/modified/removed/moved/unmatched symbol reporting is not met |
| Changed-symbol detection | present, file-classified | `changed-symbols`; kinds added/modified/removed/renamed (`changed.py:29-63`); removed symbols listed by name and line only (`changed.py:196-201`) | §5.6's "moved or could not be matched confidently" states do not exist; `--base/--head/--staged` do satisfy the explicit-base requirement |
| Edit-plan generation and validation | **absent** | `rg -i "edit_plan\|editplan\|plan_id\|rename_symbol\|apply_plan\|refactor"` over `src/` returns nothing | §6.3 and §6.4 assume a plan model to extend; nothing exists |
| Preview and diff generation | **absent** | same search; the only diffs in the codebase are git's, read by `changed.py` | §6.5's PREVIEW step does not exist |
| Structural edits (replace body, insert/delete declaration) | **absent** | same search | §6.3 P0 scope |
| Semantic refactorings via LSP/compiler | **absent** | `rg -i "lsp\|clangd\|pyright\|roslyn\|omnisharp\|gopls"` over `src/` returns nothing | §6.2 and §7 assume a provider interface; none exists |
| Refactoring provider interface | **absent** | same | §6.2 |
| Preview-before-mutation, stale refusal, overlap detection, optimistic concurrency at the code layer | **absent** here; **present** in filekeeper | `keeper 0.1.8`: `--expected-hash`, `--dry-run`, `file apply-patch`, `replace-lines`, `insert-lines`, `delete-lines` (section 9) | §6.6 items 2, 4, 9, 11, 12 presuppose a mutation path in this tool; the mutation path belongs to filekeeper, which this tool never calls |
| CLI-first with JSON output | partial | 14 of 27 registered commands accept `--json` or `--format` (12 distinct commands plus the `map` and `file-outline` aliases). The 13 that do not: `add-project`, `index-more`, `remove-project`, `reindex-project`, `list-projects`, `lookup-project`, `index-status`, `find-definition`, `find-references`, `watch`, `unwatch`, `eval`, `eval-compare` | §8.1's "`--json` output for machine consumption" is not universal |
| Predictable exit codes | partial | `errors.py` taxonomy, but a failed `semantic-search` exits 0 (measured) and the MCP layer discards the exit code | §8.1 |
| Small SKILL plus on-demand references | **absent** | one 613-line, 39,797-byte `SKILL-CLI.md`; the installed skill directory holds exactly one file, no `references/` | §8.2's reference layout does not exist |
| MCP as a thin adapter (§8.3) | present | 18 tools, argv and relay only, no store access (section 6) | §8.3 asks whether it earns its cost; the measurable costs are 22,491 B of schema, 284 ms on every CLI start, and 42 tests |
| Optional MCP and optional semantics | **absent** | `mcp`, `ollama`, `qdrant-client` are hard dependencies (`pyproject.toml:19-22`); `mcp` is imported at package import (`__init__.py:8`) | §4.2's "the default core install must not import, start, configure, or probe optional services" is false today |
| Core install without semantic extras | **absent** | same, plus indexing is impossible without both services (section 4) | §4.3 and Milestone 1's "a clean core installation can index and query a supported repository" cannot hold today |
| Build and test execution | external, documented | `AGENT.md` documents `uv run pytest`; the tool maps candidate tests only through `changed-symbols --impact` | — |

### Post-edit behaviour with only a new untracked document

Measured while this document was the only new (untracked) file in the tree
(`runs/milestone0/changed-symbols-new-doc.txt`):

- `changed-symbols` (no flags) prints `no changes`, exit 0 — an untracked file
  is invisible by default.
- `changed-symbols --include-untracked` groups `docs/overhaul-baseline-audit.md`
  under `other files` (the non-code bucket, alongside 106 further untracked
  entries in the abbreviated list) rather than reporting symbols, and it also
  parses the untracked `runs/` evidence as added code, producing 82 `added`
  symbol lines for throwaway scripts. So the flag reports drift faithfully but
  cannot distinguish evidence artifacts from source.

## 9. Feasibility facts (measured on this host, no installs)

Language servers: **none of the seven is installed.** `command -v` was run for
`clangd`, `pyright-langserver`, `pylsp`, `roslyn-language-server`, `OmniSharp`,
`csharp-ls` and `gopls`; every one resolved to nothing. The compilers and SDKs
are present (`g++`, `clang++`, .NET SDK 8), but no LSP server binary is.

`keeper` (the filekeeper CLI): **version 0.1.8**. From its own `--help`:

- `file write` accepts `--expected-hash` and `--dry-run`, plus
  `--i-accept-secret-write-risk`;
- `file apply-patch` exists ("apply a unified diff, recoverably across
  files");
- line-range edits exist: `file replace-lines`, `file insert-lines`,
  `file delete-lines` (1-based, inclusive);
- the documented exit-code contract includes 5 = concurrency conflict (stale
  hash, missing match, match-count mismatch), 7 = patch could not be applied
  with the pre-patch state intact, 8 = a failed patch could not be fully
  rolled back, 11 = symlink target outside the workspace, 12 = undo refused
  because the path changed after the operation.

So the precondition and line-range primitives an edit-plan adapter needs exist
and are documented; an all-or-nothing multi-file guarantee does **not** —
exit code 8 is the explicit admission. Whether `apply-patch` accepts a
per-file `--expected-hash` was not established from the top-level help text,
and would need `keeper file apply-patch --help`.

Installed console script state, read from the tool's `direct_url.json` (not
assumed):

```json
{"url":"file:///<repo>","dir_info":{"editable":true}}
```

with `code_indexer.pth` containing `<repo>/src`. The script at
`<home>/.local/bin/code-indexer` is a symlink into the uv tool environment,
and that environment's Python is CPython 3.12.12. Consequence: the installed
command runs this working tree, so audit and agent measurements agree without
reinstalling — and equally, an uncommitted edit in the checkout changes what
every agent runs.

An install of anything new (an LSP server, a Python/Roslyn refactoring
provider) would require `uv`-managed tooling or a system package: global
`pip` is blocked on this host (PEP 668). Nothing was installed for this audit.

## 10. Risks and sequencing observations

Several acceptance criteria in the proposal presuppose capabilities this code
base does not have, and several "new" capabilities already exist. Ordered by
how much it would cost to discover them late:

1. **Milestone 1's acceptance criterion is unreachable without changing the
   indexer, and the proposal does not say so.** "A clean core installation can
   index and query a supported repository" fails today at `indexer.py:105-106`:
   the first thing a pass does is ask Qdrant for a collection and the embedder
   for a dimension. Measured on a throwaway root: `add-project` offline exits
   1 and leaves `state=never-indexed`. Milestone 1 therefore contains a real
   data-model question (what a vector-less index stores per chunk), not a
   packaging exercise; sequencing it as "move imports behind a boundary"
   understates it.
2. **Milestone 1's "core tests pass without vector DB or embedding services"
   is already true at the test layer and false at the package layer.** All 564
   tests pass with stubs and make no network call. What is not true is that
   the package can be imported or a project indexed without the dependencies
   installed: `mcp`, `ollama` and `qdrant-client` are hard dependencies
   (`pyproject.toml:19-22`) and `mcp` is imported by `__init__.py:8`.
3. **Milestone 2 (structural search) is half-cheap and half-blocked.** Kind,
   file and substring filtering already exists on `find-symbol`, and the data
   for inheritance and call queries is already stored (`refs.relationship`
   carries `base_class` — 15 rows on the live index — and `call` — 8,698).
   What is missing is an identity to hang results on. A structural query
   language built before the identity model will be rewritten after it.
4. **Milestone 3 (edit-plan foundation) presumes selection primitives that do
   not exist.** A plan must name a range and prove it still matches. Ranges
   come from `symbols.start_line`/`end_line` under a key that includes
   `start_line` but not `end_line` (`manifest.py:62`), so a one-line shift
   changes the key. "This symbol id, still at this range" is not expressible
   yet.
5. **Milestone 6 (filekeeper adapter) is closer than it looks, with a weaker
   guarantee than the proposal implies.** `keeper 0.1.8` already provides
   `--expected-hash`, `--dry-run`, `apply-patch` and 1-based line-range edits
   with an undo journal. But exit code 8 ("a failed patch could not be fully
   rolled back") is the admission that multi-file application is not atomic,
   which is exactly the policy §6.6 item 6 asks code-indexer to define.
6. **Milestone 5 (LSP rename) has no server to talk to on this host.** None of
   `clangd`, `pylsp`, `pyright-langserver`, `roslyn-language-server`,
   `OmniSharp`, `csharp-ls`, `gopls` is installed. "Start with one language
   that has a reliable available server" is a server-provisioning milestone
   first.
7. **Milestone 7 (agent experience and the MCP decision) already has enough
   evidence, and the evidence is not tool-call logs.** The MCP surface costs
   22,491 bytes of schema (~5,622 tokens), 284 ms on every CLI start, and 42
   tests, while the household skill that agents actually load mentions MCP
   only as "no MCP tool" and routes every workflow through the CLI. Adherence
   measurements across the household are not available to this audit, so that
   part of the decision remains unmeasured — but the cost side is measured.
8. **Where the proposal proposes what already exists.** Repository overview
   (`overview`, `skeleton`), definition and reference navigation
   (`find-definition`, `find-references`), call and dependency graphs
   (`find-callers`, `find-callees`, `deps`), changed-symbol detection
   (`changed-symbols`), focused context retrieval (`get-code-context`),
   incremental indexing with content hashes and a fingerprint gate, FTS5
   lexical retrieval, confidence-labelled relationships, honest truncation
   reporting on graph walks, and a thin MCP adapter over the CLI all exist and
   are tested. Genuinely new work: a symbol identity model, structural query
   filters, a versioned edit-plan format with validation, an apply path
   through filekeeper, an LSP provider layer, and the core/extension
   separation in item 1.
9. **A live-index risk the proposal does not mention.** Manifest rows and the
   working tree can disagree, and `--skip-stale-check` is the only thing that
   stops a read from rewriting the index. `overview` demonstrated it during
   this audit: under `--skip-stale-check` it reported `cli.py` at 1,712 LOC
   and `core.py` at 1,626 LOC, while `wc -l` says 1,716 and 1,358
   (`runs/milestone0/overview.txt`). Without the flag the same command would
   have re-indexed first. Any future read-only mode must decide what a stale
   read returns.
10. **Output-contract defects that would otherwise be inherited.** A failed
    `semantic-search` exits 0 with an `error:` line on stdout (measured), and
    the MCP layer merges stdout and stderr and discards the exit code
    (`server.py:105-118`). No scripted consumer of either surface can tell
    "no hits" from "every backend failed".
11. **Untracked evidence is invisible to the post-edit loop.** `changed-symbols`
    without `--include-untracked` prints `no changes` for a new document, and
    with the flag it lumps documents into `other files` while parsing
    throwaway scripts in `runs/` as added code. A milestone that relies on
    changed-symbol detection to prove "only the intended files changed" will
    need a path filter it does not have today.

## 11. Questions for the decision leg

Each question is one the measurements make unavoidable; the evidence pointer
follows it.

1. **Does the core gain a symbol identity, or keep
   `(file, name, start_line)`?** Structural search and edit plans both hang on
   this, and today a one-line shift changes the key (`manifest.py:62`,
   section 3).
2. **What does a vector-less index store per chunk, and does Qdrant remain the
   chunk store or only the vector store?** A pass cannot begin without both
   services (`indexer.py:105-106`; `index-without-vectors.txt`). This is the
   real content of Milestone 1.
3. **Is `mcp` a hard dependency or an extra?** It is imported by
   `code_indexer/__init__.py:8`, costs 284 ms of every CLI start and 22,491
   bytes of schema, while the household skill routes agents to the CLI
   (sections 4 and 6).
4. **Are `ollama` and `qdrant-client` importable-but-optional, or must the
   package keep failing when they are absent?** Both are hard dependencies
   (`pyproject.toml:21-22`) and both are constructed eagerly in
   `Core.__init__` (`core.py:98-112`), including for `overview`, which needs
   neither.
5. **What may a read command do when the index is stale?** `overview` served
   445 files and 45,460 LOC from a manifest whose per-file `loc` disagreed with
   the tree, and nine read commands would silently re-index without
   `--skip-stale-check` (sections 1 and 10). The options differ in what they
   return, not only in cost.
6. **Does a failed `semantic-search` keep exiting 0?** If any plan, MCP or
   agent decision depends on knowing whether a query succeeded, this must be
   settled before that decision (measured: exit 0 with an error line).
7. **Which single language gets the first LSP provider, given that no LSP
   server is installed here?** (measured: none of the seven candidates
   resolve; section 9).
8. **Is the edit-plan apply path filekeeper-only, or must code-indexer also
   ship a direct-write adapter?** `keeper 0.1.8` covers hashes, dry-run and
   line ranges but not atomic multi-file application (exit code 8), and §6.7
   asks for both an adapter and a filesystem fallback (sections 9 and 10).
9. **Does structural search extend `find-symbol` or become a new command?**
   Kind, file and substring filtering already live there, and six other
   surfaces render symbol rows; a second query surface would duplicate their
   filters (`cli.py:1406-1478`).
10. **Is `overview` promoted to MCP parity, or deliberately left CLI-only?**
    It is the first command in the CLI decision tree and the only orientation
    surface with no MCP tool (section 6).



