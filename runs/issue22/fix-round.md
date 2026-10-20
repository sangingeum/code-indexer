# Fix round — the definition-roots cap and the four folds (issue #22)

Base for the "before" evidence: `5ee5154` (the tip the two-axis review FAILed;
`58473d9` adds only the gate record). Fix tip: see §Commits. Query path only:
no DDL, no extraction change, `EXTRACTION_VERSION` stays 4, no reindex owed,
exit codes and `server.py` untouched.

## Verdict per item

| item | severity | disposition |
|---|---|---|
| definition-roots read caps at 25 | blocker | **fixed** — `definitions_of` (uncapped exact-name read) behind roots, qualifier, `_definer_of`; roots keep the node budget, a cut root set is announced and not counted in `omitted` |
| `--path-prefix` hotspot attribution | should-fix (gate: blocker) | **fixed** — `file_fan_in(scope_prefix)` scopes both aggregates to the files reported on |
| ordering key is not total | should-fix | **fixed** — `callers_of (file, line, relationship, to_name, receiver)`, `import_rows (file, line, raw_target)`; claim corrected |
| impact note advises a flag that cannot help | should-fix | **fixed** — `bounds {per_name, node_budget, max_nodes}` per entry, note names the bound that fired |
| `Manifest.fan_in_counts` dead | should-fix | **deleted** |
| `find-symbol` exact mode ignores `--limit` | gate finding | **fixed** — exact-name mode is uncapped by default and honours `--limit`; browse/substring keep 25 |
| `changed-symbols` text shows six callers silently | gate finding | **fixed** — `(+N more)` after the six-line preview |
| docs assert no silent store cap | falsified by the blocker | **corrected** — scoped and completed (CHANGELOG, SKILL-CLI, docs/json-schema.md) |

Two small extensions, declared rather than smuggled:

1. **`find-definition` was the third exact-name surface**, also capped at 25
   by the same read. Leaving it would have falsified the corrected doc claim
   again, so it now uses `definitions_of` too.
2. **The symbol read's order key gained a trailing `file`.** The root cap
   takes the first `cap` roots, so "a capped read is a reproducible prefix"
   has to hold for this read as well; `(name, start_line)` is not total.

## Live before/after — `find-callers __init__ --project code-indexer`

```
base 5ee5154 : roots 25, truncated false, omitted 0, notes none
fix  tip     : roots 40, truncated false, omitted 0, notes none
```

(The store holds 40 `__init__` rows.) At a cap the root set alone spends the
whole budget, so level-0 edges are refused as well and are counted:

```
--max-nodes 10 : roots 10, truncated true, omitted 124
  note: 40 definition roots exist for this subject, node cap 10 — 30 roots not shown;
        raise --max-nodes
  note: node cap 10 reached — 124 more reference edges not shown (a lower bound: a
        refused edge is counted but never expanded, so its own edges are not walked,
        and a depth cap on the same answer can cut more); raise --max-nodes to see more
--max-nodes 25 : roots 25, truncated true, omitted 124
  note: 40 definition roots exist for this subject, node cap 25 — 15 roots not shown;
        raise --max-nodes
  note: node cap 25 reached — 124 more reference edges not shown (... a lower bound ...)
```

Captures: `before-roots-find-callers-code-indexer.json`,
`after-roots-find-callers-code-indexer{,-max-nodes-10,-max-nodes-25}.json`,
summarised by `summarize_graph_json.py`.

## Prefix scope — before/after rows

`probe_prefix_fix.py`, read-only on the live manifests. "before" is the
fan-in the prefix's files receive with a whole-store owner; "after" with the
owner resolved among the prefix's files:

```
vivarium-sim  docs/               97,405 -> 97,937   (+532,  6 names)
vivarium-sim  docs/verification   87,579 -> 93,623   (+6,044, 16 names)
b1b7f48b      docs/              361,697 -> 381,992 (+20,295, 19 names)
```

The review's 588 and 20,330 reproduce exactly (`probe_prefix_scope.py`
re-run), as does the gate's 6,044. The small difference is definitional: the
review counts every row whose owner moves, including rows from the owning
file itself, which fan-in excludes as self-references either way.

Visible table change on `b1b7f48b --path-prefix docs/`:
`docs/SUMMARY.md` 7,345 → 8,096, and
`docs/Project_Architecture_Blueprint.md` (6,179) enters the top ten.
`vivarium-sim`'s top ten is unchanged (the recovered rows fall lower).
Captures: `before-/after-prefix-overview-{vivarium-sim,b1b7f48b}.json`.

## Tie-group evidence

```
vivarium-sim  imports 3 (file, line) tie groups / 488 rows;   refs 73,911 / 608,055
b1b7f48b      imports 0 (file, line) tie groups / 12 rows;    refs 107,401 / 934,672
largest refs group: 657 rows (vivarium-sim), 198 rows (b1b7f48b)
```

`probe_tie_groups.py` → `probe-tie-groups.txt`. The old key's order inside
those groups was the plan's, not a contract.

## Pins — and they fail at the base

`pins-fail-on-before-fix-round.txt` is the base worktree's run of the updated
test files against `5ee5154`: **10 failed, 63 passed**. Failures, by item:

- roots: `assert 25 == 40`; capped case `assert 25 == 10`
- `find-symbol` exact mode: `assert 25 == 40`
- `definitions_of`: `AttributeError: 'Manifest' object has no attribute`
- `callers_of` tie: `At index 0 diff: (1, 'member') != (1, 'call')`
  (insertion order, exactly the defect)
- `import_rows` tie: `'pkg.zeta' != 'pkg.alpha'` at index 0
- prefix: `scoped.get("docs/alpha.py")` is `None` (the in-prefix definition
  lost its five references)
- impact `bounds`: `KeyError: 'bounds'` (both impact tests)
- preview: no `(+4 more)` in the rendered text

Fixtures: 40 generated classes each defining `step` (root pins); `beta.py` +
`docs/alpha.py` both defining `shared_topic`, so the global first definition
is out of prefix (prefix pin); one-line tie groups inserted in the REVERSE of
the key order (order pins).

## Verification at the tip

```
uv run pytest tests/ -q -W error   -> 498 passed, 2 deselected in 73.54s
uvx ruff check src tests           -> All checks passed!
```

## Commits

```
cbcecf6 fix(graph): hand every definition root to the walk and name a capped root set
56d4cdf fix(overview): scope the fan-in ownership to the files being reported on
3c61060 fix(manifest): make the capped-read order keys total
5aeeedf refactor(manifest): delete the dead fan_in_counts aggregate
d74b1e4 fix(changed): name the bound the impact list hit, and count the hidden preview
b945a98 docs: state the definition-root cap, the prefix scope and the total order keys
```

Every commit verifies `%G?` = `G`. Issue #22 stays open.
