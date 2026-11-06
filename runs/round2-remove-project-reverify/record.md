# Narrow re-verify — `remove-project` reclaims what the project owns

- Tip under test: `96b24b1` (branch `main`, pushed). The red being re-ruled is in
  `runs/round2-reverify/record.md`; the fix record is
  `runs/round2-remove-project-fix/record.md`.
- Scope: the one probe, the three advisories if they landed, and regression
  (suite, ruff, hygiene). Nothing from the eight folds is re-opened.
- **Live, not stub.** The embedding service (`<embedding>`) and the vector store
  (`<qdrant>`) both answered this leg, so the probe drove the shipped CLI against
  the real store and asked Qdrant itself what it held before, between and after
  each command. Capture: `probe_remove_project_live.txt`, from
  `probe_remove_project_live.py` (this gate's own probe).

## Verdict

**PASS.** Every bar below holds on live evidence. Two residual notes, neither a
bar failure, are at the end.

| bar | result |
|---|---|
| 1 everything the project owns is reclaimed | **pass** — live, three shapes |
| 2 the message is true in both directions | **pass** — live, both cases |
| 3 the alias never outlives its collection | **pass** — end state observed; order read in the code |
| 4 no residue in the live store | **pass** — collection and alias lists equal to pre-probe |
| 5 the three advisories | **pass** (one residual wording note) |
| 6 regression | **pass** — 680 passed, ruff clean, hygiene zero |

## Bar 1 — a pass, an older identity's leftover, and a pre-rename collection

A throwaway project registered with `--vectors on` (real embedding, real store),
then a collection was seeded under a second identity digest, then
`remove-project`. The store was listed at every checkpoint:

```
  after the pass: collections=['idx_<slug>__dc5868ea']
                  aliases={'idx_<slug>': 'idx_<slug>__dc5868ea'}
    points in idx_<slug>__dc5868ea: 2
    seeded a leftover from an older identity: idx_<slug>__0f0f0f0f (size=4096, 0 points)
  before remove-project: collections=['idx_<slug>__0f0f0f0f', 'idx_<slug>__dc5868ea']
                         aliases={'idx_<slug>': 'idx_<slug>__dc5868ea'}
  $ code-indexer remove-project <workdir>/proj  ->  exit=0
    | removed <workdir>/proj: collections idx_<slug>__dc5868ea, idx_<slug>__0f0f0f0f dropped, registry entry deleted
  after remove-project: collections=[] aliases={}
```

The physical collection behind the alias and the leftover both went. The third
shape is the pre-rename one — the store rebuilt into a single plain collection
named `idx_<slug>` with no alias, which is what an index built before the
identity rename holds:

```
    slug=<slug> collections=['idx_<slug>'] aliases={}
  $ code-indexer remove-project <workdir>/oldstyle  ->  exit=0
    | removed <workdir>/oldstyle: collection idx_<slug> dropped, registry entry deleted
    after remove-project: collections=[] aliases={}
```

## Bar 2 — the message says only what happened

Two directions, judged against the store's own before/after lists rather than
the command's word:

- collections existed — the printed line names both and the set of named
  collections equals the set that disappeared (`claimed drops == collections that
  disappeared: True (2 collection(s))`);
- no collection existed (a `--vectors off` project) — `removed <workdir>/plain: no
  vector collection to drop, registry entry deleted`, exit 0, and the string
  `dropped` appears nowhere in the output.

## Bar 3 — the alias never outlives its collection

The end state is unambiguous: `aliases={}` at every post-delete checkpoint. What
the end state alone cannot tell is whether the deleter's alias-first order is
load-bearing, because Qdrant may clean a pointing alias itself. Measured on a
throwaway pair (not on the project under test):

```
    alias zzorderaliasprobe_alias -> zzorderaliasprobe_live before the collection delete
    alias zzorderaliasprobe_alias -> None after the collection delete
    conclusion: Qdrant removes a pointing alias by itself, so alias-first is
                not load-bearing for the end state
```

So the ordering is belt-and-braces on this store build, and the code does it in
that order anyway (`cli.py`, `remove_project`): `physical_name(alias)` resolves
first, the claim list is built, `drop_alias(alias)` runs, and only then the
collections are dropped. The order is observable to the caller in one place that
matters: `alias_dropped` is what separates the "alias dropped (no physical
collection)" line from "no vector collection to drop", so the alias deletion
cannot be moved after the collection deletions without breaking that message.

## Bar 4 — no residue

```
  store before probe: collections=13 aliases=9
  store after probe:  collections=13 aliases=9
  [ok] the store's collection list is back to its pre-probe state
  [ok] the store's alias list is back to its pre-probe state
```

The two lists are equal element-for-element, not merely equal in length (the full
lists are in the capture). Both throwaway projects are unregistered:
`list-projects --json` after the delete printed `{"schema": 1, "projects": [] …}`,
and `index-status`'s state directory was a scratch tree removed at the end.

## Bar 5 — the three advisories

1. **Design doc — the deleted hint is no longer cited.** `rg MIGRATION_HINT` finds
   no hit in `docs/`, `SKILL-CLI.md` or `CHANGELOG.md`; the `--tree` bullet now
   says a pre-v3 manifest is not read at all (every row surface refuses with
   `RebuildRequiredError`, exit 4) and there is no signature-less answer to
   render. See the residual note below for one stale test-plan bullet.
2. **`schema=<n>` / `rows=absent` now matches the code.** Read against
   `cli.py` (`index-status`): `rows=` comes from `core.vectors_status` →
   `vectors.rows_current`, which reads exactly two facts — `meta.last_indexed`
   exists and `meta.extraction_version` equals this build's — and never the
   manifest's recorded schema; `schema=` is appended separately from
   `core.recorded_schema_version`. So a manifest at an older revision carrying
   this build's stamp prints `rows=current … schema=<n>`, exactly as
   `SKILL-CLI.md` and `CHANGELOG.md` now state, and `schema=<n>` no longer
   implies `rows=absent`.
3. **`rows_staleness_warning` is gone, with its test, and no caller remains.**
   `rg rows_staleness_warning src tests` returns nothing (only the two CHANGELOG
   entries that record the removal); `VERSION_KEYS` and `manifest_newer_than`
   survive and `indexer.py:229` still calls the latter.

## Bar 6 — regression

Fresh output this leg, full output under `runs/round2-remove-project-reverify/`:

```
680 passed, 2 deselected in 88.54s    uv run pytest tests/ -q -W error   (pytest-full.txt)
All checks passed!                    uvx ruff check src tests scripts   (ruff.txt)
publishing hygiene: scanned=617 skipped=0 violations=0                    (hygiene.txt)
```

The baseline was 680 passed, 2 deselected; the count is unchanged. The hygiene
scan covers the tracked tree including this leg's three new files.

## Residual notes (not bar failures)

1. **One stale bullet survives in the same design doc.**
   `docs/subcommand-design.md` no longer cites `core.MIGRATION_HINT`, which is the
   advisory as written. But its edge-case list for the planned `skeleton` /
   `outline` tests still reads "pre-v3 manifest (hint, no crash)", and the landed
   behaviour is a `RebuildRequiredError` refusal with exit 4 and no hint. The
   bullet is a test plan for a proposal, not a statement about the shipped
   command, so it does not fail the advisory — it is the same class of stale
   wording, one section deeper.
2. **The third message form is unreachable in practice.** `alias ... dropped (no
   physical collection)` requires an alias whose target does not resolve, but
   `Store.physical_name` returns an alias's `collection_name` whenever the alias
   exists, and Qdrant will not create an alias without a collection — so `targets`
   is non-empty whenever the alias is. The branch is harmless defensive shape;
   named here so a later reader does not mistake it for a tested path.

## What this leg did not do

- No source file edited; this directory's record, probe and captures are the only
  additions.
- No reindex and no revision bump: the live index on this checkout still refuses
  row surfaces until the post-round reindex, so exploration used `rg` and direct
  reads rather than indexed row queries.
- The live probes created collections on the shared store and deleted them; the
  residue check above is the proof, not an intention.
