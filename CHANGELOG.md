# Changelog

## [Unreleased] — reference-store coverage and precision (#9)

The two load-bearing coverage gaps the store unification left behind are
closed — a `calls` filter answers on a language with no AST query, and
`includes` is populated for every AST language, not just C/C++ — plus the C#
member-access precision pass and the CLI honesty fix. **This round needs a
rebuild**: `EXTRACTION_VERSION` is `3`, so every existing index reports
`needs-reindex` and refuses queries until `reindex-project` re-extracts the
new rows.

- **textual `call` coverage.** The textual scan (every language with no AST
  query, markdown included) now stores a `call` row for an identifier followed
  immediately by `(`. Measured over the live indexes, that adds 20,157 rows
  (13,338 of them `.csx` script call sites); allowing whitespace before the
  paren would add 89,882 more that are overwhelmingly prose (`commit(s)`,
  `function (see below)` — 79,551 of them markdown), so the tight form is the
  documented rule. One occurrence yields one row: a tight `name(` is a `call`,
  any other identifier an `identifier`. Rows stay `confidence='heuristic'` —
  a `name(` in a string or trailing comment still matches.

## [Unreleased] — one reference store, a C# AST query, precision pass (#8)

The two reference tables became one, C# stopped being textual-only, and the
precision debt issue #8 recorded was paid. **This round needs a rebuild**:
`EXTRACTION_VERSION` is bumped, so every existing index reports
`needs-reindex` and refuses queries until `reindex-project` re-extracts the
unified rows.

- **one reference store, one writer (schema v6).** `symbol_refs` is gone;
  `refs` is the single reference table with a single writer (`graph_extract`),
  one row shape and one honest `relationship`/`confidence` per row, plus a
  `receiver` column (the member-access receiver as written, plain identifier
  or NULL). A call is stored once, so the union's duplicate-line symptom
  dissolves. Relationship vocabulary: `call`|`type`|`base_class`|`member`|
  `includes`|`identifier`. Rows are written at the commit phase, after the
  file's vectors, so a crash never claims references that are not there.
  `find-references` reads the one store and labels each row with the
  confidence it carries; `--relationship` still spans
  `calls`|`includes`|`inherits`|`references`.
- **a tree-sitter C# references query** (`queries/csharp/references.scm`).
  C# joins the AST languages, so a property/field READ through a member
  access (`a.OughtRoleBindings`) is a real reference row with
  `confidence=ast` instead of nothing, and a qualified static call
  (`ArmorResolution.Classify`) is a caller row; both record the receiver
  expression as written when it is a plain identifier. Names appearing only
  in comments or strings do not match. C# `using` directives are recorded as
  imports, left unresolved (documented, never guessed).
- **precision pass (issue #8).** (1) `find-callers` no longer asserts a
  receiver it never resolved: the subject is reported once on the `note:`
  lines and `receiver=` appears on a direct hit only, and only when the store
  holds one for that row. (2) `semantic-search --format json` carries
  `skipped`/`skipped_count` (plus a trailing line in text/compact), so a
  consumer can tell the result is partial. (3) SKILL-CLI.md names the exact
  `--fresh` command set. (4) `tests/test_flag_contract.py`'s description now
  matches its parametrisation and covers `changed-symbols`. (5) the two-store
  union note is gone from SKILL-CLI.md. (6) the textual scan's comment rule is
  stated precisely: lines BEGINNING with `#`/`//`/`*`/`--` are skipped, while
  trailing comments and strings match.
- **`read_fingerprint` dropped `extraction_version` (found while running this
  round's rebuild).** The recorded fingerprint was rebuilt from four of its
  five recorded keys, so `extraction_version` silently fell back to the
  *current* value on every read and an extraction change could never mark an
  index stale. Fixed — the `EXTRACTION_VERSION` bump above now actually flips
  every existing index to `needs-reindex` — and pinned by a read-back
  round-trip test.

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