# Two-axis review — the store reads hand over everything (issue #22)

Base `0b66431` → tip `1139a1e` (`d9ade33`, `c80e16d`, `1139a1e`). Review-only;
this review changed no code.

Method: read `AGENT.md`, `CONVENTIONS.md`, the issue body, `runs/issue22/round.md`,
the full source/test diff and the affected code at the tip; then probed the two
live manifests read-only (`~/.code-indexer/<slug>/manifest.db`, URI `mode=ro`)
for query plans, ordering-key tie groups, prefix-scope damage and symbol-row
counts, and ran one live graph read to test the surviving-cap question. Gate
numbers were taken as of record from the dispatch, not re-run (§Gate record).

## Verdict

**FAIL** — one blocker. The round closes the three reads the issue enumerated,
and closes them cleanly, but issue #22's filed symptom — a short walk reporting
`truncated: false` — is still reproducible on a live index through a fourth store
read under the same walks: `Manifest.find_symbols(..., limit=25)`, the read that
produces a walk's definition roots. The round's own documentation now says that
cannot happen. Everything else found is a narrow fold.

Findings are named by what they are; the severity column is the ranking.

---

## Standards axis

### One voice for bounds — holds in the layer, broken one read further down

The rule is implemented as stated for the three named reads: no `LIMIT` clause is
emitted when `limit is None` (`manifest.py:498-509`, `518-536`, `567-578`), the
graph layer owns and announces its cap, and no second truncation vocabulary
appears — `changed-symbols` reuses `truncated`/`omitted` instead of inventing a
pair. The graph `omitted` still means "edges examined and refused at the node cap,
a lower bound", which is what the `GraphResult` docstring and the note text say.

The break is the **definition-roots read** below: `find_symbols` is a store read
that caps at 25 and sits directly under `find-callers` / `find-callees` (roots and
qualifier resolution) and under `_definer_of`.

### `limit: int | None = None` as a contract — sound, documented, no `-1` trap

"No argument means no cap" is documented in all three docstrings and in the new
`SKILL-CLI.md` §"Store reads (no silent caps)" (`SKILL-CLI.md:484`), which is
explicit that `None` means *the whole store read*, not a large number, and that a
cap belongs to the layer above. The old 200 / 5,000 semantics are gone from the
docstrings and from `docs/json-schema.md`. No path binds `None` into `LIMIT ?` —
the clause is appended only when `limit is not None` — so SQLite never sees `-1`
by accident. An explicit `limit=` stays usable and is used where a caller
genuinely wants one (`changed.py:234` `CALLERS_PER_NAME = 50`, used at `:268`),
and the tests pin both directions (`tests/test_manifest_store_reads.py:59-60`,
`:80-81`, `:99`).

### The hotspot aggregate — right question, index-supported plan

Semantics: resolved imports into a file plus references to a name that file owns,
self-references excluded, with a duplicated name owned by its first definition —
the same question the old row scan asked. `MIN(file)` over `symbols` equals the
old `setdefault` over rows ordered `(file, start_line)`, and the round's hand
count agrees on every file of both large indexes. No `symbols` row points at a
file missing from `files` (probe: 0 on both indexes), so the global definition set
makes no difference to a full overview. It does make one difference under
`--path-prefix` — see the **prefix scope** finding.

Plan, read from `EXPLAIN QUERY PLAN` on both live indexes:

```
refs aggregate     CO-ROUTINE s
                   SCAN symbols USING INDEX idx_symbols_name
                   SCAN s
                   SEARCH r USING INDEX idx_refs_to_name (to_name=?)
                   USE TEMP B-TREE FOR GROUP BY
imports aggregate  SCAN imports (488 rows) / USE TEMP B-TREE FOR GROUP BY
```

The join is index-supported: it drives from the ~6.8k grouped names and does a
range search per name on `idx_refs_to_name` rather than scanning `refs`. That is
the honest reading of "joins and `GROUP BY` on a 608k-row table must be
index-supported" — no new index is owed, and the gate's medians (≤1.66×) match
the plan.

### Fan-in dominated by documentation — ruled: **a follow-up issue**

At the tip, `vivarium-sim`'s top five fan-in entries are four markdown files and
one script (`docs/verification/adr-0037-…md: 19183`). That is *correct output*:
heuristic reference rows are real rows and the count is a count. Suppressing them
would be the same sin in reverse — an orientation surface that hides what the
store holds.

But a ranking whose top ten are docs does not orient anyone toward code, so as an
orientation surface it is misleading. The first thing to fix is not the number but
the label: a hotspot entry should say what it is (language or kind), and a
code-only variant can follow if the owner wants one.

Ruling: **a follow-up issue.** Not "must change this round", because this round's
job was to stop sampling and it does. Not "correct as is", because an unexplained
docs list at the top of the first surface every agent reads is a standing
invitation to a wrong conclusion. Removing the doubt is this round's; labelling
the result is the follow-up.

Correction to the round record's framing: before the fix the top hotspot was the
**same** markdown file at 184
(`runs/issue22/before-overview-vivarium-sim.txt:22`), so the sample hid the
*magnitude* (104×) and the false completeness claim — not the docs dominance,
which was visible before and was not revealed by this fix.

### The seed-path change — right contract, declared, pinned both ways

`graph.py:238-255`: the no-definition path now reads the whole store and applies
the walk's own `max_nodes`, counting refusals into `omitted` and setting
`truncated`. That is the only reading of "the graph layer caps and announces" that
survives an uncapped store read; leaving it unbounded would have returned 10,258
nodes on `pairA` honouring no cap at all, which is worse than the 200-row silence
it replaced. Declared in `CHANGELOG.md`, and pinned in both directions
(`tests/test_graph.py::test_seeded_reference_list_applies_and_announces_its_node_cap`:
40 → `omitted: 210`; 400 → complete with no note).

The seeding note still explains itself honestly (no definition row, symbols cover
only method|class|function|enum|interface|struct), and the cap note is the shared
`_cap_note`, whose "lower bound" wording remains accurate for seeds.

### Dead and near-dead code

- `callers_of_all` — **correctly deleted.** No consumer remains at the tip in
  `src/`, `tests/`, `docs/`, `SKILL-CLI.md` or `server.py`; the surviving mentions
  are the changelog and the round record, which are history and should keep it.
- `Manifest.fan_in_counts` (`manifest.py:538`) — **mis-documented dead method**
  (see the finding). `git grep` at the base shows it already had zero callers at
  `0b66431`, and overview at the base used `callers_of_all`, never this. Its
  docstring, "Top N names by reference count (overview hotspots)", describes a
  surface it never fed and could not have fed (it counts names; hotspots count
  files). Ruling: delete it — no consumer, no test, three lines. Not a #22
  blocker, but it is a silent 20-row cap left sitting in a store layer the round
  says hands over everything.

### Determinism — the ordering key is not total

`callers_of` keeps the `ORDER BY file, line` it already had; `import_rows` gains
it (`manifest.py:572`). Neither is a total order:

- `imports` PK is `(file, raw_target, line)` (`manifest.py:72`), so two rows can
  share `(file, line)` with different `raw_target`. Live: 3 such tie groups on
  `vivarium-sim` (488 import rows), 0 on `b1b7f48b` (12 rows).
- `refs` PK is `(file, line, to_name, relationship)`. Live: 73,911 tied
  `(file, line)` groups on `vivarium-sim`, 107,401 on `b1b7f48b`. Pre-existing for
  `callers_of` (the clause is unchanged), but it now also decides the
  `changed-symbols` per-name cut.

So "a capped read is a deterministic prefix" (docstring, `CHANGELOG.md:37`, and
the test's name) holds only when no tie group straddles the cut. The codebase
already knows the remedy — `find_references` orders `file, line, relationship`
(`manifest.py:359`). The uncapped read's consumers do not depend on an order
change: the sort key is what `callers_of` already returned, `import_rows`'
consumers aggregate (`graph.py:135`, `:422`, `overview.py`), and a capped
`import_rows` read now has **no caller at all**.

### Docs match behaviour — two claims the code does not honour

- `CHANGELOG.md:24` "**No store read caps silently any more**" — false while the
  roots read caps.
- `SKILL-CLI.md:334` "the store reads beneath these walks cap nothing at all, so
  a short answer is a cap this walk applied and announced" — false for the same
  reason.
- `SKILL-CLI.md:165`'s `changed-symbols` synopsis and
  `docs/json-schema.md`'s new `changed-symbols --json` section match the code:
  `callers`, `candidate_tests`, `truncated`, `omitted`, present only with
  `--impact` (`changed.py:288-292`). The `overview` hotspot sentence ("complete
  per-file count … two runs over one index agree exactly") is accurate for a full
  overview; the prefix defect is a scope error, not a sampling error.
- The declared behaviour change and its numbers check out against the captures:
  `184 → 19,183` and `40 → 3,627` on the **same** files; `253 → 55,503` and
  `76 → 16,662`. The 1.9% and 0.8% shares reconcile with the store counts
  (10,258/200, and 5,000/608,055).

### Test quality — strong, with three named gaps

The pins test the claim, not the shape: 250 call sites (over the old 200) through
the CLI at `--max-nodes 400`; 250 out-edges for both `callees_of_file_symbol`
forms; 30 import rows inserted in reverse; a hand-counted aggregate whose large
count is inserted **last**; 5,100 references where a 5,000-row sample cannot pass;
60 callers against the 50-row per-name read; and the complete-direction pins (no
note, `truncated: false`). Revert sensitivity is the gate's bar (11 fail against
the base) and I rule it sufficient.

Shape pins, named honestly: `tests/test_changed_symbols.py:330` asserts
`len(callers) == changed.CALLERS_PER_NAME`, which pins the constant rather than
the behaviour; `assert m.file_fan_in()[0] == ("hot.py", OVER_CAP)` pins the sort
contract and is therefore behavioural.

Gaps: no test exercises the definition-roots read (which is why it passed), none
exercises `--path-prefix`, and `test_import_rows_is_complete_and_ordered`
(`tests/test_manifest_store_reads.py:86`) uses a fixture with no `(file, line)`
tie group, so it cannot fail on the order-key finding.

---

## Spec axis

### Does it close #22 as filed? — **not yet**

The issue's words: "a project that exceeds any of those limits yields a short walk
that reports `truncated: false`". For the three enumerated reads, no longer: they
hand over the whole store, the walk caps and counts, and the short walk now
carries `truncated: true, omitted: 10218` on `pairA`, as the dispatch's re-read
shows.

The remaining path, reproduced live on the tool's own index:

```
code-indexer find-callers __init__ --project code-indexer --max-nodes 400 \
  --json --skip-stale-check
  → roots: 25, truncated: false, omitted: 0, notes: []
```

The store holds 40 `__init__` symbol rows across more than 25 files (probe over
the same manifest; 7 names in `code-indexer` and 6 in `vivarium-sim` — `Compile`
53, `IncrementDemoCounter` 53, `NullTickWorld` 46 — exceed 25 definitions). The
walk is handed 25 of 40 roots, walks only their subtrees, and certifies the answer
as complete. That is #22's symptom verbatim, today.

Fix direction (small): read the roots and the qualifier with `limit=None` — or add
an exact-name variant with no browse cap — and either count refused root
definitions into `omitted` or run them through the walk's own node counter, the
same treatment the seed path just received. `find-symbol`'s browse cap is a
different surface and may stay.

### The overview sampling defect — correctly inside the round

Same class (an unobservable bound certifying an incomplete answer), same read
(`callers_of_all(limit=5000)`), and the fix removes the read. Leaving it out would
have left `overview` publishing a 5,000-row sample as a hotspot table while the
changelog claimed completeness.

The visible output change is adequately declared: `CHANGELOG.md` carries
before/after for two live indexes, states "This changes visible output, and that
is the fix", and warns that "Any hotspot ranking computed before this change was
a sample, not a count"; `docs/json-schema.md` states the new completeness
contract. A reader diffing `overview` before and after cannot mistake it for a
regression.

### No unintended behaviour change elsewhere — confirmed

`EXTRACTION_VERSION` is still 4 (`graph_extract.py:95`), the diff carries no DDL,
no row-key and no fingerprint change, so no reindex is owed. `server.py` and
`cli.py` are untouched — the MCP relay stays a relay and `changed-symbols` keeps
its flags. `--max-nodes` / `--depth` semantics are unchanged in the
definition-root walk (roots still consume the node budget), `omitted` keeps its
documented meaning, and exit codes are untouched.

One behaviour change beyond the issue's letter, correctly declared: the
no-definition seed path now obeys `--max-nodes`. A caller that used to get 200
unbounded seeds now gets 40 and a note — right, and stated in the changelog.

---

## Findings

| id | severity | file:line | finding |
|---|---|---|---|
| **definition-roots read** | **blocker** | `src/code_indexer/manifest.py:292-293`; `src/code_indexer/graph.py:226`, `:331`, `:205`, `:161` | `find_symbols` still caps at 25 under the graph walks. `find-callers __init__` returns 25 of 40 definition roots with `truncated: false, omitted: 0`. Issue #22's symptom, live, in this index. |
| **prefix scope** | should-fix | `src/code_indexer/manifest.py:544-566`; `src/code_indexer/overview.py:178` | `file_fan_in` picks the owning definition across the whole store, then `build_overview` filters to `--path-prefix`'s files. Up to 588 reference rows lost on `vivarium-sim` and 20,330 on `b1b7f48b` under `--path-prefix docs/` (6 and 19 names affected); the old code attributed a name to its first definition *among* the prefix files. |
| **order key** | should-fix | `src/code_indexer/manifest.py:567-578` (docstring), `:509`; `CHANGELOG.md:37`; `tests/test_manifest_store_reads.py:86` | `ORDER BY file, line` is not total — imports PK `(file, raw_target, line)` with 3 live tie groups, refs with 73,911. A capped read is a reproducible prefix only when no tie straddles the cut. |
| **impact note advice** | should-fix | `src/code_indexer/changed.py:348-355` | The note always blames both bounds and advises "raise --max-nodes for more", which does nothing when the cut is `CALLERS_PER_NAME = 50` — the exact case its own test drives (`--max-nodes 100`, 60 callers). |
| **dead fan_in_counts** | should-fix (not a #22 blocker) | `src/code_indexer/manifest.py:538-542` | Dead at tip **and** at `0b66431`, and its docstring claims "(overview hotspots)", which overview never used. Delete it, or correct the docstring. |

### Notes

- **Sample framing.** The round record says the sample "happened to hide" the docs
  dominance; it did not — the same markdown file led before at 184. See the
  hotspot ruling.
- **Materialized fan-in lists.** `_expand_callers` (`graph.py:129`) now reads a
  node's entire fan-in list and discards all but the cap. No pathology on the two
  large indexes (gate medians ≤1.66×), but cost now scales with population rather
  than `--max-nodes` on names with tens of thousands of references.
- **Per-name aggregate.** `changed.py:271-273` runs one `ref_relationship_counts()`
  per target to learn the shortfall: indexed and cheap, but N extra queries per
  diff where a `LIMIT+1` read would have answered in the same round-trip.
- **Two measurement bases under one name.** `docs/json-schema.md` calls impact
  `omitted` "rows withheld, never a population total"; for the graph commands the
  same name still means "edges examined and refused". Both are explicitly
  "withheld, not population", so the pair is coherent — worth one sentence if the
  two are ever documented together.

## Gate record

`runs/issue22/gate.md` does not exist at `1139a1e`, is not in the working tree,
and its board card (**T-000007**) is still `in_progress`; the gate worker's state
directory holds only `alive`/`owner`. I ruled on the bars as carried in the
dispatch (490 passed / 2 deselected, the four medians, the 19,183 re-read, 11
revert-sensitive pins) and did not re-run them. The gate record must land before
#22 closes — and, given the blocker, it needs a bar for the definition-roots read.

## Board

Card **T-000008** read read-only (`in_progress`, `claimed_by: butler`).

## Final verdict

**FAIL** — issue #22 is not closed as delivered: the filed symptom survives on the
definition-roots read, and two documentation statements assert otherwise.

The repair itself is small and the rest of the round is sound. The blocker wants
`limit=None` on the root/qualifier read plus refusal accounting, and a pin above
25 definitions; the prefix scope wants the aggregate scoped to the files it
reports on; the order key wants a tiebreaker or a weaker claim; the impact note
wants to name the bound it hit; `fan_in_counts` wants three dead lines deleted.
With those, both axes pass.
