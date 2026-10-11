# Changelog

## [Unreleased] — the fingerprint refusal renders as one line (issue #16)

- **a mismatched index now refuses with one clean error line, not a rich
  traceback (issue #16).** `semantic-search` was the only command whose
  failures escaped to the framework: a fingerprint-mismatched index raised
  out of the command body, so an operator got a wall of traceback with the
  actionable sentence (`run: code-indexer reindex-project <path>`) buried at
  the bottom. Worse, the message sources baked a `ConfigError: ` prefix into
  the *message*, and the shared failure taxonomy renders one line as
  `<ErrorType>: <message>` — so the type was printed twice
  (`ConfigError: ConfigError: index for ...`). The refusal now goes through
  the same failure seam every other subcommand uses (`errors.fail`): exactly
  one `<ErrorType>: <message>` line on stderr and the taxonomy's exit code,
  with the traceback available under `--verbose`. The three message sources
  that carried the baked-in prefix (`fingerprint.mismatch_error`,
  `Core.assert_fingerprint_ok`'s fallback, and the embedder's fatal
  model-missing error) no longer carry it, so the taxonomy is the single
  source of the prefix. **Which surfaces refuse is unchanged**: `search`
  still refuses a mismatched index, and the manifest-only surfaces still warn
  and answer.

## [Unreleased] — fingerprint reporting (issue #13)

- **`index-status` no longer reads as idle on an index that owes a rebuild
  (issue #13).** Its output was built only from the in-process *pass* state, so
  a project whose index no longer matched the current configuration — which
  `doctor` and `list-projects` both call `needs-reindex` — printed a plain
  `state=idle`, two readers giving two answers on the same binary. It now also
  reports the *fingerprint* state: `fingerprint=needs-reindex reason=<diff>` is
  printed whenever the recorded fingerprint does not match the running
  configuration, the same verdict the other lifecycle surfaces give. The
  `index-status` docstring and `SKILL-CLI.md` describe both vocabularies (pass
  state vs fingerprint state) so they are no longer confusable.
- **the manifest-only surfaces warn once instead of answering silently
  (issue #13).** `find-references`, `find-callers`, `find-callees`, `deps`,
  `skeleton`/`map` and `outline`/`file-outline` read stored rows and are NOT
  made fatal on a mismatch — refusing them would remove the tool's usefulness
  on exactly the index a user is inspecting. They now print one stderr warning
  line when the stored rows may have been produced by different extraction
  code, comparing only the version-ordered keys (`chunker_version`,
  `extraction_version`) and naming the recorded and current values. The
  embedding-configuration keys (`embed_model` / `embed_dim` /
  `embed_text_version`) are deliberately NOT consulted — they affect the
  vectors, not the stored rows — and that asymmetry is stated in
  `fingerprint.rows_staleness_warning`. `search` remains fatal on a
  fingerprint mismatch, unchanged.

## [Unreleased] — the orphan sweep (issue #11)

- **new `sweep-orphans` subcommand clears deleted-file residue (issue #11).**
  A row in `refs`, `imports` or `symbols` whose `file` has no `files` row and
  whose file is gone from the working tree is residue from a deletion that did
  not clean up. The command is a **dry run by default**: it prints the
  per-table counts and the file list it would clear and changes nothing;
  `--apply` removes them in one transaction and reports the count removed. It
  reads the manifest directly and never triggers an index pass. No implicit
  purge is added to the pass.
- **the delete path now purges `imports` rows too (issue #11).** This is the
  one behaviour change in this leg: `Manifest.delete_files` removed `files`,
  `symbols` and `refs` rows for a deleted path but skipped `imports`, so every
  deletion left that file's import rows behind as orphans. New deletions no
  longer accrue that residue; `sweep-orphans` clears what earlier ones already
  left.

## [Unreleased] — require scoping, pidfile-probe honesty, a vacuous assertion (issues #12, #14, #15)

**This round needs a rebuild**: `EXTRACTION_VERSION` is `4`, so every existing
index reports `needs-reindex` and refuses queries until `reindex-project`
re-extracts. Existing live indexes were still recorded at `2` (never rebuilt to
`3`), so this bump adds no work beyond the rebuild that was already pending.

- **a JS/TS call with a string argument is no longer stored as an import AND
  an include (issue #12).** `queries/typescript`, `queries/tsx` and
  `queries/javascript` captured the require shape twice with no predicate on
  the function name, so `alert("hi")` produced an `imports` row (kind
  `require`, target `hi`) **and** an `includes` reference row. Both patterns
  are now scoped with `(#eq? @_fn "require")`: a `require(...)` call still
  yields its `imports` row (kind `require`) and its `includes` row, and an
  ordinary string-argument call yields neither (only its `call` row). The
  dead, unhandled `@import.require-target` capture was dropped in the same
  edit. The claim in the query comments that the pattern behaved "exactly as
  the C/C++ queries do" was inaccurate — the C/C++ capture matches the quoted
  include node only — and is corrected in place.
- **the pidfile liveness probe no longer opens read-write (issue #14).**
  `watcher.pidfile_is_free` opened the pidfile `O_RDWR` and returned "free" on
  any `OSError`, so a live watcher whose pidfile this user cannot open
  read-write read as free and `doctor` reported `stale pidfile` about a live
  watcher. `flock` works on a read-only descriptor, so the probe now opens
  `O_RDONLY`, and a new `watcher.pidfile_liveness(path) -> "free" | "held" |
  "unknown"` distinguishes "cannot open at all" (unknown) from "free".
  `pidfile_is_free` is kept for existing callers but maps unknown to *not
  proven free*, so no caller turns ignorance into a confident negative.
  `doctor`'s `watch` line renders unknown as a warning that is not the stale
  message; `stop_watcher` gains an `unknown` state and its "no watcher
  running" branch no longer claims absence on an unknown probe; `unwatch`
  exits 1 with a "cannot determine" line for it.
- **a test assertion that could never fail (issue #15).**
  `tests/test_graph.py` compared a reference row's `from_symbol` (index 1)
  against line numbers while meaning the line (index 0), so the
  "names only in comments/strings are never captured" property was untested.
  The assertion now indexes the line field, and the positive direction (the
  same name IS captured from real code, on its line) is pinned alongside it.
  The repaired assertion was then proven to fail under a mutation that lets
  the AST path also match string content, and to pass again once the file was
  restored byte-exact.

## [Unreleased] — watcher and index-state hygiene (issue #10)

Three index-side defects fixed at their source, plus the operational
ordering that keeps the third one from recurring. The code changes
themselves imply no reindex; the procedure below is what a version bump
already needed.

- **`watch --all` survives one bad registration.** A registered project
  whose root is missing or unreadable is now skipped with a single warning
  naming it, and the remaining projects are served; the watcher reports the
  projects it actually covers and exits nonzero only when it covers none.
  The failure that actually occurred — watchdog raises the
  `inotify_add_watch` error from the observer's `start()`, not from
  `schedule()` — no longer aborts the watcher: it degrades to the documented
  quiet-period polling fallback, which the docstring already promised. A
  `--background` parent also re-confirms the daemon is alive and still
  holding the pidfile after the handshake, so it never prints
  `watch daemon started` for a daemon that died on entry (previously it
  exited 0 over a dead daemon).
- **`doctor` no longer calls a live watcher stale.** The `watch` line reads
  liveness from the pidfile **flock**, not from pid existence or the file
  text. The pidfile format is `<pid> <iso-timestamp>`, so the old
  whole-file `int()` parse failed on every live watcher and reported
  `stale pidfile (pid None not running)`. A held flock now reports `running
  (pid <pid>)`, a held flock with an unreadable pid reports `running (pid
  unreadable)`, and only a free flock with a pidfile present is genuinely
  `stale pidfile`. Query-time only; no reindex implication.
- **a process does not overwrite a fingerprint it does not recognise.** A
  pass whose manifest was recorded by NEWER code than the running process (a
  version-ordered key — `CHUNKER_VERSION` / `EXTRACTION_VERSION` — recorded
  higher than this process's own) now logs a loud warning naming the
  project, both fingerprints and the remedy, and defers the re-stamp,
  leaving the newer value alone. Previously the mismatch check was
  direction-blind: an older-code watcher's sweep re-embedded the whole
  project with its older extraction code and then stamped its own older
  version, reverting the flag and replacing good vectors. The legitimate
  direction — an index built by older code, rebuilt by a newer process —
  still rebuilds and re-stamps.
- **operational ordering documented.** After any version bump, stop all
  watchers, rebuild, then restart them on the new code:
  `unwatch --all` -> `reindex-project <path>` -> `watch --all --background`.
  A watcher left running across a bump silently reverts the fingerprint
  every sweep. Recorded in the `watch --help` text and this repo's
  `SKILL-CLI.md` watch details; `doctor`'s fingerprint check names the live
  watcher when a mismatch is present.

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
  immediately by `(`. Measured over the live indexes, that adds 20,157 rows:
  13,338 (≈66%) are `.csx` script call sites (real code) and the other 6,819
  (≈34%) are not — markdown 6,468 (≈32%), then json 244, shell 78, text 9,
  toml 2 and a few stragglers. Allowing whitespace before the paren would add a
  further 89,882 matches that are overwhelmingly prose (79,551 of them
  markdown, e.g. `function (see below)`), so the tight form is the documented
  rule. That removes the spaced-prose flood but does **not** make markdown
  prose call-free: the `word(s)` shape is tight, so `commit(s)`, `actor(s)` and
  `project(s)` in prose are still stored as `call` rows. One occurrence yields
  one row: a tight `name(` is a `call`, any other identifier an `identifier`.
  Rows stay `confidence='heuristic'` — a `name(` in a string or trailing
  comment still matches.
- **AST `includes` coverage.** Every AST language now routes its import
  directive into the reference store as an `includes` row, in parallel with
  its unchanged `imports` row: python module names, TS/JS module specifiers
  and `require` sources, Go import paths, and C# `using` targets (the capture
  is a second `@ref.include` on the SAME node, exactly as the C/C++ queries
  already did). `find-references <module> --relationship includes` therefore
  answers for python/typescript/go/csharp instead of a bare negative.
  `queries/tsx/` and `queries/javascript/` were
  missing entirely, so `.tsx` and `.js` files silently produced NO reference
  rows at all; both now have real query files, and `.jsx` resolves to the
  `javascript` language like every other JS extension. Angled C/C++ system
  headers remain unresolved and produce no row (documented, pinned by test).
- **a filtered-empty report is honest.** `find-references X --relationship
  calls` no longer prints a bare negative that reads as "nothing calls this"
  when references to `X` exist under other relationships: it now prints
  `no calls references to 'X'; 12 references exist unfiltered (identifier,
  type)`. One aggregate query, not a second report; the bare line is kept for
  a genuinely absent name.
- **C# member-access precision.** Only the OUTERMOST access of a chain is a
  reference: `System.Console.WriteLine(x)` yields the `WriteLine` row and no
  row for the intermediate `Console`/`System` segment, which previously
  became a reference target nobody can call or read. Measured on 359 live
  `.cs` files: 3,805 of 61,190 stored reference rows were such intermediate
  segments (63 distinct receiver-shaped names — `Ledger`, `World`, `Producer`,
  `CultureInfo`, `JsonSerializer`). The stated trade-off: a surviving row of a
  chain deeper than one hop now carries `receiver=NULL`, because its receiver
  expression is itself a member access. The same-line receiver collapse
  (one row per `(file, line, to_name, relationship)`) is documented in
  `graph_extract`, `manifest` and SKILL-CLI.md rather than fixed: keeping both
  rows would require a `refs` table rebuild to change the primary key, which
  was measured as unclean against the gain for a rare shape. Both routes are
  pinned by test.
- **stale residue cut.** `docs/subcommand-design.md` no longer claims `deps`
  reads `symbol_refs` (it reads the `imports` table) and its supersede note
  names the v6 store; `docs/audit.md` no longer describes
  `ts_chunker.extract_refs` as the live reference producer. The dead
  production-looking pair `Manifest.find_refs` / `RefRow` (tests were their
  only callers) is deleted — `tests/test_csharp_references.py` and
  `tests/test_codeintel.py` now read through the real reader,
  `Manifest.refs_to` (and `Core.find_references`). The
  `replace_file_refs_heuristic` stub is gone from
  `tests/test_indexer_embed_context.py`; the method does not exist.

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