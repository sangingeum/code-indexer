# code-indexer — AGENT.md

Semantic code index over Ollama embeddings + Qdrant, exposed as a one-shot CLI
(`code-indexer`, typer). Python 3.11, uv-only (PEP 668: never global pip).

## Build / test / run

- Test: `uv run pytest tests/ -q -W error` (warn-clean is the house bar).
- Lint: `uvx ruff check src tests scripts`.
- Publishing hygiene: `uv run python scripts/publishing_hygiene_lint.py` — the
  mechanical form of the publishing rules. It scans every tracked path
  (`git ls-files`; untracked local evidence is out of scope by design) and
  reports `path:line: class: detail` per violation, then
  `scanned=/skipped=/violations=`. Three classes: `absolute-host-path` (a
  machine path is never publishable, capture data included; a short allow-list
  of synthetic fixture accounts is exempt, and so is pattern text — a segment
  beginning with `[`, `{` or `<`, the last because angle brackets are this
  house's published spelling for host-specific text), and — in authored
  surfaces only, meaning every tracked file **except** capture data —
  `internal-profile-name` (held as sha256 digests; `--hash-name NAME` prints one
  to add) and `board-card-id`. Capture data is a file under `runs/` with a
  `.json`, `.txt`, `.log`, `.err`, `.out` or `.jsonl` suffix: it keeps its
  payload because it is quoted evidence about another repository. A record
  `.md` under `runs/` is therefore authored and governed. `--paths P…` scans
  explicit paths. Offline test: `tests/test_publishing_hygiene.py`.
- Pre-commit hook: `.githooks/pre-commit` runs the same check over the staged
  files and blocks the commit on a violation. Activate it in a clone with
  `git config core.hooksPath .githooks`. It is the opt-in local early warning,
  not the enforcement: CI enforces the rule through the offline suite
  (`tests/test_publishing_hygiene.py`), and the hook's plain `python3` call is
  valid only while the lint stays stdlib.
- Run from source: `uv run code-indexer <command>`.
- Installed CLI on this host: `~/.local/bin/code-indexer` (uv tool; reinstall
  with `uv tool install --force .` after merged changes to test live).
- Watchdog is a **main dependency** (moved out of the former opt-in `watch`
  dependency-group: its polling fallback was a silent CPU hog). Only the
  `watch` subcommand imports it (lazy import, fallback kept for robustness).
- Python pinned to 3.12 (`.python-version` + `requires-python`) so uv.lock
  resolves a single branch (no per-Python qdrant-client/numpy forks).

## Layout

- `src/code_indexer/` — single package: `cli.py` (typer app), `core.py`
  (Core facade), `config.py`, `indexer.py`, `embedder.py`, `store.py`
  (Qdrant), `registry.py`, `manifest.py`, `locks.py` (per-project flock),
  `watcher.py` (watch daemon + PidFileLock), `server.py` (MCP — thin wrapper
  over the CLI, no logic of its own).
- `tests/` — pytest; embedder/store stubbed at the Core seam, no network.

## Architecture principles

- One-shot CLI, no daemon required; `watch` is the optional freshness daemon
  and `unwatch` stops it (both take the same flock-guarded pidfile).
- The MCP server is a **thin wrapper over the CLI**, not a second
  implementation: each tool runs the matching `code-indexer` subcommand as a
  subprocess and relays its output (same code path, same foreground
  semantics, no hidden threads).
- Single-instance gates are kernel flocks (pidfile flock for the watcher,
  per-project flock for index passes) — pid/lockfile content is advisory.
- Self-write suppression: the watcher never indexes its own state root.
- Graceful degradation everywhere: no watchdog → polling; inotify exhaustion
  → polling; no Qdrant → clear error, never a crash.

## Conventions

- Modern 3.11 annotations, pathlib, type hints on public functions.
- The daemon-side flock is the authoritative single-instance gate; parent-side
  checks are advisory heuristics (see `watcher.probe_watcher_holder`).
