# Round: the store reads hand over everything (issue #22)

Tip at start: `0b66431`. Work was done in the project checkout; the "before"
build ran from a base worktree at `0b66431` under `/tmp` (removed at the end
of the round).

Issue #22 closed the graph layer's caps (#21) but left the reads one layer
beneath them as bare `limit=` caps. This round makes the store hand over
everything it holds, computes `overview` hotspots instead of sampling them,
and gives `changed-symbols` the vocabulary to announce its own bounds.

## Commits

| commit | subject | signed |
|---|---|---|
| `d9ade33` | fix(graph): stop the store reads capping silently and compute overview hotspots | G |
| `c80e16d` | fix(changed): announce the impact list's own bounds | G |
| (this round doc + docs) | docs: record the store-read rule, the changed-symbols fields and the hotspot behaviour change | G |

## Reachability reproduced (read-only, `runs/issue22/probe_manifests.py`)

`refs` row counts and how many names / `(file, from_symbol)` pairs exceed the
old 200-row cap:

| project | refs | imports | names > 200 | (file, from_symbol) > 200 | `pairA` | `the` |
|---|---|---|---|---|---|---|
| vivarium-sim | 608,055 | 488 | 499 | 282 | 10,258 | 18,493 |
| b1b7f48b | 934,672 | 12 | 848 | 451 | 1 | 55,650 |
| code-indexer | 49,785 | 1,193 | 32 | 33 | 0 | 1,188 |
| calc-engine | 13,569 | 505 | 6 | 6 | 0 | 226 |
| tackboard | 19,909 | 512 | 7 | 7 | 0 | 535 |

`code-indexer` reads 49,785 rather than the 48,223 in the issue because its
watcher re-indexed the working tree during the round; the other four figures
match the issue to the row. The 1.9% claim is confirmed: `callers_of`'s
`limit=200` answered `pairA` with 200 of 10,258 rows, i.e. **1/51.29 = 1.95%**
(`calc eval '10258/200'` → 51.29).

The 200-row cap was also visible as a *false completeness claim*, which the
issue did not state: `find-callers pairA` on vivarium-sim has no definition
row, so it seeds from the reference rows and returned 200 seeds with
`truncated: false, omitted: 0`. The store's cap was the only cap on that
path, and nothing reported it.

## Disposition

**A — store reads stop capping by default. Done.**
`callers_of`, `callees_of_file_symbol` and `import_rows` take
`limit: int | None = None`; `None` means no cap. Explicit limits still work
(pinned). `import_rows` gained `ORDER BY file, line`, so a capped read is a
deterministic prefix. `callers_of_all` was deleted with the hotspot rewrite —
its only caller was the sampling bug.

**B — `overview` hotspots computed, not sampled. Done.**
New `Manifest.file_fan_in()`: one aggregate for resolved imports into a file,
one for references to a name that file defines (first definition wins, as the
symbol rows order it), self-references excluded — the same intent the old scan
had. `callers_of_all` removed.

*Behaviour change, live indexes (installed CLI, `--skip-stale-check`):*

| project | before, top file | after, top file | before, 2nd | after, 2nd |
|---|---|---|---|---|
| vivarium-sim | 184 | 19,183 | 40 | 3,627 |
| b1b7f48b | 253 | 55,503 | 76 | 16,662 |

Ratios: 104.26× and 219.38× (`calc eval '19183/184' '55503/253'`).

Independent check against a hand count of the same live manifests, all rows,
first-definition rule (`runs/issue22/verify_live_fan_in.py`):

```
vivarium-sim: files=474 same_keys=True mismatches=0 total=127740 (both)
b1b7f48b:     files=316 same_keys=True mismatches=0 total=393122 (both)
verdict: MATCH
```

**C — `changed-symbols` announces its own bounds. Done.**
Each impact entry gains `truncated: bool` and `omitted: int` (the graph
vocabulary, not a second one) covering both bounds in that path: the per-name
store read (`CALLERS_PER_NAME = 50`, kept — a diff can name many symbols) and
the shared `--max-nodes` budget. The per-name refusal count comes from one
existing aggregate (`ref_relationship_counts`), not a second row read. The
text renderer prints `impact: N more caller references not shown ...` when the
list is cut, and nothing when it is not.

**D — timing evidence. Done, no pathology found.**

Medians of three runs, cold process, `--skip-stale-check`, live indexes
(`runs/issue22/timing.py`; sanitized run logs in `timing-before.txt` /
`timing-after.txt`; the raw per-command captures stay out of the commit —
they quote other projects' file names):

| measurement | before | after | ratio |
|---|---|---|---|
| `find-callers pairA` (vivarium-sim, 608k refs) | 0.869 s | 0.887 s | 1.02× |
| `find-callers the` (b1b7f48b, 935k refs) | 0.895 s | 1.025 s | 1.15× |
| `overview` (vivarium-sim) | 0.885 s | 1.087 s | 1.23× |
| `overview` (b1b7f48b) | 0.891 s | 1.483 s | 1.66× |

Both thresholds clear: `overview` ≤ 2 s (worst 1.483 s), `find-callers` ≤ 3 s
(worst 1.025 s). No new index was needed — `idx_refs_to_name` serves the
`callers_of` read and the hotspot join scans `refs` once.

What the same commands now *say* on those indexes:

* `find-callers pairA` (vivarium-sim): before 200 roots, `truncated: false`,
  `omitted: 0`; after 40 roots (the walk's own cap), `truncated: true`,
  `omitted: 10218`.
* `find-callers the` (b1b7f48b): before `omitted: 161` (it examined only the
  200 rows the store gave it); after `omitted: 55611` — the truer lower bound
  the issue asked for.

**E — docs. Done.** `docs/json-schema.md` gains the `changed-symbols --json`
section and states that `overview` hotspots are a complete count;
`SKILL-CLI.md` drops the "still cap silently (issue #22)" caveat, gains a
"Store reads (no silent caps)" section, and the `changed-symbols` command line
names the new fields; `CHANGELOG.md` gains two `## [Unreleased]` sections, the
hotspot one carrying the before/after numbers above.

**F — tests. Done**, `tests/test_manifest_store_reads.py` (new),
`tests/test_overview.py`, `tests/test_changed_symbols.py`, `tests/test_graph.py`:

1. 250 call sites in 250 functions → `callers_of` returns all 250 with no
   argument; `find-callers --max-nodes 400` reports all 250 with
   `truncated: false`; at 40 it reports `omitted: 210`.
2. `callees_of_file_symbol` (250 out-edges, symbol and whole-file forms) and
   `import_rows` (30 rows inserted in reverse; complete and ordered; a capped
   read is the same prefix).
3. `file_fan_in` equals a hand count, on a fixture whose insertion order puts
   the large count last, and a 5,100-reference pin that a 5,000-row sample
   cannot pass.
4. `changed-symbols` reports `truncated`/`omitted` for the per-name bound and
   for the shared budget, and reports neither when the list is complete.
5. A wide graph with a generous cap → no truncation claim anywhere.

`uv run pytest tests/ -q -W error` → **490 passed, 2 deselected**;
`uvx ruff check src tests` → clean.

The pins are pinned in the failing direction too: the four test files were
copied into the base worktree, and against `0b66431` **11 of the new tests
fail** (full output: `runs/issue22/pins-fail-on-before.txt`).

## Exact live commands used

```
code-indexer overview --project vivarium-sim --skip-stale-check
code-indexer overview --project b1b7f48b --skip-stale-check
code-indexer find-callers pairA --project vivarium-sim --json --skip-stale-check
code-indexer find-callers the --project b1b7f48b --json --skip-stale-check
code-indexer skeleton src/code_indexer --project code-indexer
code-indexer find-callers callers_of --project code-indexer
code-indexer find-callers import_rows --project code-indexer
code-indexer find-callers callers_of_all --project code-indexer
code-indexer changed-symbols --project code-indexer --impact
```

Manifest probing was read-only (`sqlite3` URI `mode=ro`); no index was
mutated, no `reindex-project` and no `add-project` ran. The `code-indexer`
project's own watcher did re-index the working tree during the round (visible
as the 48,223 → 49,785 row count); that was its normal staleness pass, not a
command of this round.

## Notes and standing decisions

* **The no-definition seed path now applies the walk's own node cap.** It
  previously returned every row the store gave it — 200, unannounced. With the
  store uncapped it would have returned 10,258 nodes on `pairA`, honouring no
  cap at all. Applying `--max-nodes` there (and counting refusals into
  `omitted`) is the only reading of "the graph layer caps and announces" that
  holds on that path; it is declared in the CHANGELOG and pinned both ways.
* **First-definition rule kept.** A name defined in two files still belongs to
  the first definition in `(file, start_line)` order, as the old Python dict
  did. The live hand-count check shows the aggregate and the old intent agree
  on every file of both large indexes.
* **Out of scope, untouched:** extraction, schema, `EXTRACTION_VERSION` (still
  4), the store's row key, the graph caps' own numbers, exit codes,
  `server.py` (still a relay), and indexes (none added).

## Board

Card `T-000006` read read-only. Issue #22 was **not** closed — gate and review
come first.
