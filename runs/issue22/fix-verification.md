# Verification — the issue #22 fix range (narrow re-verify)

Scope: `58473d9..c496e7a` — seven commits, not the six the dispatch listed:
`cbcecf6` (the definition-roots fix itself) sits between the range base and
`56d4cdf` and carries the substance of the roots item. The round record lists
it; only the dispatch's summary omitted it.

Method: no gate is re-run and no dev claim is taken on trust. Every number
below comes from a command this leg ran, on one of three trees, and every
script names the tree it measured (`code_indexer.__file__` is printed by the
CLI runner, and by the store probes' import guard):

- **tip** — a checkout at `c496e7a`. While this leg was running, a review
  record landed on top of it (`79906a1`, `runs/` only); the code under test is
  byte-identical between the two, and the range is quoted as `c496e7a`
  throughout.
- **range base** — a worktree at `58473d9`, used for revert sensitivity and
  for the "before" half of the timing comparison.
- **prefix base** — a worktree at `0b66431`, the base the earlier gate used,
  whose `overview` resolved prefix ownership within the filtered set. Used only
  to read the old attribution rule.

Live indexes were never opened for write: the store probes connect
`mode=ro` and take a SQLite backup into a scratch copy, and every query
command carries `--skip-stale-check`. One disclosure at the end names the
commands that did not, and what they did (nothing).

Artifacts beside this file: `reverify/rv_roots.py`, `rv_surface_parity.py`,
`rv_prefix.py`, `rv_ties.py`, `rv_timing.py`, `rv_live_state.py`,
`run_cli.py`, `which_tree.py`, `sanitize_capture.py`,
`capture_overviews.sh`, `capture_prefix.sh`, `capture_changed.sh`,
`revert_sensitivity.sh`, and their captures (`roots-evidence.txt`,
`surface-parity.txt`, `prefix-evidence.txt`, `tie-evidence.txt`,
`timing-evidence.txt`, `live-state.txt`, `changed-evidence.txt`,
`changed-400.*`, `changed-8.*`, `suite-tip.txt`, `ruff-tip.txt`,
`pins-fail-on-base.txt`, `tip-overview-*.json`, `base-overview-*.json`).
The base worktree is passed in as `CI22_BASE`, so the scripts carry no
machine-specific path, and every capture names its tree relatively
(`[tip]` / `[base]`, then `src/code_indexer/__init__.py`). The one exception
is deliberate: `pins-fail-on-base.txt` is the pytest capture with pytest's
`tmp_path` fixture lines elided (`sanitize_capture.py`); failure names,
assertion text and counts are untouched, and the unedited run stays in this
checkout's working tree as `pins-fail-on-base-full.txt`.

## Verdict per item

| item | verdict |
|---|---|
| 1. the roots read is uncapped, and the count is real | **FAIL — on its closing clause only** (all three listing surfaces pass; one surface still answers from the 25-row read, see below) |
| 2. root truncation is announced, accounting coherent | **PASS**, with one wording defect in the docs the round wrote |
| 3. prefix scope restored against the old semantics | **PASS** (four project/prefix pairs, tip == independent aggregate, tip == base attribution rule) |
| 4. order keys are total | **PASS** (live tie counts reproduced; tie group cut inside; five identical repeats; aggregates unaffected) |
| 5. `changed-symbols` text says what it hides | **PASS** |
| 6. the impact note names the bound that cut | **PASS** (both bounds driven separately, and together) |
| 7. `fan_in_counts` is gone and unreferenced | **PASS** |
| 8. revert sensitivity | **PASS** — 10 failed / 63 passed at the base, 7 on value and 3 on a missing name |
| 9. the two previously-false doc claims are now true | **PASS** — both are scoped to the reads beneath the walks, and the roots cap is described rather than implied away |
| 10. timing after the fix | **PASS** — worst walk 2.543 s against 3 s, worst overview 1.488 s against 2 s |
| 11. no side effects | **PASS**, with one disclosure |

**Final verdict: FAIL** — item: *`get-code-context --symbol` still resolves an
exact name through the 25-row read and reports a store-held row as "not
indexed"* (9 of the 26 files that define `__init__` on this tool's own index).
Everything the round declared is fixed; this one surface is the bar's
"no surface can now silently answer from 25" clause, and it is not met.

---

## 1. The roots read — FAIL on the closing clause

The store, read directly (`reverify/rv_roots.py __init__ code-indexer`, from a
read-only snapshot):

```
code_indexer = src/code_indexer/__init__.py
symbols where name='__init__': rows=40 files=26
definitions_of          -> 40 rows, 26 files
find_symbols(limit=100) -> 40 rows
find_symbols(default)   -> 25 rows (browse-path default)
find_symbols(limit=None)-> 40 rows
definitions_of == find_symbols(limit=None): True
```

So the count is real: forty rows across twenty-six files, ordered by
`(name, start_line, file)`. The walk carries all forty (`--max-nodes 400`),
and every exact-name listing surface agrees with the store, row for row
(`reverify/rv_surface_parity.py __init__ code-indexer`):

```
store definitions_of('__init__') -> 40 rows, 26 files
find-symbol --limit 100      -> 40 rows same_set=True same_order=True
find-definition              -> 40 lines covers_every_row=True missing=[]
find-callers roots           -> 40 roots same_set=True truncated=False omitted=0
```

`same_order=True` is worth the word: the CLI's exact-name read and
`definitions_of` share one order key, so the surfaces do not merely agree on
the set, they agree on the sequence a capped read would take a prefix of.

The dotted-subject qualifier and `_definer_of` are on the same read
(`graph.py:181`, `:224`, `:245`, `:358` — all `definitions_of`), and the
`find-symbol` browse default and substring fallback stay capped by design
(`cli.py:39`, documented at `SKILL-CLI.md:509-513`).

**The closing clause.** `get-code-context --symbol` (`core.py:1207-1218`)
resolves the name with `core_manifest.find_symbols(symbol, substring=False)`
— the 25-row library default — and only then keeps `r.file == rel`. When the
requested file's row sorts past position 25, the filter sees nothing and the
command states a falsehood. One call per defining file:

```
files answered=17 files refused=9
  refused rc=1 src/code_indexer/core.py: InternalError: symbol '__init__' not indexed in src/code_indexer/core.py  [its row is #27 of 40 in the read order]
  refused rc=1 src/code_indexer/registry.py: ... [row #26]
  refused rc=1 src/code_indexer/fts.py: ... [row #29]
  refused rc=1 src/code_indexer/watcher.py: ... [row #31]
  refused rc=1 tests/test_unwatch.py: ... [row #30]
  refused rc=1 tests/test_embed_cost.py: ... [row #32]
  refused rc=1 tests/test_result_shaping.py: ... [row #36]
  refused rc=1 tests/test_watcher.py: ... [row #38]
  refused rc=1 src/code_indexer/doctor.py: ... [row #40]
```

Every refusal is a row at position 26..40 — exactly the rows the 25-row read
drops — and every one of those rows is returned by `find-symbol` and by
`find-definition` on the same index. The index holds them; the surface says it
does not.

Three things must be said plainly about this item:

- **It is not a regression from this range.** `core.py` does not appear in
  `git diff --name-only 58473d9..c496e7a`, and the line is unchanged since
  `2026-10-03`.
- **It is not a silent short answer.** It fails loudly, exit 1, which is why
  one could read the bar's "silently" as satisfied. That reading does not
  survive the fact: the answer a user gets is *decided by* the first 25 rows,
  and it is false. The gate cannot certify "no surface answers from 25" while
  a surface answers from 25.
- **It is the same defect family, on a surface the round did not enumerate.**
  The round declared three exact-name surfaces (`find-symbol`,
  `find-definition`, and the roots/qualifier/definer reads); this is a fourth,
  reached from the reading side rather than the listing side.

Repro, expected vs actual, and a direction for the owning developer are in
§What goes back. The review round reached the same finding independently and
ruled it a separate issue that does not reopen the graph-command question; the
bar under adjudication here is broader than that question, and it is this
leg's job to say so.

## 2. Root truncation — PASS

`--max-nodes` swept below, at, and above the root count on the live index:

```
--max-nodes 10 : roots=10 truncated=true omitted=124
  notes=["40 definition roots exist for this subject, node cap 10 — 30 roots not shown; raise --max-nodes",
         "node cap 10 reached — 124 more reference edges not shown (a lower bound: ...); raise --max-nodes to see more"]
--max-nodes 25 : roots=25 truncated=true omitted=124
  notes=["40 definition roots exist for this subject, node cap 25 — 15 roots not shown; raise --max-nodes",
         "node cap 25 reached — 124 more reference edges not shown ..."]
--max-nodes 40 : roots=40 truncated=true omitted=124
  notes=["node cap 40 reached — 124 more reference edges not shown ..."]
--max-nodes 400: roots=40 truncated=false omitted=0 notes=[]
```

The accounting is coherent and matches the code: the roots consume the node
budget, so a root-capped answer leaves no edge slots and every distinct
reference site is refused and counted — `omitted` is 124 (the subject's
distinct sites) and never includes the 15 lost roots. A generous cap produces
no note and claims nothing. The note text is identical in the tree and in the
JSON.

The docs say exactly this, in all three places: `SKILL-CLI.md:337-345`
(roots read every definition, spend the node budget, root refusals are not
`omitted`), `docs/json-schema.md` (`truncated` is the completeness flag; a
root-capped list is not counted in `omitted`), and `CHANGELOG.md` ("a second,
documented instance of `truncated: true` with `omitted: 0`" — see the wording
defect below, which is about that example, not about the rule).

**Wording defect (minor, doc, same round's text):** `SKILL-CLI.md:343-345`
and `docs/json-schema.md` present `omitted: 0` as the shape of a root-capped
answer. On this index it is not: a root-capped answer has `omitted: 124`,
because the cap is already spent on roots when the edges are examined.
`omitted: 0` with a cut root set is reachable only for a subject with no
reference rows at all. The rule they state is right; the example they attach
to it is wrong in the ordinary case.

**Record defect (minor, doc):** `runs/issue22/fix-round.md:40` prints
`--max-nodes 10 : roots 10, truncated true, omitted 0 (root cut, no edges
examined)` with one note. The capture committed in the same range
(`after-roots-find-callers-code-indexer-max-nodes-10.json`) records
`omitted: 124` and two notes, and the code agrees with the capture. The
prose is stale; the reviewer's record names the same thing.

## 3. Prefix scope — PASS

Per-file fan-in under `--path-prefix`, computed three ways on four
project/prefix pairs: an independent aggregate whose ownership is the first
definition among the prefix's files (`reverify/rv_prefix.py`), the tip CLI's
`overview --json`, and the pre-repair `overview` at `0b66431`, whose row scan
built its name→file map from the filtered file set
(`overview.py:174-190` at that base) — i.e. the old rule, not a third one.

```
vivarium-sim  docs/               files=324  tip_total=97,937   global_rule_total=97,405   recovered=532
               top: docs/verification/adr-0037-pawn-set-registration-round-2026-10-11.md 19,183
               tip_matches_scoped_aggregate=True
               base (0b66431) top10 counts are sampled: 184, 40, 36, ... — same top file
vivarium-sim  docs/verification   files=315  tip_total=93,623   global_rule_total=87,579   recovered=6,044
               top: same file 19,277   tip_matches_scoped_aggregate=True
               base top: 186, 40, 35, ... — same top file
b1b7f48b      docs/               files=425  tip_total=381,992  global_rule_total=361,697  recovered=20,295
               top: docs/layer-c/adr-0037-pawn-set-registration-close-review-...md 55,503
               tip_matches_scoped_aggregate=True
               base top: 253, 76, 62, ... — same top file
b1b7f48b      docs/adr/           files=37   tip_total=62,436   global_rule_total=45,540   recovered=16,896
               top: docs/adr/adr-0038-...md 7,761   tip_matches_scoped_aggregate=True
               base top: 76, 42, 33, ... — same top file
```

`tip_matches_scoped_aggregate=True` on all four means the CLI's numbers equal
the independent aggregate exactly — files, counts and order, top ten each.
The recovered totals (532, 6,044, 20,295) reproduce the round's declared
figures, and the earlier gate's 6,044 on `docs/verification` is the same
number.

Agreement with the base's *attribution rule*, not its counts: at `0b66431`
`defined_files` is filled from `all_symbols()` (`ORDER BY file, start_line`)
restricted to the filtered files, so the first definition in
`(file, start_line)` order **inside the prefix** owns the name
(`overview.py:174-190` there). The tip resolves `MIN(file)` over symbols
inside the same prefix (`manifest.py:568-605`). For a name defined in two
files both pick the lexicographically first; for a name defined twice in one
file both pick that file — the same rule, reached by two formulations. The
independent aggregate in this probe is written to the base's rule, and it is
the tip's number on all four pairs, which is how the two rules are shown to
agree in effect rather than only on inspection. The whole-store rule differs
on 6, 16, 19 and 10 names respectively — exactly the names whose owner was
outside the prefix — and those rows are the recovered totals above. The
base's counts are a 5,000-row unordered sample of `refs` and cannot be
compared numerically; its top file agrees with the tip's on all four pairs,
which is what that sample can support.

Scoped totals exceed unscoped ones by construction (the recovered rows), and
the unscoped aggregate is unchanged by the fix: `overview` with no prefix at
the tip still answers 19,183 / 3,627 (vivarium-sim) and 55,503 / 16,662
(b1b7f48b), the same top-two the gate recorded.

## 4. Order keys — PASS

The added keys are supersets of their tables' primary keys (`manifest.py:74-82`
for `refs`, `:66-72` for `imports`):

- `callers_of`: `ORDER BY file, line, relationship, to_name, receiver` —
  the `refs` PK is `(file, line, to_name, relationship)`, plus `receiver`.
- `import_rows`: `ORDER BY file, line, raw_target` — the `imports` PK is
  `(file, raw_target, line)`.

Live tie counts, counted here from SQL (`reverify/rv_ties.py`), against the
round's declared figures:

```
vivarium-sim   refs     tie_groups= 73,911 rows_in_ties=494,238 rows=608,055 largest_group=657
vivarium-sim   imports  tie_groups=      3 rows_in_ties=      9 rows=    488 largest_group=4
b1b7f48b       refs     tie_groups=107,401 rows_in_ties=932,086 rows=934,672 largest_group=198
b1b7f48b       imports  tie_groups=      0 rows_in_ties=      0 rows=     12 largest_group=1
code-indexer   refs     tie_groups=  6,449 rows_in_ties= 41,179 rows= 49,785 largest_group=49
code-indexer   imports  tie_groups=    335 rows_in_ties=    729 rows=  1,193 largest_group=5
```

73,911 and 107,401 reproduce exactly; the "3 in a 488-row imports table" is
the vivarium-sim imports figure. The other rows are extra, not contradictions:
the second index's 12-row imports table has no ties, and the third index (not
in the round's probe) has both.

Fixture (`reverify/rv_ties.py`, on a copy of the code-indexer manifest): one
`(file, line)` carrying three relationships, inserted `member` → `identifier`
→ `call`, i.e. the reverse of the key order; and one `(file, line)` imports
pair inserted `pkg.zeta` → `pkg.alpha`, sorted at the head of the table so a
cap of one lands inside it.

```
callers_of() uncapped -> [(7,'call'), (7,'identifier'), (7,'member')]
callers_of(limit=2) x5 distinct_results=1 rows=((7,'call'),(7,'identifier')) is_ordered_prefix_of_whole=True cut_inside_tie_group=True
import_rows() uncapped head -> [('AA_import_probe.py','pkg.alpha'), ('AA_import_probe.py','pkg.zeta')]
import_rows(limit=1) x5 distinct_results=1 rows=(('AA_import_probe.py','pkg.alpha'),) is_ordered_prefix_of_whole=True
```

Five repeats per cap, one distinct result each, each an ordered prefix of the
uncapped read, and in both cases the answer is the key's order, not insertion
order. That is the property the old `ORDER BY file, line` did not have.

Uncapped consumers are unaffected because they never depended on the key:
`file_fan_in` is one `GROUP BY` aggregate over every row with no `ORDER BY`
and no `LIMIT`, and two consecutive calls return identical dicts; the
fixture's three inserted non-self rows land in the owning file's count (3)
regardless of the order they were inserted in.

## 5. `changed-symbols` text — PASS

Over this range, at a generous cap, the text prints the six-line preview and
counts the rest (`(+14 more)`, `(+68 more)`, `(+2 more)`); at a small cap the
same file prints `(+2 more)` and then the bound note. The JSON is unchanged in
meaning and gains one field:

```
cap 400: manifest.py callers=74 truncated=true omitted=1  bounds={"per_name":1,"node_budget":0,"max_nodes":400}
         tests/test_graph.py callers=238 truncated=true omitted=154 bounds={"per_name":154,...}
         graph.py callers=5 truncated=false omitted=0 bounds={"per_name":0,"node_budget":0,...}
cap 8:   cli.py callers=8 truncated=true omitted=12 bounds={"per_name":0,"node_budget":12,"max_nodes":8}
         graph.py callers=0 truncated=true omitted=5 bounds={"per_name":0,"node_budget":5,...}
         manifest.py callers=0 truncated=true omitted=75 bounds={"per_name":1,"node_budget":74,...}
         tests/test_graph.py callers=0 truncated=true omitted=392 bounds={"per_name":154,"node_budget":238,...}
```

`omitted == per_name + node_budget` in every entry, so the split is exact
rather than indicative. A complete entry claims nothing: `truncated=false`,
`omitted=0`, no note, and — for the five-caller case — no `(+N more)` either,
which is the negative control for the preview.

## 6. The impact note names the bound that cut — PASS

Both bounds driven separately, then together:

```
cap 400 (only the per-name read can cut):
  impact: 1 more caller references not shown — 1 refused by the per-name read cap of 50 callers per
          symbol (raise --max-nodes does not affect that bound; run find-callers <symbol> for one
          symbol's whole fan-in)
cap 8 (the shared budget cuts):
  impact: 12 more caller references not shown — 12 refused by the shared node budget of 8 (raise --max-nodes for more)
  impact: 5 more caller references not shown — 5 refused by the shared node budget of 8 (raise --max-nodes for more)
both at once:
  impact: 75 more caller references not shown — 1 refused by the per-name read cap of 50 callers per
          symbol (...); 74 refused by the shared node budget of 8 (raise --max-nodes for more)
```

The advice matches the bound every time: the per-name branch says plainly that
`--max-nodes` does not touch it, the budget branch points at the knob that
does. `docs/json-schema.md` documents `bounds` with that split.

## 7. `fan_in_counts` — PASS

`rg -n fan_in_counts src/ tests/ docs/ SKILL-CLI.md` returns one line, and it
is a test **name** that contains the words as prose
(`tests/test_overview.py:170: test_hotspot_fan_in_counts_the_whole_store_not_a_sample`).
No definition, no call, no doc reference survives; the method is gone from
`manifest.py`.

## 8. Revert sensitivity — PASS

The four updated test files were copied over a worktree at `58473d9` and run
against that base's sources, with the package path printed before the run
(`reverify/revert_sensitivity.sh`):

```
code_indexer = src/code_indexer/__init__.py
--- does the base source carry definitions_of? -> 0 (absent, as expected)
10 failed, 63 passed in 8.69s
```

That is the round's declared 10/63 exactly. Split by kind:

- **Value failures (7)** — these are the round's evidence:
  - `test_every_definition_root_reaches_the_walk`: `assert 25 == 40`
  - `test_a_capped_root_list_says_how_many_roots_exist`: `assert 25 == 10`
  - `test_find_symbol_exact_name_is_uncapped_and_honours_limit`: `assert 25 == 40`
  - `test_callers_of_order_is_total_inside_a_one_line_tie_group`:
    `At index 0 diff: (1, 'member') != (1, 'call')` — insertion order, the
    defect itself
  - `test_import_rows_order_is_total_inside_a_one_line_tie_group`:
    `'pkg.zeta' != 'pkg.alpha'` at index 0
  - `test_path_prefix_keeps_the_in_prefix_definition_of_a_shared_name`:
    `assert None == 5` — the in-prefix definition lost its five references
  - `test_text_preview_counts_the_caller_lines_it_does_not_print`:
    `assert '(+4 more)' in ...` — the rendered text stops at six lines
- **Shape failures (3, named honestly)** — they fail because a name is missing,
  not because a number is wrong, and would also pass against a stub that
  always emits the field: `test_impact_reports_the_per_name_store_bound` and
  `test_impact_reports_the_shared_node_budget` (`KeyError: 'bounds'`), and
  `test_definitions_of_returns_every_definition_row`
  (`AttributeError: 'Manifest' object has no attribute 'definitions_of'`).

The tie pins matter most for this round's honesty: they insert their rows in
the reverse of the key order, so a plan-order answer cannot pass by luck, and
the base's failure text shows exactly the insertion order the old key leaked.

## 9. The two previously-false doc claims — PASS

Both claims are now scoped to the reads beneath the three graph walks, and
every read under those walks is uncapped by default at the tip:

- `callers_of` — `limit: int | None = None` (`manifest.py:521`), used without
  a limit at `graph.py:149`, `:258`;
- `callees_of_file_symbol` — `limit: int | None = None` (`manifest.py:548`),
  used without a limit at `graph.py:173`;
- `import_rows` — `limit: int | None = None` (`manifest.py:607`), used without
  a limit at `graph.py:155`, `:454` (`deps`);
- `definitions_of` — no limit parameter at all (`manifest.py:330`), used at
  `graph.py:181`, `:224`, `:245`, `:358`.

`CHANGELOG.md` says "No store read **under these walks** caps silently any
more" — true as scoped. `SKILL-CLI.md:334-336` says "The store reads beneath
these walks cap nothing at all" — true as scoped. The roots cap is described,
not implied away: population, cap, hidden count and the knob, in the same
sentence as the note that omits it from `omitted`.

The one place the round's own prose is not exactly true is the wording defect
in §2 (`omitted: 0` as the shape of a root-capped answer) and the stale
capture line in the round record. Neither is an over-claim of completeness —
they under-state the edge refusals — but both should be corrected with the
folds.

## 10. Timing — PASS

Cold subprocess per run, three runs, median, `--skip-stale-check`, stdout
discarded; base is `58473d9`, tip is `c496e7a` (`reverify/rv_timing.py`):

```
find-callers __init__ (code-indexer)   base 2.396s  tip 2.543s  (+0.147s)  limit 3.0  within
find-callers Compile (vivarium-sim)    base 0.896s  tip 0.893s  (-0.003s)  limit 3.0  within
find-callers pairA (vivarium-sim)      base 0.918s  tip 0.898s  (-0.020s)  limit 3.0  within
overview (vivarium-sim)                base 1.078s  tip 1.072s  (-0.006s)  limit 2.0  within
overview (b1b7f48b)                    base 1.470s  tip 1.488s  (+0.019s)  limit 2.0  within
all_within_threshold: True
```

The uncapped roots cost 0.15 s on the graph walk with 40 definitions — the
only measurable delta, and 0.46 s inside the threshold. Run twice with the
same shape (the earlier pass measured 2.398 → 2.556 and 1.489 → 1.468); the
spread is ordinary cold-start noise, and no median moves more than 0.16 s.

## 11. No side effects — PASS

- `EXTRACTION_VERSION = 4` (`graph_extract.py:95`), unchanged.
- No extraction, schema, store or server file in the range
  (`git diff --name-only 58473d9..c496e7a`): `changed.py`, `cli.py`,
  `graph.py`, `manifest.py`, `overview.py`, three docs, four test files,
  `runs/`. `server.py` is untouched and still a subprocess relay
  (`server.py:69`, `:100`).
- `uv run pytest tests/ -q -W error` → `498 passed, 2 deselected in 71.25s`
  (warn-clean); `uvx ruff check src tests` → `All checks passed!` Both
  reproduced here, not quoted from the round.
- Live indexes untouched, read straight from each manifest
  (`reverify/rv_live_state.py`): vivarium-sim 608,055 refs / 488 imports,
  b1b7f48b 934,672 / 12, code-indexer 49,785 / 1,193 — identical to the
  gate's record — `extraction_version=4` on all three, and the manifest
  mtimes are unchanged (the code-indexer index still dates from before this
  leg started).
- **Disclosure.** Seven `get-code-context` calls ran without
  `--skip-stale-check` before the flag discipline was applied to them (four
  line-range reads and three `--symbol` reads). `get-code-context` is one of
  the commands whose path carries the staleness probe, so those calls were
  allowed to run it; one of them printed
  `WARNING file changed since last index; line numbers may be shifted`. No
  write followed: the manifest mtimes and every row count above are unchanged,
  so no incremental pass wrote anything. No `reindex-project`, `add-project`,
  `index-more` or `sweep-orphans` ran. Every other query command carried the
  flag, and every CLI query in the probes is invoked with it.

## What goes back

**The blocking item (bar 1's closing clause).**

`get-code-context --symbol` decides from a capped read.

- Where: `core.py:1207-1218` —
  `rows = [r for r in core_manifest.find_symbols(symbol, substring=False) if r.file == rel]`.
- Repro on the live index: `code-indexer get-code-context
  src/code_indexer/doctor.py --symbol __init__ --project code-indexer`
- Expected: the `__init__` definition at `src/code_indexer/doctor.py:398`
  (the store holds it; `find-definition __init__` prints it).
- Actual: `InternalError: symbol '__init__' not indexed in
  src/code_indexer/doctor.py`, exit 1. Nine of the twenty-six defining files
  answer this way; the refused rows are exactly positions 26-40 of the read
  order.
- Proposed failing test: a fixture with thirty classes each defining `step`,
  then `get-code-context <file-of-the-thirtieth> --symbol step` must return
  that definition's lines and must not raise. It fails at the current tip.
- Direction (not implemented here): read the file's own rows
  (`symbols_for_file(rel)` filtered by name) instead of an exact-name read
  filtered afterwards — no cap, no population question, same answer shape.

The review round reached the same finding and ruled it a separate issue that
does not reopen the graph-command question; the bar adjudicated here is wider
than that question. Which route to take — a small fold now, or a separate
issue plus a re-adjudication of this clause — is the orchestrator's call, not
this leg's.

**Non-blocking folds (doc only, all from the round's own text).**

1. `SKILL-CLI.md:343-345` and `docs/json-schema.md`: a root-capped answer's
   `omitted` is not `0` in the ordinary case — it is the number of distinct
   reference sites refused once the roots have spent the budget (124 on the
   live example). Keep the rule, fix the example.
2. `runs/issue22/fix-round.md:40`: the `--max-nodes 10` line contradicts its
   own committed capture — `omitted: 124` and two notes, not `omitted: 0` and
   one.
3. `manifest.py:531-535` (docstring): say "the primary-key column set plus the
   receiver", not "the first four columns are the row's primary key" — the
   sets match, the orders do not.

## Board

The board card (narrow re-verification of the issue #22 blocker fix) was read
read-only: status `in_progress`, claimed by the orchestrator, its dependency
satisfied. No board write was made by this leg.

Issue #22 does not close on this verdict: its five declared surfaces are clean,
but the bar's "no surface answers from 25" clause is not met until the
`get-code-context --symbol` read is fixed.
