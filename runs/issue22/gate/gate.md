# Verification gate — the store reads hand over everything (issue #22)

Scope: base `0b66431` → code tip `1139a1e`. Two documentation commits landed
after the tip (`def499e`, `5ee5154`); `git diff --stat 1139a1e 5ee5154` touches
only `runs/`, so every measurement below describes the tip's code.

Method. Live manifests were never opened for write: each probe copies the
database with sqlite's backup API from a `mode=ro` connection and calls the
store reads on the copy. Base comparisons run from a separate worktree at
`0b66431` with `PYTHONPATH` pointed at it, and every run prints
`code_indexer.__file__` so a measurement cannot silently describe the wrong
tree. Scripts are committed beside this file under `runs/issue22/gate/`.

## Verdicts

| Gate item | Verdict |
|---|---|
| 1. The store reads no longer cap by default | **PASS** |
| 2. Every remaining bound on the query path is ruled | **FAIL** — two blocking, three minor |
| 3. Hotspots equal independently computed ground truth | **PASS** |
| 4. The declared behaviour change is the fix, and it is true | **PASS** |
| 5. `changed-symbols` announces both of its bounds | **PASS** (text wording defect filed under item 2) |
| 6. The no-definition seed path caps and announces | **PASS as delivered** — declared, pinned both ways |
| 7. Negative controls claim nothing | **PASS** |
| 8. The new pins fail on base | **PASS** — 11 fail; 6 on value, 5 on shape |
| 9. Timing reproduced against the thresholds | **PASS** |
| 10. No side effects | **PASS** with one disclosure |

**Final verdict: FAIL.** The issue's filed symptom — a store read answering
with a silent fraction of the truth and calling it complete — is still live
one layer up, on the walk's definition roots. The round returns.

---

## 1. The store reads no longer cap by default — PASS

`probe_store_reads.py SRC SLUG` (`runs/issue22/gate/probe_store_reads.py`),
against read-only snapshots of the two measurement indexes.

```
### tip, vivarium-sim
callers_of('pairA') default=10258 limit=200=200
callees_of_file_symbol(Vivarium.Sim/RuleSetLoader.cs, Load): ground=393 default=393 limit=200=200
callees_of_file_symbol(<verification doc>, None): ground=32681 default=32681 limit=200=200
import_rows() default=488 ground(imports)=488
import_rows(limit=5) repeated_identical=True ordered_by_file_line=True
  capped is prefix of whole=True

### base, vivarium-sim
callers_of('pairA') default=200 limit=200=200
callees_of_file_symbol(Vivarium.Sim/RuleSetLoader.cs, Load): ground=393 default=200 limit=200=200
callees_of_file_symbol(<verification doc>, None): ground=32681 default=200 limit=200=200
import_rows() default=488 ground(imports)=488
import_rows(limit=5) repeated_identical=True ordered_by_file_line=False
```

Same shape on `b1b7f48b` (a whole-file fan-out of 10,549 rows: default 10,549
at the tip, 200 at base). An explicit `limit` still bounds the read, and at the
tip the bounded read is the ordered prefix of the whole read; at base it is an
arbitrary slice. All three reads are proven uncapped at the source, not merely
at the CLI.

Honest limit of this evidence: the largest `imports` table on any registered
index is 1,193 rows, so the old 5,000-row default cannot be reached live. What
is shown live is that the default equals the table count and that the order
defect existed; the cap-vs-whole distinction for imports is exercised through
the explicit limit.

## 2. Every remaining bound on the query path — FAIL

`rg -n "limit" src/` plus every slice in the four files was ruled one by one.

### Blocking — the walk's definition roots are capped at 25 and uncounted

`graph.py:224` builds its roots from `Manifest.find_symbols(target,
substring=False)`, whose signature is `limit: int = 25` and which applies
`LIMIT ?` unconditionally — including on an exact-name lookup.

```
$ <python> runs/issue22/gate/def_counts.py code-indexer __init__
symbols where name='__init__': rows=40 files=26

$ code-indexer find-callers __init__ --project code-indexer --json --skip-stale-check
{"roots":25,"def_roots":25,"truncated":true,"omitted":109,...}

$ code-indexer find-callers __init__ --project code-indexer --json --max-nodes 500 --skip-stale-check
{"roots":25,"def_roots":25,"truncated":false,"omitted":0}

$ code-indexer find-callees __init__ --project code-indexer --json --max-nodes 500 --skip-stale-check
{"roots":25,"def_roots":25,"truncated":true,"omitted":0}

$ code-indexer find-symbol __init__ --project code-indexer --json --limit 100 --skip-stale-check
{"n":25}
```

40 definition rows exist; the walk answers from 25; `omitted` counts nothing
for the 15 lost roots, and with a generous node cap the answer reports
`truncated: false` — a complete answer that is not complete. That is the
symptom this issue was filed for, one layer above the reads it repaired.
`find_symbol` compounds it: exact-name mode never forwards `--limit`, so no
surface can list all 40 definitions.

### Blocking — a prefix-scoped overview loses reference rows

`file_fan_in()` resolves each name's owning file over the whole store
(`MIN(file)`), and `build_overview` then filters the result to `--path-prefix`.
A name whose first definition sits outside the prefix contributes nothing to
the in-prefix file that also defines it.

```
$ <python> runs/issue22/gate/prefix_fan_in.py vivarium-sim docs/verification
refs_total=608055
A shipped rule (global first definition, then filter): 87579
B prefix-scoped first definition:                     93623
rows lost by the shipped rule: 6044
names defined under the prefix: 258

$ code-indexer overview --project vivarium-sim --path-prefix docs/verification --skip-stale-check --json
top fan-in file: 19183   (base at the same prefix: 186)
```

The previous code resolved the first definition *within the filtered set*, so
this is a regression introduced by the aggregate, not an inherited quirk. On
this prefix it silently drops 6,044 rows. Unscoped overview is correct (item 3);
scoped overview is not, and nothing in the payload says it is a partial count.

### Minor — `changed-symbols` text shows 6 of N caller lines with no signal

`changed.py:344` renders `imp["callers"][:6]`. The JSON carries all 27 callers
and `omitted: 0`; the text prints six:

```
$ code-indexer changed-symbols --project code-indexer --base 0b66431 --head 1139a1e --impact --skip-stale-check
modified src/code_indexer/manifest.py / function callers_of:498-515
...
  impact: runs/issue21/round.md (heuristic)
  impact: runs/issue21/round.md (heuristic)
modified src/code_indexer/overview.py / lines 174-178
$ <count of impact lines in that block>: 6
```

The same renderer announces its other preview (`other files: ... (+N more)`),
so the omission here is an inconsistency, not a house style. One line fixes it.

### Minor — `ORDER BY file, line` is not a total order

Ties inside the ordering key mean a capped read is reproducible only by
iteration luck:

```
$ <python> runs/issue22/gate/order_ties.py vivarium-sim b1b7f48b code-indexer
vivarium-sim: tied (to_name,file,line) groups=124 rows_in_ties=248
b1b7f48b: tied (to_name,file,line) groups=54 rows_in_ties=108
code-indexer: tied (to_name,file,line) groups=13 rows_in_ties=26
```

Repeated runs agreed in my captures, but the key does not guarantee it. A
third sort column (`relationship`) would.

### Minor — the impact note names the wrong remedy

With `--max-nodes 400` the per-name read is the only bound left cutting
(154 rows), yet the advice is "raise --max-nodes for more", which cannot
recover a single one of them. The note should name the bound that cut.

### Note, not a cap — dead helper

`Manifest.fan_in_counts` has no caller (`rg -n fan_in_counts src tests` finds
only its definition). `callers_of_all` was removed this round; this near-twin
was missed. Not a silent cap, just residue.

### Ruled non-caps

Documented, user-facing, flag-bearing limits: `--limit` on search, find-symbol
browse mode and find-references; `--max-nodes` / `--depth` on the walks;
`overview --max-lines`; ranking budgets in `core.py`/`ranking.py` (search
result shaping, with `dropped`/`truncated` in the payload); top-N hotspot and
language lists in `overview` (a ranking, and the JSON carries the full
aggregate). `changed-symbols`' own two bounds are announced (item 5).

## 3. Hotspots equal independently computed ground truth — PASS

`fan_in_truth.py SLUG OVERVIEW_JSON` computes fan-in from its own SQL: resolved
imports into a file, plus references whose `to_name` is defined in that file
(first definition by `(file, start_line)` via `ROW_NUMBER`), self-references
excluded — a formulation independent of `file_fan_in()`'s `MIN(file)` aggregate.

```
vivarium-sim: files_with_fan_in=474 total=127740 imports_rows=0 ref_rows=127740
b1b7f48b:     files_with_fan_in=316 total=393122 imports_rows=0 ref_rows=393122
identical_top10=True   (both projects; every row equal in count and file)
```

Top-10, vivarium-sim (independent = overview): 19,183 · 3,627 · 2,359 · 2,285 ·
2,265 · 2,248 · 2,235 · 2,049 · 1,949 · 1,937. Top-10, b1b7f48b: 55,503 ·
16,662 · 11,677 · 11,175 · 7,761 · 7,514 · 7,345 · 6,753 · 5,681 · 5,515.
Base overview on the same indexes answers 184 and 253 for the top file, so a
sample cannot produce these lists.

The import half of the aggregate contributes 0 on both indexes (their resolved
imports into files are all self-references or unresolved), so this live check
evidences the reference half; the import half is covered by the fixture pins.

## 4. The declared behaviour change is the fix, and it is true — PASS

`CHANGELOG.md` names the change with numbers: top file **184 → 19,183**
(second **40 → 3,627**) and **253 → 55,503** (second **76 → 16,662**), and says
plainly that any earlier hotspot ranking was a sample. Item 3 shows the new
numbers are the true counts; the base runs reproduce 184, 40, 253 and 76
exactly. Declared and correct.

## 5. `changed-symbols` announces both bounds — PASS

```
$ code-indexer changed-symbols --project code-indexer --base 0b66431 --head 1139a1e \
    --impact --max-nodes 400 --json --skip-stale-check
tests/test_changed_symbols.py: callers=238 truncated=true omitted=154
tests/test_graph.py:           callers=130 truncated=true omitted=262
src/code_indexer/changed.py:   callers=5   truncated=false omitted=0
tests/test_overview.py:        callers=0   truncated=false omitted=0

$ code-indexer changed-symbols ... --impact --json      (default --max-nodes 40)
tests/test_changed_symbols.py: callers=8 truncated=true omitted=384
tests/test_graph.py:           callers=0 truncated=true omitted=392
```

`check_changed_bounds.py` recomputes both bounds from the store for those
files: population 392, rows the per-name reads hand over 238 — payload
`callers=238 omitted=154` at a generous cap (per-name bound alone) and
`callers=8 omitted=384` at the shared budget. Both counts are exact against
the store, not merely non-zero, and a complete entry (`tests/test_overview.py`,
population 0) claims nothing. The text note wording defect is item 2e.

## 6. The no-definition seed path — PASS as delivered

```
$ code-indexer find-callers pairA --project vivarium-sim --json --skip-stale-check
{"roots":40,"truncated":true,"omitted":10218,
 "notes":["the index holds no definition for 'pairA' ... seeding from reference rows ...",
          "node cap 40 reached — 10218 more reference edges not shown ..."]}
$ ... --max-nodes 200
{"roots":200,"truncated":true,"omitted":10058}
$ ... --max-nodes 11000
{"roots":10258,"truncated":false,"omitted":0}
```

10,258 stored rows; the generous cap returns all of them and claims nothing.
This is the correct reading of the contract: a cap the user raises with
`--max-nodes`, announced when it cuts, and a lower bound that is exact for the
seeds it refused. The alternative — returning 10,258 nodes at the default
budget — would break the walk's one budget. Declared in `CHANGELOG.md` (the
200-node answer that used to claim completeness is named) and pinned in both
directions by `test_seeded_reference_list_applies_and_announces_its_node_cap`.
**Acceptable as delivered; no separate issue.**

## 7. Negative controls — PASS

```
$ code-indexer find-callers _cfg --project code-indexer --json --skip-stale-check
{"roots":2,"truncated":false,"omitted":0,"notes":null}
$ code-indexer find-callers no_such_symbol_zzz42 --project code-indexer --json --skip-stale-check
{"roots":0,"truncated":false,"omitted":0,"notes":[<no definition>, <no reference rows>]}
```

A small complete answer claims nothing; an unknown name is not dressed up as
truncation.

## 8. Revert sensitivity — PASS, with the shape pins named

The four test files were copied into the base worktree and run against
`0b66431` sources (`PYTHONPATH` verified to resolve there):

```
11 failed, 54 passed in 6.90s
```

Real value failures (6): `callers_of` 200 vs 250; `callees_of_file_symbol`
200 vs 250; `import_rows` order (`pkg/mod_29.py` first, not `pkg/mod_00.py`);
`file_fan_in` hotspot 4,986 vs 5,102; `find-callers` 200 of 250 call sites;
seeded reference list 200 roots where the cap asks for 40.

Shape-only failures (5, named honestly — they fail because a field or method
is absent, not because a number is wrong): `test_file_fan_in_matches_a_
manual_per_file_count` and `test_file_fan_in_does_not_stop_at_a_sample`
(`AttributeError: 'Manifest' object has no attribute 'file_fan_in'`),
`test_impact_reports_the_per_name_store_bound` (`AttributeError:
CALLERS_PER_NAME`), and the two `--impact` pins that read `payload["truncated"]`
(`KeyError: 'truncated'`). The two pins that decide this round — the
>200-call-site store read and the hotspot aggregate — fail on value.

## 9. Timing reproduced — PASS

`timing.py`, cold subprocess per run, three runs, median, `--skip-stale-check`,
base and tip side by side:

```
find-callers pairA (vivarium-sim)  base 0.871s  tip 0.879s   limit 3.0s
find-callers the (b1b7f48b)        base 0.923s  tip 1.026s   limit 3.0s
overview (vivarium-sim)            base 0.882s  tip 1.075s   limit 2.0s
overview (b1b7f48b)                base 0.887s  tip 1.478s   limit 2.0s
all_within_threshold: True
```

The round's own figures reproduce within noise (worst overview 1.478 s against
2 s; worst walk 1.026 s against 3 s).

## 10. No side effects — PASS, one disclosure

- `EXTRACTION_VERSION = 4` (`src/code_indexer/graph_extract.py:95`), unchanged.
- No extraction, schema, store or server file in the diff
  (`git diff --name-only 0b66431 1139a1e`): `changed.py`, `graph.py`,
  `manifest.py`, `overview.py`, three docs (`CHANGELOG.md`, `SKILL-CLI.md`,
  `docs/json-schema.md`), tests and `runs/`. `server.py` is untouched and still
  a relay.
- `uv run pytest tests/ -q -W error` → `490 passed, 2 deselected in 71.62s`
  (warn-clean); `uvx ruff check src tests` → `All checks passed!`.
- Every command run for this gate exited 0.
- Live indexes untouched, verified by read-only counts: `vivarium-sim`
  608,055 refs / 488 imports, `b1b7f48b` 934,672 / 12, `code-indexer`
  49,785 / 1,193 — identical to the round record; `extraction_version=4` on
  all three.
- **Disclosure:** two commands ran without `--skip-stale-check` before the flag
  discipline was in place — `python -m code_indexer --help` and
  `python -m code_indexer overview ...`. `python -m code_indexer` runs the MCP
  server entry point, which reads stdin and exits 0; it printed nothing and
  started no index pass. Every other query command carried
  `--skip-stale-check`. No `reindex-project`, `add-project` or `sweep-orphans`
  ran.

## What goes back

Blocking, for the owning developer:

1. **Definition roots.** `find_symbols` applies `LIMIT 25` to exact-name
   lookups, and `find-callers` / `find-callees` take their roots from it, so a
   name with 40 definitions answers with 25 and reports a complete answer
   (`omitted: 0`). Either the roots read stops capping (and the walk's own node
   budget applies, counted), or the root shortfall is counted like every other
   refusal. Requires a pin: a fixture name with more than 25 definitions must
   yield every definition, with the cap, if any, announced.
2. **Prefix-scoped fan-in.** `file_fan_in()` must resolve first definitions
   over the same file set the overview reports on, or the scoped overview must
   say its counts are global. Requires a pin: a prefix-scoped overview on a
   fixture whose name is defined inside and outside the prefix.

Minor, same round if cheap: the six-line text preview, the non-total
`ORDER BY`, the misleading remedy in the impact note, the dead
`fan_in_counts`.

## Board

The board card (verification gate for the issue #22 store-read repair) was read
read-only. Issue #22 stays open: this gate is a FAIL.
