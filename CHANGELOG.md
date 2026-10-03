# Changelog

## [Unreleased] — 2026-10-03 documentation repair round

Documentation-only corrections (no behavior changes); docs drift now guarded
by `tests/test_docs.py` (runs in CI via `pytest -m "not live"`).

- **SKILL-CLI.md** (agent-facing, 720d39c):
  - decision tree stated the wrong default ranking; the real default is
    `--ranking vector` (pure cosine); added a one-line recommendation to pass
    `--ranking hybrid` for exact-identifier queries; removed the
    "rerank/heuristic" wording that is not flag values.
  - command block de-duplicated (find-callers/find-callees/deps appeared
    twice with a dangling "see below" reference), grouped by lifecycle
    (registry → orientation → lookup → reading → search → edits → lifecycle),
    one entry per command with full options verified against `--help`.
  - previously undocumented flags now listed: `index-more`,
    `--include`/`--priority`, `--fresh` (plus a global-flags line),
    `--head`/`--include-untracked`/`--substring` (changed-symbols),
    `--mode`/`--rerank`/`--context-lines` (semantic-search),
    `--relationship` values, dev-only `eval`/`eval-compare`.
  - `--type` documented as an alias of `--symbol-type` on find-symbol.
  - the find-symbol-only `--name alias` section replaced by a
    project-selection section covering every project-scoped command.
  - canonical index-state list (idle/indexing/never-indexed/needs-reindex/
    error) with meanings and agent actions.
  - environment-variable list completed against the config module
    (EMBED_BATCH, UPSERT_BATCH, MAX_FILE_BYTES, CHUNK_MAX_CHARS,
    PARANOID_HASH, VERBOSE; EMBED_CACHE default ON) with placeholder-default
    warning for OLLAMA_URL/QDRANT_URL.
  - front matter: trigger-oriented description; version aligned with
    pyproject (0.1.0); unsourced "855 B vs 2.2 KB measured" claim removed.
- **README.md** (5690f46): rewritten as a short overview linked to
  SKILL-CLI.md; CLI↔MCP parity table (replacing a false 1:1 claim;
  overview/index-more/doctor/watch/unwatch/eval* labeled CLI-only);
  canonical state table; schema history (current schema v5); updated
  staleness/fingerprint/filtering text; new troubleshooting, platform
  support, security/privacy, limitations, and performance sections; MCP
  client config kept with the installed-tool variant and stdout note; stale
  "Python 3.11" claim removed; dev section rewritten with real test
  commands; drift-prone tool-count number removed (0896d0c).
- **pyproject.toml / metadata** (18c21f5): description unified between the
  GitHub About and pyproject; Python-support mismatch of issue #4 settled
  (requires-python `>=3.12,<3.13` since 0fd9604; README no longer claims
  3.11) — issue #4 closed.
- **tests/test_docs.py** (new): offline drift tests — command/option coverage
  against the typer/click registry, duplicate-command detection, env-var
  coverage (code ↔ docs, both directions), MCP parity-table check, Python
  version consistency (pyproject ↔ README ↔ .python-version), and
  link/anchor + dangling-"see below" checks.