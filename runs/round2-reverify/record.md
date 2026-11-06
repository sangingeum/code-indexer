# Narrow re-verify — round-2 folds

- Tip: `0251679` (branch `main`, pushed). Folds: `0b2270c` and `0251679`;
  the reviewed base was `f9616d0`.
- Scope: the eight folds and the regression only. The round-2 gate's twelve bars were
  not re-run. Every number below is this gate's own fresh output, captured under
  `runs/round2-reverify/`.
- **Verdict: FAIL.** All eight folds verify. The regression section carries one
  reproducible defect the fold's collection rename introduced: `remove-project` no
  longer removes a project's vector collection and reports that it did.

## Fold disposition

| fold | result | evidence |
|---|---|---|
| FOLD-1 read leaves the manifest byte-identical; pre-7 refuses, `schema=<n>` | **pass** | `probe_folds.txt` (own probe) |
| FOLD-2 vectors-off refusal with no reachable service | **pass** | `probe_folds.txt` |
| FOLD-3 the identity is in the physical name on every writer path | **pass** | live Qdrant: `probe_live_collection.txt` |
| FOLD-4 a zero-chunk project reports `rows=current` | **pass** | `probe_folds.txt` |
| FOLD-5 the last-resort identity hash is line-free | **pass** | `probe_line_free_hash.txt` |
| FOLD-6 / FOLD-8 dead surface gone | **pass** | `rg` over `src/` + the fold's own pin test |
| FOLD-7 docs true | **pass** (two advisories) | `SKILL-CLI.md`, `CHANGELOG.md` read against the code |
| regression: suite, ruff, hygiene | **fail** | `pytest-full.txt`, `ruff.txt`, `hygiene.txt` green; `probe_remove_project.txt` red |

## FOLD-1 — a read leaves the manifest byte-identical

My own probe (`runs/round2-reverify/probe_folds.py`), not the fold leg's: a throwaway
project registered and passed with `--vectors off` against a closed embedding and
vector port, then every read command run in a subprocess with the manifest's sha256
taken before and after each invocation.

Current manifest (schema 7), one batch:

```
    sha256 before: dcaa841c7c16a04ad069a51e011b311a297645abe4f7fa92eafee90370d45244
  find-symbol      exit=0 stdout=139B sha_same=True
  find-definition  exit=0 stdout=124B sha_same=True
  find-references  exit=0 stdout=25B  sha_same=True
  skeleton         exit=0 stdout=96B  sha_same=True
  overview         exit=0 stdout=145B sha_same=True
  outline          exit=0 stdout=66B  sha_same=True
    sha256 after:  dcaa841c7c16a04ad069a51e011b311a297645abe4f7fa92eafee90370d45244
```

Pre-7 manifest: the fixture's `symbols` table was replaced by the old line-bearing
shape (`(file, name, start_line)`, no `decl_hash`) carrying one row, and
`meta.schema_version` set to `6`. Every row surface then:

```
  find-symbol      exit=4 stdout=0B stderr_lines=1 refused=True sha_same=True
    RebuildRequiredError: index for <workdir>/legacy was written at manifest schema 6
      and this build reads schema 7; its rows are in an older layout, so this answer
      would describe an index this build would not produce;
      run: code-indexer reindex-project <workdir>/legacy
  ... the same single line, exit 4 and empty stdout for find-definition,
      find-references, skeleton, overview, outline
    rows after every read: 1 (expected 1)
    recorded schema after every read: 6 (expected 6)
    symbol columns after every read: ['file', 'name', 'start_line']
    byte-identical: True
```

`index-status` on the same file printed `... rows=current ... schema=6` and exited 0;
on the current manifest it printed `schema=7`. A read neither migrates nor empties.

## FOLD-2 — no-service vectors-off refusal

Both service variables pointed at `http://127.0.0.1:9` (closed) for the whole probe:

```
    exit=4 stdout=0B stderr=283B
    ConfigError: index for <workdir>/proj was built with vectors=off; semantic search
      needs vectors; run: code-indexer reindex-project <workdir>/proj --vectors on
```

Exit 4, `ConfigError`, empty stdout, remedy named — not exit 3 `BackendError`. The
code path agrees: `Core.search` calls `gate_entries(entries)` before
`_query_vector` (`src/code_indexer/core.py:1084-1086`), and the gates themselves
answer from the manifest and this process's configuration.

## FOLD-3 — the identity in the physical name, on every writer path

Read: `Indexer.physical_collection` (`src/code_indexer/indexer.py:82-94`) is the one
helper — it composes the identity and returns `vectors.collection_for(slug,
identity)`; `vectors.collection_for` (`src/code_indexer/vectors.py:126-134`) returns
`idx_<slug>__<identity digest>` and `vectors.alias_for` returns `idx_<slug>`. The
call sites are the two in-pass defaults (`indexer.py:140`, `indexer.py:664`) and
`Core._vector_target` (`core.py:354-362`), which every vector-writing branch of
`run_index` and `reindex_with_swap` uses before `store.swap_collection(...,
alias_for(slug))` (`core.py:400-418`, `core.py:842-857`). No `f"idx_{slug}__new"`
and no plain-slug default survives.

Exercised against a **live collection**, not a stub (`probe_live_collection.py`;
real embedding service, real vector store, a throwaway project, three CLI paths):

```
    identity=qwen3-embedding:8b/4096/contextual-header-v1
    expected physical name: idx_456177bd__dc5868ea
  [full pass]        collections with the slug: ['idx_456177bd__dc5868ea']
                     alias idx_456177bd -> idx_456177bd__dc5868ea   points=3
  [incremental pass] same single collection, same alias, points=4  (a file edited;
                     the read's --refresh ran the pass)
  [vectors rebuild]  same single collection, same alias, points=4
  cleanup: remaining with the slug: []  alias=None
```

Points rising 3 → 4 on the same physical collection is the incremental pass appending
into the identity-stamped collection rather than opening a second one. No plain-slug
physical collection and no `__new` scratch existed at any checkpoint.

## FOLD-4 — a zero-chunk project

A project holding one empty `.py` file, passed with `--vectors off`:

```
    chunks stored=0  last_indexed=True
    project=<workdir>/empty state=idle rows=current vectors=off vectors_state=absent schema=7
```

The fixture was genuinely zero-chunk (`chunks=0`) and the verdict is `rows=current`.
`vectors.rows_current` now reads `meta.last_indexed` plus `meta.extraction_version`
only (`src/code_indexer/vectors.py:71-91`); the chunk count is reported separately.

## FOLD-5 — the last-resort identity hash

The fallback in `Manifest._symbol_row_tuple`
(`src/code_indexer/manifest.py:545-556`) hashes `f"{s.name} ({s.symbol_type})"` when
a row has neither `decl_hash` nor a signature — name and kind, no range. Probe:

```
    decl_hash(alpha function 10-12)  = sha256:7f6fb0ac...
    decl_hash(alpha function 99-120) = sha256:7f6fb0ac...   (identical)
    decl_hash(beta function 10-12)   = sha256:69df7d39...
    decl_hash(alpha class 10-12)     = sha256:d8d987b8...
    ok   the basis is recomputable from name and kind alone
```

The same declaration observed at different lines keeps its hash; a different name or
kind does not. The module docstring states the cost of the collapse (`manifest.py:35-38`).

## FOLD-6 / FOLD-8 — dead surface

`rg -n "record_mode|VECTOR_SIDE_KEYS|chunks_for_file|MIGRATION_HINT|schema_migrated" src/`
returns no definition and no caller for any of the four names; the only remaining hit
is `fingerprint.rows_staleness_warning`, a live pure function (see advisories). The
fold's pin test passes in the targeted run, asserting the attributes are absent.

## FOLD-7 — docs

`SKILL-CLI.md:316-345` and `CHANGELOG.md:1-33` match the code on all four statements
the fold owns: the identity-stamped physical name with the `idx_<slug>` alias and no
`__new`; the read-only open and the `RebuildRequiredError` shape with exit 4; the
`ConfigError` naming `--vectors on` decided before the query is embedded; and
`rows=current` no longer requiring chunks. Two imprecisions are listed as advisories.

## Regression

Green:

```
  678 passed, 2 deselected in 89.97s        (uv run pytest tests/ -q -W error, exit 0)
  66 passed                                 (the fold tests + tests/test_refusal_needs_reindex.py,
                                             tests/test_read_contract.py, exit 0)
  All checks passed!                        (ruff check src tests, exit 0)
  publishing hygiene: scanned=608 skipped=0 violations=0
                                            (595 before this gate's files were staged)
```

Red — `remove-project` no longer removes the vector collection, and says it did
(`probe_remove_project.py`, live collection, throwaway project):

```
    after the pass:        collections=['idx_e4e918b1__dc5868ea']
                           aliases={'idx_e4e918b1': 'idx_e4e918b1__dc5868ea'}
  $ code-indexer remove-project <workdir>/proj  ->  exit=0
    | removed <workdir>/proj: collection idx_e4e918b1 dropped, registry entry deleted
    after remove-project:  collections=['idx_e4e918b1__dc5868ea']
                           aliases={'idx_e4e918b1': 'idx_e4e918b1__dc5868ea'}
    residue left behind: True
```

Cause: `cli.py:717` builds the name as `f"idx_{entry.slug}"` and
`cli.py:718-720` drops it only `if core.store.collection_exists(collection)`.
`Store.collection_exists` is `client.collection_exists(name)` (`store.py:86-87`),
which answers for physical collections and not for aliases, so the guard is false and
the drop never runs — after which the command prints the drop it did not perform.
Before the fold the same line was correct: the physical collection *was* `idx_<slug>`
(`f9616d0:src/code_indexer/indexer.py:121,635`). The fold moved every physical name
to `idx_<slug>__<identity digest>` and left this one deleter naming the alias, so the
deleted registration now leaks an alias plus a physical collection.

Reproduction: `QDRANT_URL=<vector store> uv run python runs/round2-reverify/probe_remove_project.py`
needs a reachable vector store and cleans up the residue it demonstrates (the probes
read `QDRANT_URL` from the environment; no host address is committed).

## Advisories (not fold failures)

1. `docs/subcommand-design.md:107` still says a pre-v3 manifest emits "the migration
   hint already defined in `core.MIGRATION_HINT`" — that symbol was deleted by
   `0b2270c`, so the design doc now cites something that does not exist.
2. `SKILL-CLI.md:337-339` and `CHANGELOG.md:8-9` place `schema=<n>` "beside
   `rows=absent`". Observed: a manifest recorded at schema 6 that also carries this
   build's `last_indexed` and `extraction_version` prints `rows=current ... schema=6`.
   The row verdict is truthful (rows were written by this extraction); the doc's
   pairing over-generalizes the never-indexed case.
3. `fingerprint.rows_staleness_warning` remains a tested pure function with no
   production caller anywhere in `src/` — the fold leg named it rather than cut it,
   which is a reviewer ruling, not a gate finding.

## What this gate used, and its limits

- The live index on this checkout refuses row surfaces with exit 4 until the
  post-round reindex (`schema=7`, `fingerprint=needs-reindex`), so exploration used
  `rg`, direct reads and the fold table's file:line references instead of indexed
  row queries. `index-status` was used where a manifest fact was wanted.
- The two live probes created throwaway collections on the shared vector store and
  deleted them: `probe_live_collection.txt` ends with `remaining with the slug: []  alias=None`, and
  `probe_remove_project.txt` ends with `cleaned up: ([], {})`.
- No source file was edited by this gate; the records and probes here are the only
  additions.
