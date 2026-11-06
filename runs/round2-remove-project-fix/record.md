# remove-project — reclaiming the collection the alias serves

- Card: identifier withheld (this is an authored `runs/*.md` record).
- Base: `98e8635` (branch `main`, pushed). Scope: the one regression the narrow
  re-verify found, its tests, and the three advisories that were each a small,
  local edit. Nothing else.
- Verdict: the deleter now resolves the alias to the physical collection, drops
  every collection the project owns, removes the alias first, and prints only
  what it actually did.

## The defect

`remove-project` built the collection name as `f"idx_{entry.slug}"` and dropped it
only `if core.store.collection_exists(...)`. Since the collection rename, that
name is an **alias**, and `Store.collection_exists` answers for physical
collections only, so the guard was false: the command exited 0 printing
"collection idx_<slug> dropped" while the physical collection and its alias both
survived. Before the rename the physical name *was* `idx_<slug>`, so the line was
correct then; the rename made it a lie.

## The fix

`src/code_indexer/cli.py` (`remove_project`) resolves first, then deletes:

1. `physical = core.store.physical_name(alias)` — the alias's target, or the
   plain name when it is a real (pre-rename) collection.
2. `core.store.collection_names()` (new) lists PHYSICAL names; every name equal
   to the alias or beginning `idx_<slug>__` is also claimed, which catches a
   collection left behind by an older embedding identity.
3. `core.store.drop_alias(alias)` (new) removes the alias **before** the
   collections, so it cannot be left dangling.
4. Each claimed collection is dropped.

The line is unchanged in spirit — one project's collection goes away — but it no
longer assumes the alias is the physical name.

What the command prints, in each case:

| state | printed |
|---|---|
| one or more collections | `removed <path>: collection <physical> dropped, registry entry deleted` (plural noun and a comma-separated list when there are several) |
| an alias with no physical collection | `removed <path>: alias idx_<slug> dropped (no physical collection), registry entry deleted` |
| nothing of the project's in the store | `removed <path>: no vector collection to drop, registry entry deleted` |

A drop is never claimed that did not happen, and the second and third forms say
so plainly.

## The tests

Added to `tests/test_lifecycle_selector.py`; the stub store there now keeps
physical collections and aliases apart (as the client does) and prunes aliases
when a collection is deleted.

| test | pins |
|---|---|
| `test_remove_project_drops_the_collection_the_alias_serves` | the physical collection behind the alias is the one dropped, the message names it, and no alias survives |
| `test_remove_project_drops_a_collection_from_an_older_identity` | the live collection and a stale identity's collection are both reclaimed, alias gone |
| `test_remove_project_with_no_collection_claims_no_drop` | no collection: exit 0, `no vector collection to drop`, no drop recorded |

## The probe

**No live services were reachable on this host** (nothing listening on the
embedding or vector-store port, no service URL in the environment), so the
re-verify's live probe was not run. Its **stub twin** was:
`runs/round2-remove-project-fix/probe_stub_remove_project.py`, capture in
`probe_stub.txt`. It drives the real command and the real `Store` adapter
against an in-memory client that keeps collections and aliases in separate name
spaces — the resolution under test is the shipped code, not a re-implementation.

```
  seeded: collections=['idx_f84ac5c3__0f0f0f0f', 'idx_f84ac5c3__dc5868ea']
          aliases={'idx_f84ac5c3': 'idx_f84ac5c3__dc5868ea'}
  $ code-indexer remove-project  ->  exit=0
    | removed <workdir>/proj: collections idx_f84ac5c3__dc5868ea,
      idx_f84ac5c3__0f0f0f0f dropped, registry entry deleted
    after remove-project: collections=[] aliases={}
    store calls: ['delete_alias idx_f84ac5c3',
                  'delete_collection idx_f84ac5c3__dc5868ea',
                  'delete_collection idx_f84ac5c3__0f0f0f0f']
    residue left behind: False

  $ code-indexer remove-project  ->  exit=0   (a project with no collection)
    | removed <workdir>/plain: no vector collection to drop, registry entry deleted
    after remove-project: collections=[] aliases={}
    store calls: []
    claimed a drop: False
```

The alias deletion precedes both collection deletions, which is the ordering the
live probe's residue line was measuring.

## Advisories — all three taken

1. `docs/subcommand-design.md` no longer cites the deleted migration hint: the
   `--tree` bullet now says a pre-v3 manifest is not read at all (every row
   surface refuses with `RebuildRequiredError`, exit 4) and there is no
   signature-less answer to render.
2. The `schema=<n>` / `rows=absent` pairing was corrected in `SKILL-CLI.md` and
   `CHANGELOG.md`: `schema=<n>` is printed whenever the manifest records an
   older revision, while `rows=absent` is the separate never-indexed verdict — a
   manifest at schema 6 carrying this build's pass stamp prints `rows=current`.
3. **Ruling: delete.** `fingerprint.rows_staleness_warning` had no production
   caller and its contract was already rendered by the staleness block; a
   callerless function whose docstring describes itself as used surface is
   worse than no function. Removed with its one unit test; the docstring of
   `current_version_stamp` no longer names it. `manifest_newer_than` and
   `VERSION_KEYS` are untouched and still used.

## Verification (fresh output, this leg)

```
680 passed, 2 deselected in 91.13s        uv run pytest tests/ -q -W error
All checks passed!                        uvx ruff check src tests scripts
publishing hygiene: scanned=611 skipped=0 violations=0
```

The suite baseline was 678; +3 new tests, −1 test whose function was deleted.

## What this leg did not do

No live vector store was touched (none reachable); no reindex, no revision bump,
no command-surface change. The untracked evidence files already in the working
tree (`runs/` captures, a QA document) were left exactly as found.
