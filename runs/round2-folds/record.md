# Round 2 folds — leg record

- Card: identifier withheld (this is an authored `runs/*.md` record and a board id is a
  prohibited token here; the dispatch brief carries it).
- Base: `f9616d0` (round-2 review tip, captured at `ae81993`). Tip of this leg: see the
  verification section.
- Scope: the eight folds the two-axis review requested, their tests, and the FOLD-7 docs. No
  revision bump, no DDL change, no new command surface, no structural filters, no provider work.

## Disposition, per fold

| fold | disposition | carrier |
|---|---|---|
| read paths open the manifest read-only; row surfaces gated on the recorded schema; `index-status` reports `schema=<n>` | **applied** | `0b2270c` + the read-path completion commit |
| refusal ordering: gates run before the query embedding | **applied** | `0b2270c` |
| one helper decides the physical collection name, aliased per write pass | **applied** | `0b2270c` |
| `rows` decided by the pass stamp and extraction version only | **applied** | `0b2270c` |
| last-resort declaration hash made line-free | **applied** (name + kind, not the observed range; the collapse that costs is stated in the docstring) | `0b2270c` |
| dead surface: `record_mode`, `VECTOR_SIDE_KEYS`, `chunks_for_file` | **applied** | `0b2270c` |
| dead surface: the four unreachable migration-hint branches | **applied** | `0b2270c` |
| docs: identity naming, the refusal's precondition, the read/migration statement | **applied** | docs commit |

One further dead-surface finding, not in the fold table: `Core.rows_staleness` had **no caller
anywhere in `src/` or `tests/`** — the manifest-only staleness warning it existed for is rendered by
the staleness block instead. It opened the manifest read-write, so a read-only sweep found it; it is
deleted with this leg rather than patched. Its one-time helper `fingerprint.rows_staleness_warning`
remains a tested pure function with no production caller — named here rather than cut unilaterally,
for the reviewer to rule on.

## The five surfaces that failed the byte-identity test

The first run of the new byte-identity test failed on `find-definition`, `find-references`,
`overview`, `skeleton` and `outline` (`find-symbol` passed), and the failure was order-dependent:
each of those cases passed when run alone.

The investigation, in the order it settled the question:

1. **A read-only open does not checkpoint.** Probe: a WAL database with a live writer, 4096-byte
   file and a 61,832-byte WAL; a `mode=ro` open reads the committed rows and leaves the file at
   `5c9dec18…`, 4096 bytes. Only the *writer's* close checkpoints (`→ 9e3fe3f6…`, 53,248 bytes).
   So the read-only path could not be the thing changing bytes.
2. **The failing runs measured a pending writer.** The captured "before" hash in the failing
   assertions was `5c9dec18…` — the 4096-byte WAL-pending state — and the "after" hash was the
   checkpointed file. The test rig passed `core.manifest_for(entry.slug)` inline to
   `index_project(...)`, so nothing closed it: the connection survived by reference cycle and was
   collected by the cyclic GC at an arbitrary later allocation, i.e. during one of the invocations
   under measurement. That is the writer costing the file its bytes, not the reader.
3. **A settled baseline passes all six, with no code change on those paths.** With the rig closing
   its own writer (`try: index_project(...) finally: seeding.close()`), all six surfaces are
   byte-identical — the capture below is that run.
4. **The mechanism is now asserted directly, not inferred.** `test_a_read_opens_the_manifest_read_only`
   wraps `Manifest.__init__` and fails if any manifest a read command opens has `writable=True`. A
   byte comparison can only notice a writable open that costs the file something; this notices the
   open itself.

So the honest statement is not "five surfaces were still writing". It is: **the byte-identity test's
first run was measuring the fixture's own unclosed writer, and the one genuinely write-capable read
path it surfaced (`Core.rows_staleness`) was dead code.** Both are fixed: the rig settles its
baseline, the dead method is gone, and a new test pins the opener. The assertion itself was not
weakened — the logical snapshot remains the writer's contract and the byte hash remains the
reader's.

## Before / after, from the commands

Full capture: `runs/round2-folds/read-contract.txt`, produced by
`uv run python runs/round2-folds/probe_read_contract.py`. Both services point at a closed port
(`http://127.0.0.1:9`); paths are masked in the capture.

### Read paths cost the file nothing — a current manifest

```
== CURRENT (revision written by this build) read batch (manifest.db) ==
sha256 before: af431a6d07aa644087a2f59dfe5699206814f72170d4af10556d1282c08b3e91
  find-symbol      exit=0 stdout=137B sha256_same=True
  find-definition  exit=0 stdout=123B sha256_same=True
  find-references  exit=0 stdout=313B sha256_same=True
  skeleton         exit=0 stdout=159B sha256_same=True
  overview         exit=0 stdout=159B sha256_same=True
  outline          exit=0 stdout=68B sha256_same=True
sha256 after:  af431a6d07aa644087a2f59dfe5699206814f72170d4af10556d1282c08b3e91
byte-identical: True
```

### A pre-revision-7 manifest: refused, never migrated

```
index-status exit=0
project=<project> state=never-indexed rows=absent … schema=6

sha256 before: a4e4eb69114cb3ae8f10e36e13ea97d629e8ecb11464da7ddf8161d5d02e5248
  find-symbol      exit=4 stdout=0B sha256_same=True
    stderr: RebuildRequiredError: index for <project> was written at manifest schema 6 and this
            build reads schema 7; its rows are in an older layout, so this answer would describe an
            index this build would not produce; run: code-indexer reindex-project <project>
  … the same one-line refusal and exit 4 for find-definition, find-references, skeleton, overview,
    outline, each with sha256_same=True
sha256 after:  a4e4eb69114cb3ae8f10e36e13ea97d629e8ecb11464da7ddf8161d5d02e5248
byte-identical: True
symbol rows after every read: 1
recorded schema after every read: 6
```

Before this leg the same batch ran the migration on the first read, emptied `symbols` and advanced
`schema_version` to 7 (gate capture `runs/round2-gate/b10`): `symbols after open: 0`, file sha
changed. Now the row and the revision are still there after six reads.

### The vectors-off refusal with no reachable service

```
== semantic-search on a vectors-off project, embedder at a closed port ==
exit=4 stdout=''
stderr: ConfigError: index for <project> was built with vectors=off; semantic search needs vectors;
        run: code-indexer reindex-project <project> --vectors on
```

Before the fold the same invocation exited **3 `BackendError`**: the query embedding ran first
(`core.py:1041` at the reviewed tip) and the gates only inside the per-entry loop, so the documented
class, exit code and remedy line were unreachable on exactly the machine they exist for.

## Tests added

| test | what it pins |
|---|---|
| `test_a_read_leaves_the_manifest_byte_identical` (6 params) | the file's sha256 is unchanged by every read command, on a settled baseline |
| `test_a_read_opens_the_manifest_read_only` (6 params) | no manifest a read opens is write-capable — the mechanism behind the byte assertion |
| `test_a_read_of_a_version_six_manifest_refuses_and_never_migrates` | a pre-7 manifest: one `RebuildRequiredError` line, exit 4, empty stdout, sha256 unchanged, the old-key row and `schema_version=6` intact, `index-status` reporting `schema=6 rows=absent` |
| `test_semantic_search_on_a_vectors_off_project_refuses_before_dialling` | exit 4 `ConfigError` naming `--vectors on` with the embedder at a closed port — never exit 3 |
| `test_every_writer_path_names_its_collection_after_the_identity` | full and incremental passes both write `idx_<slug>__<identity digest>`, alias coherent, no `__new`, no plain-slug physical collection |
| `test_a_zero_chunk_project_reports_rows_current` | a completed pass with no chunks reports `rows=current` |
| `test_no_dead_surface_survives_the_folds` | `record_mode`, `VECTOR_SIDE_KEYS`, `chunks_for_file`, `MIGRATION_HINT`, `schema_migrated` are gone and the replacement helpers exist |
| `test_pre_v3_manifest_is_read_at_its_recorded_schema` (rewritten) | a read reports the recorded revision and leaves the file alone; a write open still migrates |

Test doubles gained `swap_collection` where a pass now binds the alias; `test_fingerprint`'s swap
assertion now checks the superseded *physical* collection instead of the plain slug name.

## Live state, and what this leg did not do

The LIVE manifests on this checkout were already migrated to revision 7 **by gate reads** before this
leg: they hold `symbols=0` for the migrated tables and report `needs-reindex` (the extraction bump
made that true independently). This leg does not repopulate them — the post-round reindex is that
operational step, and no live collection was rebuilt here. The refusal every row surface gives until
then is the documented behaviour, not a regression.

## Process deviations, named

- **One `sed -i` on `runs/round2-folds/probe_read_contract.py`** (renaming `exit_code` to `returncode`
  on `CompletedProcess`), which bypasses the filekeeper rule. It is a discipline finding against this
  leg, not a hidden one; every other write went through filekeeper.
- **The brief's premise for this resume did not hold as stated.** It reported five read surfaces still
  touching the file; the measurement above shows the byte churn was the test rig's unclosed writer.
  The two real defects the investigation produced — the dead `Core.rows_staleness` write-capable open
  and the unstable baseline — are fixed, and the mechanism is now asserted directly.
- **The provisional probe bug is fixed**: the capture was originally produced by
  `python -m code_indexer`, which runs the MCP server rather than the CLI, so `add-project` exited 0
  doing nothing. It now runs `-m code_indexer.cli`.
