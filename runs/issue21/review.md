# Two-axis review — issue #21: call-site identity and no silent graph cap

Range under review: base `cd2fd26` → tip `4152325`

- `a6f283f` — fix(graph): identify a caller by its call site and report every
  refused edge (issue #21)
- `4152325` — docs(graph): state the call-site identity, the truncation fields
  and the round record (issue #21)

Both commits are signed. Read for this review: the full diff, the issue body,
`runs/issue21/round.md`, and the committed evidence. The verification gate's
numbers are of record and were not re-run; the checks below are the review's
own, plus independent before/after reproductions.

## Verdict

| Axis | Verdict |
|---|---|
| Standards (repo conventions, coherence, output contract, test quality) | PASS WITH FOLDS |
| Spec (fidelity to issue #21) | PASS WITH FOLDS |
| **Final** | **PASS WITH FOLDS** — closes #21 as delivered once the folds below land and the two follow-up decisions are taken |

No finding requires a behaviour change. Everything below is a string, a
document, an evidence file or a test assertion.

---

## Standards axis

### S-1 — should-fix — the cap note justifies its own lower bound with the wrong mechanism

`src/code_indexer/graph.py:89-97` (`_cap_note`); the same sentence is quoted
verbatim at `CHANGELOG.md:26-28`, `SKILL-CLI.md:333-335` and
`runs/issue21/round.md:109-110`.

The runtime text reads `… (a lower bound: the walk stopped examining at the
cap); raise --max-nodes to see more`.

The code does not stop examining at the cap. In `find_callers` the refusal
branch (`graph.py:258-265`) `continue`s inside the loop, so every remaining
edge of the current frontier is still examined and counted; so does the `deps`
walk (`graph.py:437-446`). The count is a lower bound for a different reason:
a refused key is added to `seen` and is never expanded, so whatever calls *it*
is never walked — and a depth cap on the same answer can cut more besides.

Why this is worth a fold rather than a shrug: the parenthetical exists solely
to stop a reader treating `omitted` as the whole population, and it does that
by describing a walk that did not happen. An auditor who reads "stopped
examining" will conclude the walk saw only the first N edges; it in fact
examined and refused all of the remainder it could reach. Suggested wording:
`a lower bound: a refused edge is counted but never expanded, so its own edges
are not walked`.

The sentence is generated once and quoted three times, so all four places must
move together.

### S-2 — should-fix — the published `--json` contract was not updated

`docs/json-schema.md:60-67` still documents `{"roots": [node...]}` for
`find-callers`/`find-callees` and `{"out": {level: [...]}, "in": {level:
[...]}}` for `deps`. All three commands now also emit top-level `truncated`,
`omitted`, and `notes` when non-empty (`graph.py:388-399`, `graph.py:474-480`).

This file is the repository's JSON contract — it is where the existing
`truncated`/`dropped` pair of `semantic-search` is documented (`line 10`) — so
it is the place an agent looks before trusting a payload, and it is now wrong
in the direction the issue complained about: a consumer that reads the schema
sees no completeness signal at all. Doc-only fold.

### S-3 — should-fix — `truncated: true` with `omitted: 0` is reachable and under-documented

Measured (`--json`, read-only):

```
$ code-indexer find-callees find_callers --project code-indexer --depth 1 --max-nodes 500
{"truncated": true, "omitted": 0,
 "notes": ["depth cap 1 reached — deeper callees exist beyond depth 1; raise --depth to see them"]}
```

`GraphResult`'s docstring (`graph.py:63-73`) is accurate: `omitted` counts
node-cap refusals only. But neither S-2's schema nor `SKILL-CLI.md:325-346`
says that a depth-capped answer can carry `omitted: 0`, so a consumer deciding
completeness from `omitted > 0` — the natural reading of a field named
`omitted` — gets an incomplete answer and calls it complete. That is the same
class of over-claim the issue is about, one abstraction up.

Fold: one sentence in `docs/json-schema.md` and `SKILL-CLI.md`: *"read
`truncated`; `omitted` counts node-cap refusals only and is 0 on a depth-capped
answer."*

### S-4 — should-fix — the stdout/stderr placement is not pinned by any test

The behaviour itself is correct and was verified by hand: a capped answer's
note lands on **stdout** with the tree (`stderr` empty), a complete answer's
explanatory note lands on **stderr**. That is the right contract for an agent
piping stdout.

The pins cannot detect a regression, though. `tests/test_graph.py:485`, `:487`,
`:548` and `:567` assert against `result.output`, and in the pinned click
(`8.5.0`) `Result.output` is a *mixture* of stdout and stderr in write order
(`Result.output` docstring, `click/testing.py:282-292`). Those assertions pass
whether the note is written to stdout or to stderr — which is exactly the
ternary they sit next to: `typer.echo(f"note: {note}", err=not
result.truncated)` (`cli.py:308`). Inverting that predicate would keep the
suite green.

Fold (test-only): assert `result.stdout` / `result.stderr` in those four
places, e.g. cap note in `result.stdout` and `"note:" not in result.stderr`,
and the converse for a complete answer that carries an explanation.

### S-5 — note — the node-count consequence is not stated

Because every import statement is now its own site, a widely-referenced symbol
lists more rows per answer and reaches the default `--max-nodes 40` sooner.
Measured on the `code-indexer` project for one heavily imported helper: base
39 output lines and no note; tip 41 lines, capped, with the note.

The direction is right and the changelog does say a transitive site is no
longer swallowed — but one line belongs there for anyone diffing outputs
before and after: *expect more nodes per answer, and expect the default caps to
report truncation more often.*

### S-6 — note — one stream decision covers two kinds of note

`cli.py:307-308` chooses the stream for *all* notes from `truncated`, so any
truncation also moves an unrelated explanatory note onto stdout. Measured
(`find-callers SimCore.DistanceBetween --max-nodes 2`): the dotted-subject
explanation is printed on stdout beside the cap note. Nothing is over-claimed,
but this coupling is why "explanatory notes stay on stderr" is only
conditionally true, and it will surprise the next reader of the docstrings.
Design note only — no fold required, but a sentence in `_emit_graph`'s
docstring saying the whole note block follows the answer when the answer is
incomplete would keep the docs honest.

### S-7 — note — evidence hygiene: a host path in a committed evidence file

`runs/issue21/pre-fix-regression.txt:5` carries an absolute pytest temporary
path that includes the host's user name. The house publication rule bars
VM-specific paths in pushed content. Fold: replace the leading components with
`<tmp>/pytest-…/test_…`.

### S-8 — note — naming and terminology: clean, with one accepted exception

New identifiers (`GraphResult`, `_site_key`, `_root_key`, `_callee_key`,
`_cap_note`, `_depth_note`) and the new test names
(`test_find_callers_reports_every_call_site_in_one_enclosing_symbol`,
`test_find_callers_reports_a_refused_edge_instead_of_dropping_it`,
`test_deps_reports_its_truncation_and_keeps_its_levels`) read as natural
language; no coordinate or plan-id shorthand names code or a test. The
evidence directory names the issue number rather than a concept; that is
provenance of the kind documents already carry (an issue or decision number is
legitimate in docs and evidence), it matches the sibling `runs/` directories,
and this repository carries no coordinate-label lint. Ruled acceptable, not a
violation.

### S-9 — note — "no cap is silent" promises more than the walk delivers

`SKILL-CLI.md:325` heads the new section *"An incomplete answer says so — no
cap is silent."* True of the three graph walks, false of the store reads one
layer below (see P-2). Scope the sentence to the graph walks, or the doc
promises something the tool does not do.

### What holds on this axis

- **The identity rule is coherent and it is written where a reader hits it.**
  The module docstring (`graph.py:6-24`) states all three node identities and
  names the asymmetry explicitly; `find_callers`' docstring states site
  identity and `find_callees`' docstring states the opposite rule as
  deliberate; `SKILL-CLI.md:316-337` carries both; both CLI docstrings carry
  it. The asymmetry is defensible on its own terms — "who calls this" is a
  question about sites, "what does this reach" is a question about targets —
  and a documented asymmetry is a design, not a defect.
- **The rule really is in one place.** The only symbol-level dedup keys in the
  module are the three helpers: `_site_key` (used at `graph.py:257`, `:283`),
  `_root_key` (`:243`, `:319`), `_callee_key` (`:328`, `:347`). The remaining
  `seen` set in `deps_for_file` (`graph.py:431`) keys file names, a different
  domain, and is not a fourth inline symbol key.
- **The new result type reads coherently.** `GraphResult` replaces a two-tuple
  at both function boundaries and the four adapted call sites in
  `tests/test_reference_surfaces.py` read `.nodes` / `.notes`; `result_to_json`
  keeps `roots` byte-identical and adds siblings; `graph_to_json` survives as
  the renderer `result_to_json` builds on rather than as dead code. `server.py`
  needed no logic change and received none — it builds CLI argv and relays
  stdout (`server.py:91`, `:144`, `:288`), so the new fields reach the MCP
  tools unchanged.
- **Test quality.** The pins test the claim, not the implementation: the
  call-site pin asserts the two `(line, symbol)` pairs from the JSON payload
  rather than touching `_site_key`; the truncation pins assert the answer is
  marked and that the count is presented as a lower bound; the generous-cap and
  unknown-name pins are the negative controls that give `truncated` meaning
  (a suite that only ever asserts `truncated: true` proves nothing). Fixtures
  are discriminating: the caps used (2 and 1) sit below the number of
  candidates the rig provides, so refusals actually occur. The one gap is S-4.

---

## Spec axis

### P-1 — should-fix — suggestion (3) is refuted by evidence but not named as answered; suggestion (1) is dropped without a decision

The issue offers three suggestions and says "either is enough". Implementing
(2) plus the root cause the reporter did not find is therefore a *faithful*
answer to the complaint, not a partial one — the class of failure the issue
names ("an incomplete result is indistinguishable from a complete one") is
closed at the graph layer for both the omission and the caps.

But the dispatch of the two remaining suggestions is uneven:

- **Suggestion (3), the extractor.** This is not out of scope — it is refuted.
  The manifest read shows the reference rows exist at 152, 210 and 218, so
  extraction was never at fault and no extractor change is owed. That
  refutation is present in the round record only implicitly (under "What was
  wrong"). It deserves one explicit line in the disposition: *suggestion (3)
  closed as not applicable — the row was in the store; the query path dropped
  it.*
- **Suggestion (1), lexical-vs-store counts.** Neither done nor ruled out. The
  record's "Deliberately not done" section (`round.md:143-159`) does not
  mention it at all. Declining it is legitimate (it is a second lexical sweep
  per query, and after this fix the store is no longer the thing that goes
  missing); declining it *silently* is the one outcome an issue about silent
  omissions can fairly object to.

Rule: #21 may close on this fix, but the decision on suggestion (1) must be on
record — either a stated rejection with its reason, or a follow-up issue.

### P-2 — should-fix, follow-up owed — an incomplete list can still read as complete one layer below

`Manifest.callers_of` (`manifest.py:498`, `limit=200`),
`Manifest.callees_of_file_symbol` (`manifest.py:507`, `limit=200`) and
`Manifest.import_rows` (`manifest.py:531`, `limit=5000`) are silent caps on the
store read, and every graph walk goes through them (`graph.py:127`, `:133`,
`:151`, `:293`). A project that exceeds any of them yields a short walk that
reports `truncated: false` — precisely the failure mode of this issue, with the
new fields certifying the answer as complete.

This is correctly outside this leg's scope, and the round record discloses it
(`round.md:148-152`). It is not, however, a detail that can be left to memory:
the disclosure lives in a round document, while the defect lives in the tool.
A follow-up issue must be filed before #21 is closed, and the closure comment
should reference it.

### P-3 — note — the remedy is honest about the population, not quantified

`omitted` counts edges the walk examined and refused, not the missing
population; the record justifies that choice (counting the population would
cost a second full scan per refusal) and the text note says so. Accepted —
with S-1's wording corrected, the note states what it is, and the count is a
true lower bound rather than a claim about the whole.

### P-4 — note — the issue's line numbers and the store's rows disagree

The report names line 223; the store and the record name 218 (the three rows
are 152, 210, 218). The evidence is internally consistent and the pre/post
comparison is unaffected, but a reader placing the issue beside the record sees
an unexplained mismatch. One clause in the record — the tree has moved since
the report, the row under test is now at 218 — closes it.

### P-5 — the reported failure mode is closed

For the reported shape (a second call site inside an already-reported
enclosing symbol) and for its general class at the graph layer: a caller node
is a site, so the second site is printed with its own line and receiver; a
same-file third site is printed for the same reason; a node-cap refusal is
counted and announced; a depth-bounded answer announces itself. Combined with
the measured identity of `find-callees` output (below), the two commands now
answer different questions and each answer is honest about its own bounds.

### No unintended behaviour change — verified

- **`find-callees` output is unchanged.** Run against the same `vivarium-sim`
  index from a base worktree (`cd2fd26`) and from the tip: byte-identical
  output, exit 0 both. The target-set identity is genuinely unchanged, not
  merely documented as unchanged.
- **Import-based fan-in is still reported**: import rows appear as
  `[import:import/ast]` nodes at both base and tip.
- **Exit codes**: 0 on success in every run performed here (callers, callees,
  deps, JSON and text, capped and uncapped); the failure taxonomy is untouched
  — no error path in the diff.
- **No extraction or fingerprint change**: the diff contains no
  `graph_extract.py`, no schema and no embed change, so `EXTRACTION_VERSION`
  is untouched and no reindex is owed. That claim in the changelog holds.
- **JSON shape**: `roots` and the per-level `in`/`out` dicts keep their shape;
  the new keys are additive siblings.

### Environment caveat for future rounds (not a finding against this change)

The assumption that the installed console script is a pre-fix build does not
hold on this host: the tool install is an *editable* install of this checkout
(`direct_url.json` → `"editable": true`), so `~/.local/bin/code-indexer` runs
whatever the working tree currently holds. A genuine "before" must come from a
base worktree, as the comparison above did; a "before" run made from the
installed script after the change would silently show the fix and look like
evidence.

---

## Folds required (doc / comment / string / test only — no behaviour change)

1. **S-1** — reword the cap note in `graph.py:89-97` and its three quotations
   (`CHANGELOG.md:26-28`, `SKILL-CLI.md:333-335`, `round.md:109-110`).
2. **S-2** — document `truncated` / `omitted` / `notes` for all three graph
   commands in `docs/json-schema.md:60-67`.
3. **S-3** — one sentence: `truncated` is the completeness flag; `omitted` is
   0 on a depth-capped answer.
4. **S-4** — assert `result.stdout` / `result.stderr` at
   `tests/test_graph.py:485`, `:487`, `:548`, `:567`.
5. **S-7** — redact the host temp path in `runs/issue21/pre-fix-regression.txt:5`.
6. **S-5** (optional, note) — one changelog line on the node-count consequence.
7. **P-1** — record the decision on suggestion (1) in the round record, and name
   suggestion (3) as refuted rather than unaddressed.

## Follow-ups owed before #21 is closed

- File the store-level silent-cap issue (**P-2**) and reference it from the
  closure comment.
- Put the suggestion (1) decision on record (**P-1**).

With those two decisions taken and the five folds landed, this change is a
correct, well-evidenced and honest repair of the reported defect, and it is
better than the report asked for: it found the real cause instead of the
suggested one.
