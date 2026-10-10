# Changelog

## [Unreleased] — agent-report round (issues #5, #6, #7)

Three reported defects, fixed at the query/manifest seam; no index rebuild is
needed (all three are query-time or listing-time changes).

- **whole-project search no longer dies on one unresolvable project**
  (c5341bb, issue #6). One registered project whose collection is gone
  aborted every project-less `semantic-search` with a single trailing error
  line and zero hits. Per-project failures are now isolated: the project is
  skipped with a loud warning naming it, the remaining projects' hits are
  returned, and the run reports the skipped count. If no project answers the
  run still errors. The fingerprint (`needs-reindex`) gate stays fatal and
  deliberately sits outside the skip path — a wrong-model index answers wrong
  silently. `list-projects` fetches ONE collection listing (physical
  collections + aliases) and reports a project that has completed a pass but
  whose collection is gone as `state=error collection=missing` instead of a
  healthy `idle`. The live end-to-end test now deregisters what it registers
  (it leaked one registration per live run).
- **`--skip-stale-check` is accepted everywhere, as documented** (efebc91,
  issue #7 second defect). `find-callers`, `find-callees`, `deps`,
  `overview` and `changed-symbols` did not accept it while SKILL-CLI.md
  claimed every command does; all five now take it and route it into the
  Core, and `eval-compare` accepts it as a no-op so the claim is literally
  true. SKILL-CLI.md states exactly where the flag changes behavior.
- **`find-references` and `find-callers` stop under-reporting** (issues #5,
  #7 main claim). `find-references` now reads the UNION of both reference
  stores (`symbol_refs`: invocation targets `calls`/`includes`;
  `refs`: AST `call`/`type`/`base_class` plus the textual `identifier`
  fallback for languages without an AST query, C# included), labelling every
  row with the store and confidence it carries, and a bare "no references" is
  printed only when both stores are empty. `find-callers` accepts a dotted
  `Type.Member` subject (qualifier resolved against the symbol rows, fan-in
  matched on the trailing member name, receiver reported per hit), and when
  the subject has no definition row (properties/fields are not extracted as
  symbols) it seeds from the `refs` rows and says so on `note:` lines instead
  of printing a bare `no results`. SKILL-CLI.md and the command docstrings
  document which store answers what and that the graph is textual/unbound.
  Follow-up recommended but NOT done here: a tree-sitter C# references query
  and/or unifying the two reference tables (see the round report).

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