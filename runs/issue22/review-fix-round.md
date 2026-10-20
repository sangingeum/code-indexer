# Review — issue #22 fix round (narrow re-verify)

Scope: `58473d9..c496e7a` (seven commits; the dispatch listed six — `cbcecf6`
is in the range and is the substance of item 1, and the round record lists it,
so nothing is missing from the record, only from the dispatch list).
Base for "before": `58473d9`; older comparisons where the brief asks for them
(`1139a1e`). Review-only: no code changes in this leg.

My earlier findings at `5ee5154` stand as written and are not re-litigated here.
This leg judges only the changed surfaces, against the eight rules in the
dispatch. Butler evidence is of record and was not re-run except where a claim
was verifiable in one live read.

## Verdict per item

| # | item | verdict |
|---|---|---|
| 1 | the root-cap contract | **pass** — semantics coherent, `truncated` precedence preserved, two-note case reads clearly; one wording gap (F5) |
| 2 | the prefix fix restores the OLD rule | **pass** — scoped ownership asks the old question; unscoped aggregate is byte-identical to `1139a1e` and `58473d9` |
| 3 | total order keys | **pass** — all three keys are supersets of their table's PK; the docstring's phrasing is off (F3) |
| 4a | `find-definition` fixed in the same leg | **pass** — right call, not scope creep |
| 4b | `core.py:776` left as-is | **pass, with a correction to the stated ground** — sound, but because of an invariant nobody wrote down (F6), not because it is not a walk |
| 5 | one voice still holds | **pass** — one vocabulary; one advice string over-promises (F4) |
| 6 | the two doc claims are now true | **pass, with folds** (F1, F2) |
| 7 | test quality for the new pins | **pass, with two gaps** (F9 two-note case, F10 browse default) |
| 8 | can #22 close? | **yes on its five surfaces** — with a same-class defect named that is *not* in this range (see below) |

## Evidence for the load-bearing claims

Root cap, live at the tip (`find-callers __init__ --project code-indexer
--max-nodes 400 --json`):

```
roots 40, truncated false, omitted 0, notes null
```

Which is the filed symptom closed: the store holds 40 `__init__` rows across
26 files and the walk is handed all 40. `find-symbol __init__ --json` returns
the same 40 rows; ranks 26-40 exist, so the old 25-row read was cutting a real
tail, not padding.

Roots consume the budget, checked in code rather than only in output:
`graph.py:282-285` seeds `count = len(result)` with `result` already holding
the roots, and `graph.py:299` / `graph.py:375` refuse at `count >= max_nodes`.
A root set at the cap therefore leaves zero edge slots — which is exactly what
the `--max-nodes 10` capture shows (`roots 10, omitted 124`) and what a
`--max-nodes 0` run would show as a cut root set with no edges at all.

Unscoped `file_fan_in` unchanged: at `1139a1e` (`manifest.py:544-562`) the two
aggregates are `SELECT resolved_file, COUNT(*) … WHERE resolved_file IS NOT
NULL AND resolved_file != file GROUP BY resolved_file` and `… JOIN (SELECT
name, MIN(file) AS file FROM symbols GROUP BY name) s ON s.name = r.to_name
WHERE r.file != s.file GROUP BY s.file`. At the tip (`manifest.py:584-605`)
the unscoped branch rebuilds those two strings byte for byte and only appends
`substr(...)` clauses when `scope_prefix` is set. No third rule.

Scoped ownership is the old rule: `MIN(file)` among symbols *inside the
prefix* is the file attribute of the first definition in `(file, start_line)`
order among the filtered files — a tie inside one file resolves to that same
file either way. The prefix comparison is `substr(file, 1, len) = prefix`,
the same rule the caller applies at `overview.py:60` (`p.startswith`), and
deliberately not `LIKE` (case-folding would scope by a different rule).

Two-note case, live text at `--max-nodes 10`:

```
note: 40 definition roots exist for this subject, node cap 10 — 30 roots not shown; raise --max-nodes
note: node cap 10 reached — 124 more reference edges not shown (a lower bound: a refused edge is
      counted but never expanded, so its own edges are not walked, and a depth cap on the same
      answer can cut more); raise --max-nodes to see more
```

It reads as two facts about one knob, not as noise: the first line is
population-vs-shown for roots, the second is refusals among the edges the walk
actually examined. The precedence sentence (`truncated` over `omitted`, two
reachable cases) is stated in `SKILL-CLI.md`, `CHANGELOG.md` and
`docs/json-schema.md` with the same words.

## Findings

| id | severity | where | what |
|---|---|---|---|
| F1 | minor (doc) | `runs/issue22/fix-round.md:40-42` | The record contradicts its own capture: it prints `--max-nodes 10 : roots 10, truncated true, omitted 0 (root cut, no edges examined)` and one note, but `runs/issue22/after-roots-find-callers-code-indexer-max-nodes-10.json` (committed in this range) records `omitted: 124` and **two** notes. The capture is right and the code agrees with it; the prose is stale. Correct to `omitted 124` and quote both note lines. |
| F2 | minor (doc) | `SKILL-CLI.md:496-503` | "The manifest store hands over everything it holds. `Manifest.callers_of`, `callees_of_file_symbol`, `import_rows` and `Manifest.definitions_of` … take `limit: int \| None = None`" — `definitions_of` has **no** limit parameter at all (`manifest.py:330-343`), which is the stronger fact worth stating; and the module-level sentence is falsified in the same module by `refs_to(limit=25)` (`manifest.py:371-383`) and `find_symbols(limit=25)` (`manifest.py:292`). The carve-out two paragraphs down (line 509) covers the intent, but the unqualified opening reads as a claim about the whole store layer. Scope it the way `CHANGELOG.md` does ("under these walks"). |
| F3 | minor (docstring) | `manifest.py:531-535` | "the first four columns are the row's primary key" — the `refs` PK is `(file, line, to_name, relationship)` (`manifest.py:74-82`); the order key's first four are `(file, line, relationship, to_name)`. Same set, different order. Say "the primary-key column set (plus the receiver)". The totality conclusion is unaffected and correct. |
| F4 | minor (string) | `changed.py:326-341` | The per-name advice reads "run `find-callers <symbol>` for one symbol's whole fan-in" — `find-callers` is itself bounded by `--max-nodes` (default 40) and announces it, so the bare command can answer 40 of 200. Name the knob: "`find-callers <symbol> --max-nodes N`". The distinction the note draws (per-name cap 50 does not answer to `--max-nodes`; the node budget does) is correct and is the right fix for the original one-size advice. |
| F5 | minor (string) | `graph.py:110-123` | `_root_cap_note` names roots exist / roots shown / the knob, but not the consequence: the hidden roots' subtrees are **never walked**, so the sibling edge note's `124` counts only the edges of the walked roots. In the two-note case a reader can take 124 as the size of everything hidden. One clause closes it: "…; their subtrees are not walked, so `omitted` does not count the edges they would have contributed". |
| F6 | nit (comment) | `core.py:776` | The pin takes `exact[:3]` from a read that is capped at 25. That is safe **only because** the order key is total and 3 < 25 — the cap cannot change which three rows are pinned. Nothing at the site says so, so the next reader cannot distinguish it from the defect at line 1210. One comment line. |
| F9 | minor (test) | `tests/test_graph.py` (`test_a_capped_root_list_says_how_many_roots_exist`) | The pin's fixture has no reference rows at all, so it proves "root refusals do not leak into `omitted`" but cannot prove "edges are still counted when the roots are also cut" — the two-note case has no pin. Add one edge to a capped-root fixture and assert `omitted == 1` with both notes present. |
| F10 | minor (test) | `tests/test_graph.py` (`test_find_symbol_exact_name_is_uncapped_and_honours_limit`) | Exact-name default, exact-name `--limit 3` and `find-definition` are pinned; the browse-mode default is not — `_BROWSE_CAP` (`cli.py:39`) could change to 5 and nothing fails. One assertion: `find-symbol --type class` with no `--limit` returns 25 of the 40 in the rig's fixture. Pin the behaviour, not the constant. |

No pin in this round asserts a constant. Each of the ten fails at the base for
the reason the record names (`pins-fail-on-before-fix-round.txt`: 10 failed /
63 passed), including the two order-key pins, which insert the tie group in the
**reverse** of the key order so that a plan-order answer cannot pass by luck.

## 4b, stated plainly

The round's ground for leaving `core.py:776` alone — "it feeds an explicitly
capped, `truncated`/`dropped`-reporting surface rather than a graph walk" — is
true but is the weaker of the two arguments available, and it is not the one
that does the work. The criterion that actually decides it is: *does the cap
change this answer?* At 776, no: the surface takes three rows from a
totally-ordered read, so a 25-row cap and an uncapped read pin the same three.
That is sound, and F6 asks only that the invariant be written down.

The neighbouring line shows why the surface-based argument is not enough on its
own: `core.py:1210` is also not a graph walk, also reads the same exact-name
store read, and *there the cap changes the answer*, because the rows are
filtered by file **after** the cap.

## Out of range, but found here: `get-code-context --symbol` is still capped

`core.py:1207-1218` resolves `--symbol` from
`core_manifest.find_symbols(symbol, substring=False)` — the 25-row default —
and then keeps only `r.file == rel`. When a name has more definitions than the
cap and the requested file's row sorts past it, the filter sees nothing and the
command raises `symbol '<name>' not indexed in <file>` for a row the store
holds. Pre-existing (present unchanged at the base `5ee5154` and at the tip), not a
regression from this range, and a different symptom from #22's (a false
negative, not a false completeness claim) — but the same capped-read shape, and
live-reachable on this tool's own index:

```
$ uv run code-indexer find-symbol __init__ --project code-indexer --json
  … 40 rows; src/code_indexer/registry.py is row 26, src/code_indexer/doctor.py row 40

$ uv run code-indexer get-code-context src/code_indexer/embedder.py --symbol __init__
--- src/code_indexer/embedder.py:39-56 ---     (row 11 — inside the cap: works)

$ uv run code-indexer get-code-context src/code_indexer/registry.py --symbol __init__
InternalError: symbol '__init__' not indexed in src/code_indexer/registry.py

$ uv run code-indexer get-code-context src/code_indexer/doctor.py --symbol __init__
InternalError: symbol '__init__' not indexed in src/code_indexer/doctor.py
```

Direction (not implemented here): read the file's own symbol rows
(`symbols_for_file(rel)` filtered by name) instead of an exact-name read
filtered afterwards — no cap, no population question, same answer shape.

This must be filed as its own issue and fixed in a later round. It does not
reopen #22: #22 is about graph commands certifying an incomplete walk as
complete, and every surface it names is clean at the tip.

## Folds required

Doc / comment / string / test only — no behaviour change:

1. F1 `runs/issue22/fix-round.md:40-42` — match the record to its own capture.
2. F2 `SKILL-CLI.md:496-503` — `definitions_of` has no limit; scope the
   "hands over everything" sentence to the reads beneath the walks.
3. F3 `manifest.py:531-535` — "primary-key column set", not "first four
   columns are the primary key".
4. F4 `changed.py:326-341` — `find-callers <symbol> --max-nodes N`.
5. F5 `graph.py:110-123` — say that hidden roots' subtrees are not walked.
6. F6 `core.py:776` — record the "3 of 25 under a total key" invariant.
7. F9 `tests/test_graph.py` — pin the two-note answer.
8. F10 `tests/test_graph.py` — pin browse-mode's default 25 as behaviour.
9. File the `core.py:1210` issue (code fix, next round).

## Final verdict

**PASS WITH FOLDS.** The blocker is fixed and the fix is coherent with the
round's own documentation; the four folds the author declared inside the leg
(prefix scoping, total keys, bound-naming, dead helper) are correct and
revert-sensitive; the two declared extensions are right calls.

Issue #22 closes on its five surfaces — the filed symptom (a walk handed 25 of
40 roots while certifying the answer complete) is unreachable at the tip. The
`get-code-context --symbol` defect above is a new, pre-existing issue of the
same family and must not be allowed to falsify the corrected doc claim a second
time: file it before the next release notes tick "no store read under these
walks caps silently".

DB card reviewed: **T-000011** (read-only; no writes).
