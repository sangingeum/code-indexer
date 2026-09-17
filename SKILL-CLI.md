---
name: code-indexer
description: "Use for shell code search: code-indexer CLI, one-shot."
version: 1.0.0
---

# code-indexer (CLI)

One-shot typer CLI over `code_indexer.core` (same core as the
`code-indexer-mcp` MCP server — see the `code-indexer-mcp` skill for the
agent/MCP surface). Repo: `/home/keum/dev/athena/code-indexer/`. Binary:
`code-indexer`; package `code_indexer`. LAN-local; no daemon, no server RPC.

## When to use

- Shell, cron jobs, or scripts that need semantic code navigation against
  registered repos: search, symbols, refs, line-ranged source context.
- For in-conversation agent tool calls, prefer the MCP tools
  (`code-indexer-mcp` skill) — same operations.

## Commands (each accepts --skip-stale-check)

```bash
cd /home/keum/dev/athena/code-indexer && uv run code-indexer <cmd>   # or installed console script
code-indexer add-project /path/repo [--name myproj]   # register + initial index (foreground in CLI)
code-indexer lookup-project /path/repo                # registration check, no side effects
code-indexer list-projects
code-indexer semantic-search "query" [--project P] [--limit 8] [--file-filter '*.py'] [--json]
code-indexer find-symbol Name [--project P] [--symbol-type class]   # exact-first, capped substring fallback
code-indexer find-definition Name [--project P]                      # exact match only
code-indexer find-references Name [--project P] [--relationship calls]  # all confidence=heuristic
code-indexer get-code-context src/f.hpp --start-line 40 --end-line 80   # or --symbol Foo::bar
code-indexer index-status /path/repo
code-indexer reindex-project /path/repo   # full rebuild, foreground
code-indexer remove-project /path/repo    # DESTRUCTIVE: drops collection + manifest + registry entry
code-indexer watch /path/repo [--duration 300]   # optional poll-loop re-index daemon
code-indexer watch --all                  # watch every registered project
```

## watch (optional daemon, never required)

`code-indexer watch` is a long-lived poll-loop watcher: each tick it takes
the per-project flock and runs the same staleness probe / incremental pass
the one-shot commands use, then sleeps `WATCH_DEBOUNCE` seconds (default 3).
Polling, not inotify — the content-hash diff makes an unchanged tick cheap
(hash scan only; no embedding, no Qdrant traffic when nothing changed).

- `--duration T` bounds its life: exits 0 after T seconds. `0` or omitted =
  run forever. SIGINT/SIGTERM exit 0 and release the lock (kernel flock).
- Projects: repeatable path/slug/name args, or `--all` (every registered
  project, round-robin one pass per project per tick). Never pass both.
- Opt-in only: one-shot commands work identically without any watcher; the
  watcher never registers anything and is never on the default path.

## --name alias

`find-symbol`, `find-definition`, `find-references`, `get-code-context`
accept BOTH `--project X` and `--name X` for the project argument (path,
slug, or registered custom name) — typer dual option names.

## Key rules

- `--skip-stale-check` skips the staleness probe / incremental pass (fast,
  may serve slightly stale results). Default behavior: search/status trigger
  a re-scan if STALE_TTL (60 s) elapsed; concurrent invocations serialize via
  a per-project flock — exactly one index pass runs, others print
  "<slug>: indexing in progress".
- Workflow: `semantic-search` -> `find-symbol`/`find-definition` ->
  `get-code-context --symbol`. Never read whole files.
- First index of a big repo is slow (~1.2 docs/s GPU-hosted); check
  `index-status` before trusting thin results.
- Never manage index state yourself (manifests, chunk hashes, collections) —
  the tool owns them.
- Env: `OLLAMA_URL`, `QDRANT_URL`, `EMBED_MODEL`, `INDEX_ROOT`
  (default `~/.code-indexer`), `STALE_TTL`, `WATCH_DEBOUNCE`
  (`watch` poll tick, default 3 s).

## Gotchas

- `remove-project` deletes the whole index; re-adding re-indexes from scratch.
- Existing `idx_*` collections are reused if the registry is retained; a fresh
  registry re-add re-attaches to the same collection and re-indexes (chunk-hash
  diff means only changed chunks are re-embedded; deterministic uuid5 point IDs
  make re-upserts idempotent).
- `.gitignore`/`.codeindexignore` are honored; files >1 MB and binaries skipped.
- Branch switches re-index correctly (content-hash diff, mtime ignored).
