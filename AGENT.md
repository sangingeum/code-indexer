# code-indexer — AGENT.md

Semantic code index over Ollama embeddings + Qdrant, exposed as a one-shot CLI
(`code-indexer`, typer). Python 3.11, uv-only (PEP 668: never global pip).

## Build / test / run

- Test: `uv run pytest tests/ -q -W error` (warn-clean is the house bar).
- Run from source: `uv run code-indexer <command>`.
- Installed CLI on this host: `~/.local/bin/code-indexer` (uv tool; reinstall
  with `uv tool install --force .` after merged changes to test live).
- Watchdog is an opt-in dependency-group (`uv sync --group watch`); only the
  `watch` subcommand imports it (lazy import + polling fallback).

## Layout

- `src/code_indexer/` — single package: `cli.py` (typer app), `core.py`
  (Core facade), `config.py`, `indexer.py`, `embedder.py`, `store.py`
  (Qdrant), `registry.py`, `manifest.py`, `locks.py` (per-project flock),
  `watcher.py` (watch daemon + PidFileLock), `server.py` (MCP).
- `tests/` — pytest; embedder/store stubbed at the Core seam, no network.

## Architecture principles

- One-shot CLI, no daemon required; `watch` is the optional freshness daemon.
- Single-instance gates are kernel flocks (pidfile flock for the watcher,
  per-project flock for index passes) — pid/lockfile content is advisory.
- Self-write suppression: the watcher never indexes its own state root.
- Graceful degradation everywhere: no watchdog → polling; inotify exhaustion
  → polling; no Qdrant → clear error, never a crash.

## Conventions

- Modern 3.11 annotations, pathlib, type hints on public functions.
- The daemon-side flock is the authoritative single-instance gate; parent-side
  checks are advisory heuristics (see `watcher.probe_watcher_holder`).
