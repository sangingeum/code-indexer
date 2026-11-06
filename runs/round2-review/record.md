# Round 2 two-axis review

- Reviewed tip: `ae81993` (round base `a22769d`); `origin/main` == `ae81993` at review start
- Reviewer: the household reviewer/architect
- Card: identifier withheld (an authored `runs/*.md` record is hygiene-governed and a board id is a
  prohibited token there; the dispatch brief carries it)
- Gate verdict reviewed: **PASS with two findings**. The gate measured `cf6af65`; `cf6af65..ae81993`
  is capture normalisation only (18 files, all under `runs/round2-gate/`, verified with
  `git diff --stat`), so the gate's bars do describe this tip.
- Deliverable: this record. No code, no dev record, no gate record, no ADR and no plan was edited
  by me.

## Verdict

**changes-requested.**

The round does most of what its records say. The identity key is line-free and the gate proved a
line shift preserves it; the surrogate id is revision-scoped and exposed beside `index_revision`;
the reference columns exist and are staged NULL; a pass with no service reachable writes a complete
manifest including chunk text (I reproduced it: `add-project` on a throwaway root with both service
URLs pointed at a closed port, exit 0, `rows=current vectors=off`); the vectors-on pass embeds from
`chunks` and opens no source file; the parse verdict comes from the parser's own result and
`index-status` counts all three; `--vectors auto` records `off` for a new project before the first
pass (`cli.py:600-608`), so `auto` cannot silently enable embedding; the per-side fingerprint
separates row drift from vector drift. 661 passed, 2 deselected, warn-clean on my own run.

One item blocks: **F2**. A read command opens every manifest read-write, runs the migration on
open, and on a pre-revision-7 manifest drops the symbol table's rows before answering. The plan
ordered round 1 before round 2 for this exact reason — "a schema migration on a codebase whose
reads still write is a migration that cannot be verified" (`docs/overhaul-plan.md:386-387`) — and
shipping the migration while that sentence is still true inverts the ordering's purpose. The gate is
right that the behaviour is documented in ADR-0001's consequences; what is not documented anywhere
an agent reads is that the *reader* is the thing that empties the table.

**F1** is a required fold, not a blocker: the refusal still refuses, with a nonzero exit and no
false answer, but the documented class, exit code and remedy line are wrong in the most likely real
case. Its root cause is one layer deeper than the gate recorded, which is why I want the fix in this
leg rather than carded.

Three spec findings of my own, all in the collection-identity machinery ADR-0001 section 2 calls
"not negotiable": the physical collection name does not always carry the embedding identity
(live evidence, below), one writer-side path can mix vectors across models, and `rows=current`
reports `absent` for a project that legitimately produces no chunks. None is architectural; all
three are small, located, and named in the fold table.

## Axis 1 — specification fidelity

Section by section, against the shipped tree.

### ADR-0002 section 1 — the identity model

**Holds.** `symbols` is keyed `(file, qualified_name, symbol_type, decl_hash)` with `id`,
`name`, `container`, `language`, `start_line`, `end_line` as attributes (`manifest.py:96-110`); no
line number is in the key, and `idx_symbols_file_start` survives only as a lookup index. `container`
and `language` are denormalized columns, and the kind column is still named `symbol_type`. The
chain is threaded through the parse (`ts_chunker._walk` → `_join_qualified`), not re-split from a
name string. `decl_hash` is `sha256:` + the whitespace-collapsed chunker signature
(`chunker.py:52-66`), and the no-signature fallback hashes the declaration's first line
(`manifest.py:479`), which is what the record specifies.

Two letter-of-the-record deltas, neither a defect in behaviour:

- A row with neither a signature nor a `decl_hash` falls back to
  `declaration_hash(None, f"{name}:{start_line}-{end_line}")` (`manifest.py:482-487`). That keeps the
  key total but puts a line number back into it for that one class of row, which the record's "the
  key is line-free" sentence does not contemplate. Extraction always supplies a signature, so the
  branch is unreachable from the pass; the leg record declares it openly. Fold FOLD-5 below.
- `language` is derived from the extension (`_lang_from_ext`) rather than literally copied from
  `files.language` (`indexer.py:411`). The two are computed by the same function, so the values agree
  today; the record's wording is stricter than the code.

### ADR-0002 section 2 — the revision-scoped surrogate id

**Holds.** `id` is assigned from `meta.symbol_id_seq` inside the same transaction as the insert, is
`UNIQUE`, is never reused after a delete (`manifest.py:427-468`), and is regenerated because a
reparse deletes and re-inserts every row of the file. `find-symbol --json` and `find-definition
--json` emit `symbol_id` per row (`cli.py:1800-1814`, `cli.py:1862-1874`); `index_revision` rides in
the staleness block on the same object (`manifest.index_revision`, `manifest.py:496-501`), which is
literally "next to" it. Nothing treats the id as a locator: the plan vocabulary is absent from the
round, and `docs/json-schema.md:247-253` says the same thing the record says.

### ADR-0002 section 3 — reference targets, staged

**Holds.** `refs` keeps `to_name`, `relationship`, `confidence`, `receiver`, `from_symbol` unchanged
and gains `target_symbol_id` and `resolution`, both nullable (`manifest.py:126-142`); the only writer
(`replace_file_refs`, `manifest.py:726-745`) inserts neither, so every row is NULL, and no code reads
or writes `resolution` anywhere in `src/` (checked by search). Graph commands are untouched by the
round (`graph.py` is not in the diff), which is the record's "no answer can regress" by construction
while every target is NULL.

### ADR-0001 section 2 — three axes, the policy matrix, and the collection rule

**Mostly holds; the naming rule is not enforced everywhere.** The axes are recorded as the record
says: `extraction_version` (integer, `graph_extract.EXTRACTION_VERSION` 5), `embedding_version`
(one composed `model/dim/format` string, `vectors.embedding_identity`), `index_schema_version`
(integer, written on open by the migration that a bump means, `manifest.py:274-280`). Rows survive
an embedding bump (the bump rebuilds only the collection: `core.reindex_with_swap` takes the
rows-current branch and calls `embed_index`, `files_indexed=0`) and vectors survive a schema bump
(the migration touches no collection; the policy-matrix test asserts the store saw no upserts).

Three deltas against the rule that "a collection holds exactly one embedding identity ... stamped
into the collection name":

1. **The live physical collections do not carry an identity.** `core.py:770-771`: a same-identity
   vector rebuild embeds into `f"idx_{slug}__new"` and aliases the slug onto it. That name carries no
   digest and is reused for every identity. The gate's own capture of the live store shows it:
   `runs/round2-gate/b6-collections-by-identity.txt` lists `idx_code-indexer__new`,
   `idx_calc-engine__new`, `idx_vivarium-adr__new`, `idx_vivarium-sim__new` and four more, with exactly
   one digest-named collection (`idx_53000ef2__dc5868ea`). The doc sentence that calls the digest
   "stamped into the physical collection name" (`SKILL-CLI.md:318-321`) is therefore not true of twelve
   of the thirteen live collections (eight are `idx_<slug>__new`, four plain slug names).
2. **A fresh vectors-on pass creates an unstamped collection.** `indexer.py:121` (and the same line at
   `:635`) defaults `collection = f"idx_{slug}"`; a row-writing pass with vectors on therefore creates
   a real collection named after the slug. Reachable today via `index-more <proj> <path>
   --vectors on` on a project kept off (`rescan=True` defeats the embed-only path), and via any
   ordinary pass whose rows are stale while the identity also changed.
3. **One writer-side path can mix models.** `core.run_index` (`core.py:360-395`) chooses the
   collection before the identity is consulted whenever the pass is not embed-only
   (`embed_only` requires `rows_current`). So a project whose rows are stale *and* whose embedding
   identity changed — an extractor upgrade shipped together with a model change, or the watcher
   picking up a model change after an extraction bump — writes new-model vectors into the
   collection the alias currently points at, which was built by the old model. That is the one
   thing ADR-0001 section 2 says is not negotiable. `reindex_with_swap` is correct (both of its
   vector branches build a fresh physical collection and swap), so the fix is to route the ordinary
   pass through the same helper. No test covers this combination; the suite's embedding-bump test
   goes through `run_index` with current rows and therefore takes the correct branch.

Enforcement is real for the identity-*change* path (a changed identity cannot append: `_vector_target`
returns a new digest-named collection and `swap_collection` drops the old one) and incomplete for the
steady-state path. Fold FOLD-3.

### ADR-0001 section 3 — the chunks table and the service-free pass

**Holds, verified by running it.** `chunks(file, chunk_index, start_line, end_line, symbol,
 symbol_type, chunk_hash, text)` keyed `(file, chunk_index)` (`manifest.py:145-157`), text is the raw
chunk without the contextual header (`manifest.ChunkRow` docstring), written by the same pass that
reads the file (`indexer.py:362-377`), purged per file (`delete_files`, and `replace_file_chunks` on
re-chunk). A vectors-off pass writes files, symbols, refs, imports, `chunks`, `chunks_fts` and
`index_errors` with no service and records `meta.vectors=off` — my throwaway-root probe produced
files=3, chunks=2, fts=2, `vectors=off`, `vectors_state=absent`, and `add-project` exited 0 with both
URLs pointed at a closed port. A vectors-on pass reads only `chunks.text`/`chunk_hash` and the file
rows (`embed_index`, `indexer.py:627-724`); the named test makes the source tree unreadable and the
gate reproduced it directly (`files_indexed=0`, `chunks_embedded=13`).

One spec deviation folded as FOLD-4: `rows_current` (`vectors.py:71-88`) requires `count_chunks() > 0` on top
of the extraction version, so a project whose indexed files legitimately produce no chunks reports
`rows=absent` for ever. I reproduced it: a single empty `empty.py` indexed at extraction 5 gives
`state=idle rows=absent parse=parse_unavailable:1` from `index-status`. ADR-0001 section 4 defines
`rows` as current-or-absent in the sense of "written by this build's extraction"; the chunk count is
a different fact. Fold FOLD-4.

### ADR-0001 section 4 — `--vectors auto|on|off`

**Holds.** One option on all three pass commands — `add-project` (`cli.py:552-640`),
`index-more` (`cli.py:639-700`) and `reindex-project` (`cli.py:1180-1210`) share one
`VectorsOpt` declaration (`cli.py:153`);
`auto` resolves against `meta.vectors` (`vectors.resolve_mode`); a new project records `off` before
its first pass (`cli.py:600-608`), so the `or MODE_ON` default in `resolve_mode` only ever applies to
a project with no recorded mode, i.e. a pre-extension vectors-on project, which the record requires to
stay on; `watch` passes no `vectors` argument at all (`core.watch_pass` → `run_index()` default
`auto`), so it follows the recorded mode and cannot enable. `index-status` prints `rows=`, `vectors=`
and `vectors_state=` in text and JSON (`cli.py:1010-1016`).

### ADR-0001 section 5 — per-side staleness and the two refusals

**Holds on structure, with two fidelity defects.** `fingerprint_status` splits the sides
(`core.py:633-680`): a row-axis difference raises `RebuildRequiredError` (exit 4) from every row
surface, an embedding-only difference leaves the rows answerable and reports `vectors_state=stale`.
The vectors-off refusal is `VectorsDisabledError`, rendered `ConfigError:`, exit 4, naming
`--vectors on` (`errors.py:92-105`, `core.assert_vectors_available`).

- **F1, and its root cause is wider than the gate recorded.** Both gates run *after* the query is
  embedded: `core.py:1041` computes `self._query_vector(query)` and only then does the per-entry loop
  call `assert_vectors_available` / `assert_fingerprint_ok` (`core.py:1063-1064`) — under a comment
  that says "Fingerprint gate BEFORE any embedding work" (`core.py:1050`). So a vectors-off project
  with no reachable embedder exits 3 `BackendError`, and so does a needs-reindex project, and the
  docstring's "`ConfigError` (a rebuild) from the fingerprint gate before any embedding work"
  (`core.py:1009-1011`) is false. One move fixes all three: resolve the entries, run both gates over
  them, then compute the query vector.
- A per-surface note the gate already ruled on and I do not overturn: `find-symbol --json` answers
  rows without repeating `vectors_state`. ADR-0001 section 5's "the answer says so" is satisfied by
  `index-status`; the record does not require the field per surface.

### ADR-0004 section 7 — the parse verdict

**Holds.** `ts_chunker.parse_file` performs one parse and returns `(chunks, verdict)`;
`_tree_verdict` reads `has_error` and walks for `is_missing` (`ts_chunker.py:132-193`);
`parse_unavailable` is returned on every regex-fallback path (no grammar pack, no grammar for the
extension, parser exception, no AST chunks), and an error-tolerant tree still yields chunks with
`parse_error`. The indexer stores it per file (`files.parse_verdict`) from the parser's return value
only, never from the chunker (`indexer.py:319-326`, `:381-385`), and `index-status` prints all three
counts always, in text and as three flat JSON fields (`cli.py:1087-1097`,
`manifest.parse_verdict_counts`). The named tests pin a syntax error and a no-grammar file.

One semantic note for the record's owner, not a fold: a *valid* file with no declarations (an empty
module, a docstring-only module, a comment-only markdown file) reports `parse_unavailable`, because
the AST walk produced no chunks and therefore the regex fallback ran. That is what the record's
causal sentence says, and it is what my probe showed (three files, three `parse_unavailable`), but an
agent reading `parse_unavailable` as "this file could not be parsed" is being told something false
about a file that parsed perfectly. Worth a clause in the record when it is next touched.

### Round 1's contract, as this round leaves it

The exit taxonomy, the two command classes, the nine reads that never repair, the one-line
`<ErrorType>: <message>` failure shape and the empty stdout on failure all still hold — the refusal
matrix is tested text-and-JSON per surface, and the twelve
call sites of `_refuse_obsolete_rows` (defined at `cli.py:317-338`) sit before every row answer.

**One sentence of it is now false: ADR-0003's "a read command never writes the manifest".** See the
F2 ruling below. This is the finding that decides the verdict.

## Axis 2 — repo standards

### Tests assert behaviour — with one seam gap that matters

Good: the refusal matrix runs every surface in both output modes; the identity tests build a real
pre-7 manifest by hand rather than pretending with a meta value; the vectors-on test makes the
source tree unreadable; the per-side fingerprint test mutates a copy and checks the exit code, the
stderr class and the empty stdout; the identity line-shift test shifts real definitions.

**The gap the dispatch asked for, named:** every claim about *collection identity* is asserted
against `StubStore` (`tests/test_vector_policy_matrix.py:44-96`), which re-implements
`Store.swap_collection`, `collection_exists`, `physical_name` and `_alias_exists` by hand instead of
defining a protocol the real store satisfies. A stub that agrees with the production code proves the
caller's decisions, not the store's semantics. Three consequences are outside the suite's reach, and
two of them are live bugs in this tip (FOLD-3):

- The stub never asserts that the physical name contains the identity digest anywhere. It asserts
  `live != collection_a` and `collection_a in store.dropped`, which passes for `__new` and for an
  unstamped `idx_<slug>` alike — and that is exactly what the live store now holds.
- Whether a real Qdrant accepts `upsert`/`delete` through an *alias* is assumed, not tested: the
  steady-state path (`_vector_target` returning `idx_<slug>`) writes through the alias name, and the
  stub's `collection_exists` treats an alias as a live target. Qdrant's documentation states that
  *queries* can use an alias; it does not state that writes can, and no test in the suite would
  notice if they cannot.
- `test_schema_bump_migrates_the_manifest_and_leaves_the_collection` simulates a v6 manifest by
  rewriting two `meta` values on a v7 one (`tests/test_vector_policy_matrix.py:167-193`). A real v6
  manifest has no `chunks` table and an old-key `symbols` table; this test would keep passing if the
  migration were destructive. The real v6 case is covered once, in `tests/test_identity.py`, and by the
  gate's b10 probe — it is not covered by the policy-matrix module that claims the migration property.

Recommendation: have the test seam be the *store protocol* (a `Protocol` with the five methods, real
`Store` and stub both satisfying it) and assert the digest in the physical name plus the drop of the
old physical collection; and build the v6 fixture with real DDL, as `test_identity.py` does.

### Docs truth — sweep of the surfaces this round touched

Checked: `README.md` (the vectors paragraph and the version table), `SKILL-CLI.md` (identity, parse
verdicts, vectors, refusal contract, key rules), `CHANGELOG.md`, `docs/json-schema.md`. The three
version axes, `rows`/`vectors`/`vectors_state`, the five identity fields, the parse counts, `--vectors`
on all three pass commands, the refusal matrix and the `deps` `--format json` spelling all match the
CLI. Remaining false or unstated claims, all folded:

- `SKILL-CLI.md:318-321` — "the composed embedding identity ... is stamped into the physical
  collection name". Not true of a same-identity rebuild (`__new`) or of a fresh vectors-on pass
  (`idx_<slug>`); twelve of the thirteen live collections are counter-examples. True again after
  FOLD-3.
- `SKILL-CLI.md:335-341` and `CHANGELOG.md:33-35` — the vectors-off refusal is stated as `ConfigError`
  and exit 4 unconditionally. With no reachable embedder it is `BackendError`, exit 3 (F1). True again
  after FOLD-2.
- `SKILL-CLI.md` never says that opening a pre-7 manifest through *any* command migrates it in place
  and empties the symbol table. `README.md:227` does say it ("the old-key symbols table is re-created
  empty") and `CHANGELOG.md:73` repeats it, so the honest statement exists — in the two surfaces an
  agent reads least. Add one line to the skill's migration note (FOLD-7).

### Dead surface introduced by this round

Three symbols added by the diff and referenced nowhere: `vectors.record_mode` (`vectors.py:106-111`,
whose two branches return the same value), `vectors.VECTOR_SIDE_KEYS` (`vectors.py:40`) and
`Manifest.chunks_for_file` (`manifest.py:381-384`). The indexer writes `meta.vectors` directly, so
`record_mode` has no caller at all. FOLD-6.

A fourth, created by the round rather than added: the four `MIGRATION_HINT` branches
(`cli.py:1706, 1741, 1823, 1882`) are now unreachable, because every one of those commands calls
`_refuse_obsolete_rows` first (`cli.py:1697, 1731, 1778, 1858`) and a manifest that was just migrated
from a pre-7 schema is by construction `needs-reindex`. The constant predates this round; its
reachability does not. FOLD-8.

### Commit identity, signing, hygiene, terminology

- Every commit in the range is signed (`git log --format='%h %G?'` on the eight implementation
  commits and the three gate commits: `G` throughout) and authored by the repo-local identity.
- `uv run pytest tests/ -q -W error` → `661 passed, 2 deselected in 86.52s`, exit 0 (my own run).
- Terminology: no coordinate labels in the new identifiers, file names or test names (searched the
  added `def`/constant names and the new test modules). "v7" appears only in comments as a schema
  revision reference, which is the same class as an ADR number.
- Hygiene: the gate's authored records mask the card id (`runs/round2-gate/record.md:2`,
  `runs/round2-leg2-service-free-pass/record.md:3`); the captures were normalised at `ae81993`. This
  record carries no board id, no host path and no profile name.

### Do the round's own records overclaim anything the gate did not measure?

Two, both minor and both in the leg records rather than the gate's:

- `runs/round2-leg2-service-free-pass/record.md` states the vectors-off refusal as "raises
  `ConfigError` naming `--vectors on`, exits 4" with no precondition. It is the same overclaim as
  F1 in the doc surfaces, and the gate's F1 is its correct statement.
- The gate's own bar text is the sharper one: "collection name carries the identity digest
  (`idx_<slug>__<8 hex>`), observed live in the Qdrant list". The capture it cites shows one
  digest-named collection and eight `__new` ones. The bar's verdict (the naming rule exists) is
  supported by the code path it names; the evidence sentence is not what the file says. I did not
  edit the gate record — this is the correction it should carry if the owner asks for one.

## Ruling on the gate's two findings

### F1 — the vectors-off refusal runs after embedder initialisation: **fold, required**

The gate's description is exact and its severity (moderate) is right; I would raise its scope.
Root cause at `core.py:1041` versus `core.py:1063-1064`: the query embedding happens before the
per-entry gates, so both the vectors-off guard and the fingerprint guard are skipped when the
embedder is unreachable — for a needs-reindex project as well as a vectors-off one, and contrary to
two docstrings that claim the gate runs "before any embedding work". The documented remedy line
(ADR-0001 section 5) is a promise about a machine with no Ollama, which is exactly the machine that
gets the wrong error. FOLD-2. Not blocking: the command still fails loudly, with no answer and no
false success, so no caller is told something untrue about their code — only about what to do next.

### F2 — reads open the manifest read-write and migrate it in place: **blocking**

Confirmed on the code and on the gate's captures; the destructive half is mine only by reading, not
by re-running (the dangerous-SQL guard blocks a `DROP TABLE` probe in this session, and I did not
work around a safety guard):

- `Manifest.__init__` opens `sqlite3.connect(db_path)` with no mode (`manifest.py:213-217`), so every
  opener is read-write; it executes `PRAGMA journal_mode=WAL`, then the DDL.
- If `symbols` exists and has no `decl_hash` column it is **dropped** (`manifest.py:231-232`) and
  re-created empty. Gate b10 on a copied v6 manifest: `symbols after open: 0`, `chunks after open: 0`,
  file sha changed, second open idempotent. There is no recovery path for those rows short of a
  reparse.
- The milder half I did measure myself, and it is milder than the finding implies: on a manifest
  already at revision 7 a read changes **nothing** — `find-symbol` on a freshly written manifest left
  sha256 and size byte-identical and created no `-wal`/`-shm` residue. So the twelve-manifest byte
  change the gate saw was the migration, not WAL churn on the read path. Good news for the fix: only
  the pre-7 open needs to change.

**The option I would accept: a read-only open with a schema gate** (option (b) in the brief, in its
cheapest form).

- `Manifest(path, writable=False)` opens with `file:...?mode=ro`, skips `journal_mode` and every
  DDL/`ALTER`, and exposes the recorded `schema_version`.
- A read whose manifest is not at the current schema answers the surfaces that need no rows
  (`index-status` reports `schema=<n> rows=absent`, `list-projects`, `lookup-project`, `doctor`) and
  refuses every row surface with the existing `RebuildRequiredError` shape, exit 4, naming
  `reindex-project`. No column-tolerance work is needed, because no row query runs.
- Migration stays exactly where it is now — on write opens (pass commands, the watcher, `--refresh`) —
  which is what "migrated on open, then run one pass per project" already means operationally.
- Cost: one keyword on `Manifest`, a schema check at the two entry points that can answer without
  rows (`core.vectors_status`, `core.fingerprint_status`) and in `_refuse_obsolete_rows`, a `schema=`
  field in `index-status`, and two tests: a copied v6 manifest is byte-identical after every read
  command, and a row surface on it exits 4 with empty stdout. Half a day, no data risk.
- Why not the alternatives: **copy-on-migrate** (c) doubles write amplification and leaves a second
  file whose lifetime nothing owns — and it still has a read doing a migration, so ADR-0003's
  sentence stays false. **An explicit `index-status --migrate`** (a) is a good companion but not a
  substitute: the trap is the *implicit* migration, and giving it a flag does not stop the implicit
  one that already exists on the read path. **An ADR amendment declaring migration-on-open an
exception** (d) I would argue against in writing: it licenses a read to destroy rows that a reparse
  cannot reconstruct cheaply, which is the one thing the read contract exists to make impossible; and
  the exception would have to be repeated in ADR-0003's decision paragraph, its consequences and the
  skill, i.e. the guarantee becomes "reads do not write, except sometimes".

**Does ADR-0003 need an amendment? No — if option (b) ships, its sentence becomes true again, which
is the point of choosing it.** What needs an owner decision is **ADR-0001 section 2**, whose
`index_schema_version` row says the manifest is "migrated on open": under (b) that must read
"migrated on open by a write pass; a read reports the recorded schema and refuses to answer rows".
That is an ADR edit and therefore the owner's call, not mine and not a dev leg's — I am stating it,
not making it. If the owner instead prefers (d), the ADR-0003 amendment is the owner's to make and I
record my disagreement here.

## Folds for the fix leg

FOLD-1 is blocking; FOLD-2 is required for closure; the rest are corrections to make in the same leg.

| id | file:line | exact change | kind |
|---|---|---|---|
| FOLD-1 | `src/code_indexer/manifest.py:213-232`, `src/code_indexer/core.py:201-204`, `src/code_indexer/cli.py:317-338`, `src/code_indexer/cli.py:1010-1016` | Open manifests read-only on read paths (`writable=False`, no `journal_mode`, no DDL), gate row surfaces on the recorded schema with the existing `RebuildRequiredError` shape, and report `schema=<n>` from `index-status`. (gate F2) | fix (blocking) |
| FOLD-2 | `src/code_indexer/core.py:1036-1067` | Resolve the entries, run `assert_vectors_available` and `assert_fingerprint_ok` over them, and only then compute `vector = self._query_vector(query)`; drop the now-false "BEFORE any embedding work" comment and the docstring claim at `core.py:1009-1011`. (gate F1) | fix (required) |
| FOLD-3 | `src/code_indexer/core.py:769-773`, `src/code_indexer/core.py:360-395`, `src/code_indexer/indexer.py:121`, `src/code_indexer/indexer.py:635` | Every vector-writing pass takes its physical collection from `vectors.collection_for(slug, identity)` and swaps it under the `idx_<slug>` alias: replace `f"idx_{slug}__new"` and both `collection or f"idx_{slug}"` defaults. One helper decides the physical name; the identity is then always in the name. | fix (spec: ADR-0001 section 2) |
| FOLD-4 | `src/code_indexer/vectors.py:71-88` | Decide `rows_current` from `last_indexed` + `extraction_version` only; if a chunk-presence signal is wanted, report it as its own fact rather than folding it into `rows`. A zero-chunk project must not report `rows=absent` after a completed pass. | fix (spec: ADR-0001 section 4) |
| FOLD-5 | `src/code_indexer/manifest.py:482-487` | Either drop the range-based third fallback (extraction always supplies a signature, so the branch is unreachable) or make it hash nothing line-bearing; the record promises a line-free key. If it stays, say in the docstring that this one class is line-dependent. | fix (record fidelity) |
| FOLD-6 | `src/code_indexer/vectors.py:106-111`, `:40`; `src/code_indexer/manifest.py:381-384` | Delete `record_mode` (no caller; both branches identical), `VECTOR_SIDE_KEYS` and `chunks_for_file`, or wire each to a caller. | dead surface |
| FOLD-7 | `SKILL-CLI.md:318-321`, `:335-341`; `CHANGELOG.md:33-35`; plus one new line in the skill's migration note | After FOLD-2 and FOLD-3 the two existing sentences become true; add the missing statement that opening a pre-7 manifest through a read migrates it and empties its symbol rows (or, after FOLD-1, that a read reports the schema and refuses until a pass runs). | docs |
| FOLD-8 | `src/code_indexer/cli.py:1706, 1741, 1823, 1882` | The four `MIGRATION_HINT` branches are unreachable behind `_refuse_obsolete_rows`; delete them or fold the hint into the refusal message. | dead surface |

## Deferred, with the record that owns each

- **Populating `refs.target_symbol_id` / `resolution`.** Staged by this round's own record; ADR-0002
  section 3 owns the rule and a later leg owns the work. Not a gap.
- **`find-symbol`'s structural filters, `--name-pattern`, `--container`, `--inherits`, `--has-call`,
  the `evidence:` label and the ambiguity envelope.** ADR-0002 sections 4-5, round 3.
- **A row surface repeating `vectors_state` in its own payload.** ADR-0001 section 5 does not require
  it; if the owner wants per-surface reporting, it is a one-field change owned by a later record,
  not a deviation here.
- **`get-code-context` computing the staleness verdict twice.** The leg record names it as a carried
  round-1 residual; it is a cost, not a contract, and I did not fold it.
- **Collection residue when a project is switched to `--vectors off`.** The collection survives and
  nothing drops it; no record decides whether it should. Worth a sentence in the record that owns
  the mode switch, not a fix here.

## Not reviewed

- Rounds 3-5 (structural query, plans and apply, provider and agent surface) and any commit outside
  `a22769d..ae81993`.
- The operational reindex of the twelve live projects and the correctness of their content after it;
  the live index is still at extraction 4, so every row surface on this checkout refuses with exit 4,
  and exploration used `rg`/`fd` and direct reads where the house indexer refused (stated in-band, as
  the dispatch allowed).
- The watcher daemon's internals (`watcher.py` is untouched by the diff; I reviewed only the mode
  plumbing at `core.watch_pass`).
- `doctor`, the MCP surface and `server.py` — outside this round's diff.
- Whether a real Qdrant accepts `upsert`/`delete` through an alias: I named it as an untested
  assumption (Axis 2) rather than answering it. Verifying it needs a live collection rebuild, which
  the gate also declined and which the reindex register does not cover.
- The gate's own bars: ruled on, not re-run; the one number I re-measured independently is the test
  suite (661/2, warn-clean).

## Evidence produced by this review

All at `ae81993`, in this session's scratch directory; no live state was touched.

- `git ls-remote origin refs/heads/main` → `ae819931754c9b7c60f7f2f1276ec1385dd68508`.
- `uv run pytest tests/ -q -W error` → `661 passed, 2 deselected in 86.52s`, exit 0.
- Service-free pass, throwaway root, `OLLAMA_URL`/`QDRANT_URL` at `http://127.0.0.1:9`:
  `add-project` exit 0; `index-status` → `state=idle rows=current vectors=off vectors_state=absent`;
  manifest `chunks=2 chunks_fts=2 files=3 extraction_version=5 schema_version=7`.
- Zero-chunk project (one empty file): `index-status` → `rows=absent` after a completed pass —
  FOLD-4's reproduction.
- Read-on-a-current-schema probe: `find-symbol` on a freshly written v7 manifest left
  `sha256=eafb6e91…` and size 86016 unchanged and wrote no `-wal`; the mutation the gate observed is
  the pre-7 migration, not the WAL path.
- `runs/round2-gate/b6-collections-by-identity.txt` — twelve of the thirteen live collections carry no
  identity digest (eight are `idx_<slug>__new`, four are plain slug names); one does.
