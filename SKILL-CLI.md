---
name: code-indexer
description: "Use for shell code search: code-indexer CLI, one-shot."
version: 1.0.0
---

# code-indexer (CLI)

One-shot typer CLI over `code_indexer.core`. Repo:
`/home/keum/dev/athena/code-indexer/`. Binary: `code-indexer`; package
`code_indexer`. CLI-only — no MCP variant exists. LAN-local; no server RPC.

## When to use

- All semantic code navigation against registered repos: search, symbols,
  refs, line-ranged source context — from shell, scripts, or in-conversation.

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
code-indexer watch /path/repo [--duration 300] [--background]   # optional inotify re-index daemon
code-indexer watch --all                  # watch every registered project
```

## watch details

- `--duration T` bounds its life: exits 0 after T seconds (`0`/omitted =
  forever; honored by `--background` too). SIGINT/SIGTERM exit 0.
- Degradation: no watchdog installed or inotify watch-descriptor
  exhaustion -> quiet-period polling fallback (hash scan per project per
  quiet tick). Correctness is never lost, only latency.
- `--foreground` (default) is the plain inherited-stdio behavior.
- Projects: repeatable path/slug/name args, or `--all` (round-robin).
  Never pass both.
- A second watcher is refused while a live one holds the PID-file flock.

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
  (`watch` quiet period, default 3 s; alias `WATCH_QUIET_PERIOD`),
  `WATCH_SWEEP_INTERVAL` (periodic full sweep, default 300 s).

## Gotchas

- `remove-project` deletes the whole index; re-adding re-indexes from scratch.
- Existing `idx_*` collections are reused if the registry is retained; a fresh
  registry re-add re-attaches to the same collection and re-indexes (chunk-hash
  diff means only changed chunks are re-embedded; deterministic uuid5 point IDs
  make re-upserts idempotent).
- `.gitignore`/`.codeindexignore` are honored; files >1 MB and binaries skipped.
- Branch switches re-index correctly (content-hash diff, mtime ignored).
