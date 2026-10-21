# Two-axis review — issues #24, #25, #26: symbol-mode follow-ups

Range under review: base `8254894` → tip `0221b30` (branch `main`).

- `9e621ef` — fix(symbol-mode): match the exact symbol name only on the changed-file path
- `1029550` — fix(eval): score a labelled symbol against every row of it in the file
- `7d61da0` — fix(symbol-mode): one decorated definition answers as one range
- `4b5aa16` — fix(symbol-mode): merge definitions before applying context padding
- `0221b30` — docs(runs): verification gate record for issues #24-#26

Read for this review: the full diff, the three issue bodies, `AGENT.md`,
`SKILL-CLI.md`, `docs/json-schema.md`, `CHANGELOG.md`, the committed gate record
and its captures, and the code the diff touches. All commits of the round are
signed (`%G? = G`), the worktree is clean apart from the pre-existing untracked
`runs/` captures, and the diff is query-time only: `EXTRACTION_VERSION` is
untouched at 4, no schema or chunker behaviour changed, no reindex is owed —
the gate's bar 5 and its `index-status` / `list-projects` captures agree, and I
checked the same claim by reading the hunks rather than the summary.

The verification gate was **not** re-run. Its six bars are of record and are
accepted as measurements. What follows rules on them, and re-reads exactly one
of them (bar 2) where the record's prose and its own capture disagree about
scope. No source, test or `CHANGELOG` file was edited by this review.

## Verdict

| Axis | Verdict |
|---|---|
| Standards | PASS WITH FOLDS |
| Spec | PASS WITH FOLDS |
| **Final** | **PASS WITH FOLDS** |

All three fixes are load-bearing and correctly built: the base tree answers a
dotted name on the changed-file path, scores a hit in a label's second row as a
miss, and returns two ranges for one decorated definition; the intermediate
tree that merged after padding collapses six definitions into one span at
`--context-lines 6`. Nothing in the fold list is a behaviour fix except one
additive JSON field. Eight folds, all documentary or test-pin except the
contract marker (fold 6), which is the one requirement of the three issues the
round did not deliver.

Closing reads:

- **#24** — closeable as delivered. Folds 1, 3, 7 and 8 are doc and test-pin
  fidelity, not gaps in the fix.
- **#25** — closeable as delivered. Fold 3 prunes the surviving wrapper.
- **#26** — **closeable only after fold 6 lands.** The behaviour is right and
  is pinned; the issue's second requirement ("the decision should be visible in
  the JSON contract") was answered with prose in a docstring and two doc
  passages, which is not the contract. Fold 2 and fold 5 make the surrounding
  prose true; fold 6 is the outstanding requirement.

---

## Rulings on the four open questions

### 1. #26 — is prose disclosure enough, or is a machine-readable marker owed?

**A machine-readable marker is owed. Prose is not the contract.** Ruled plainly,
because this is the one place where the round reads the issue more loosely than
it is written.

The issue gives two implementation options and then one requirement that binds
either: "Whichever is chosen, the decision should be visible in the JSON
contract (no silent merging)." The round chose the first option, implemented it
correctly, and disclosed it in the `code_context` docstring, twice in
`SKILL-CLI.md` and in the `CHANGELOG` — while the committed `CHANGELOG` entry
states, as a merit, "The JSON answer shape (`file` / `stale` / `re_resolved` /
`segments`) is unchanged". That is the requirement declined, not satisfied.

Three reasons the decline does not hold:

- **The answer is a versioned contract.** `cli.py` emits `dict(data, schema=1)`,
  and the repository carries the contract document — `docs/json-schema.md:42-48`
  — which this round did not touch at all. A decision that only a person reading
  `SKILL-CLI.md` can see is not visible to the machine that parses the payload.
- **The merge is lossy.** After it, segment count no longer equals row count.
  A consumer cannot tell one span from one span that absorbed a decorator row,
  cannot audit the merge, and cannot recover the row-level answer the previous
  round shipped (issue #23's "every matching row"). "No silent merging" exists
  precisely to keep that loss auditable.
- **Prose in three places is already drifting.** See standards findings S-1 and
  S-4: the second `SKILL-CLI.md` passage is ungrammatical (the gate's own
  observation, confirmed), and both docstrings point at `SKILL-CLI.md` as the
  place the rule is stated.

Recommended shape — minimal, additive, no schema bump (`truncated`/`omitted`
were added the same way in issue #21): a per-segment **`symbol_rows`** integer,
present in symbol mode only, counting the stored rows merged into that span
(sum over segments = rows matched). It is the auditable fact; the rule name
stays prose. Fold 6 carries the exact change. Bumping `schema` is not required
for an added field and would break every reader for no gain; recording the field
in `docs/json-schema.md` is required.

### 2. `_symbol_range` — keep for compatibility or prune?

**Prune.** The library has no external callers: it is a private
(`_`-prefixed) helper in `evalharness`, and after this round its only caller is
one test (`tests/test_symbol_resolution.py:237`). Its new docstring says it is
"kept for a caller that wants one range", and no such caller exists — which is
the exact shape issue #24 deleted this round: a branch kept for a caller who
might want it, documented as if that caller were real. Keeping it also preserves
a second way to answer "where is this symbol in this file" that is wrong for
every label with more than one row — the defect #25 fixed — one refactor away
from being called again.

Pruning is cheap and loses nothing: the test that uses it pins the past-the-cap
read, which it can pin just as well against `_symbol_ranges`. Fold 3.

### 3. #24 — is delete-with-exact-name-only the right reading, and does the record tell the truth about why?

**The reading is right; the record's reason is overstated and must be scoped.**

Right reading, on all three counts the issue asked for: the `endswith("." + symbol)`
disjunct is gone and the exact-name comparison is the whole rule
(`core.py:1251`); the C# extractor comment now states what is stored rather than
what a caller might wish to match (`ts_chunker.py:285-289`); a test pins the
exact-name rule on a changed file. The issue also named "the two
symbol-from-node / symbol-from-declarator helpers" — only the one was wrong:
`_symbol_from_declarator`'s docstring already says it prefers the last part of a
`qualified_identifier` (`Foo::bar` → `bar`), so it was left alone, correctly, and
the gate record says so (bar 2, third paragraph). That is the right answer to
the issue, not a partial one.

Where the record needs scoping is its headline sentence
(`runs/issue24-26/gate-verification.md:69-71`): "So the suffix branch was live
behaviour on the changed-file path, not merely dead code: on the base the
command answered a symbol that is not the requested name." Its own capture
shows why that is not what was measured. `probe-base.txt` reports
`[24] dotted live row answered --symbol prepare: segments=1 spans=[(10, 12)]
re_resolved=True`, and the probe gets that answer by **substituting** a chunk
whose symbol is `pkg.prepare` — the extractor cannot produce such a row, which
is the issue's premise and the gate's own bar-2 paragraph ("the extractor cannot
produce such a row … a natural-language repro does not exist").

So the accurate statement is: the branch would answer a dotted row if one ever
reached the changed-file path, and the differential proves that semantics and
proves the deletion changes no answer for any real input. The issue's claim that
the branch "can never fire" **stands for every indexed file** — the base
"answered" only because the probe handed it a row that extraction never stores.
A reader of the current sentence will conclude a real file's answer depended on
a dead branch, which is false, and will then distrust the count of zero dotted
names that the issue actually rests on. Fold 7 rescopes the two sentences; bar 2
stays **PASS**, because the rule is pinned and the deletion is right.

### 4. Padded spans that overlap — confirm or overturn the gate's ruling?

**Confirmed on the code; the gate's "no doc is owed" conclusion is overturned.**

The ruling that overlapping padded windows are acceptable presentation, not a
defect, is right, and for the reason the gate gives: identity is decided on the
definitions, presentation on `--context-lines`, and the alternative — merging
the padded windows — is exactly the defect `4b5aa16` removed (one span `(1, 28)`
at `--context-lines 6`, six at `0`). Overlap is the honest consequence of two
definitions being close together; suppressing it would mean dropping lines or
silently redefining a segment. The count invariant (six definitions → six
segments at 0, 2 and 6) is the property callers can rely on, and the captures
show it holding. No code change, no follow-up issue.

What I do not accept is the last paragraph's conclusion that a sentence in
`SKILL-CLI.md` "is left to the routing decision — it is not worth a behaviour
change or an issue on its own". The sentence is owed, as a fold, not as an
issue: `SKILL-CLI.md:375-376` now says "The merge is part of the answer, never a
hidden convenience: counting segments counts definitions", which invites a
caller to concatenate segment bodies and expect no repetition; at
`--context-lines 6` the gate measured 74 emitted lines over a 28-line union, 46
of them repeated (62.16%, both figures the gate's, second one re-checked with
`calc`: `46/74*100 = 62.1622`). `SKILL-CLI.md` is copied byte-identically into
every agent's skill tree, so a missing consequence sentence is an instruction to
every agent in the household to waste tokens and to mis-read the answer. Fold 2
is one sentence.

---

## Standards axis

### S-1 — should-fix — the second `SKILL-CLI.md` passage is ungrammatical and says something slightly different from the first

`SKILL-CLI.md:551-559`, the `get-code-context` bullet in the command summary:

> … and then the ranges of that one name that overlap or touch **merged into one
> span each**, so a decorated definition answers as one segment …

The gate reports the missing auxiliary ("are"). Confirmed, and it is not only
grammar: the primary passage (`SKILL-CLI.md:368-377`) says "one contiguous span
per run", while this one says "one span each". For a name with two disjoint
runs the second phrasing reads as one span per *range* — the rule with the merge
taken out. Since this is the file every agent carries, both readings are
shipped. Fold 1 fixes both in one edit.

### S-2 — should-fix — `evalharness._symbol_range` is a dead wrapper whose docstring states an intention

`src/code_indexer/evalharness.py:120-130`. Covered in ruling 2; fold 3.

### S-3 — should-fix — the `CHANGELOG` narrates a defect that never shipped

`CHANGELOG.md:42-57`: "`code_context` padded every row's range with
`context_lines` **before calling `_merge_symbol_ranges`**, whose adjacency test
(`start <= previous_end + 1`) then fused two genuinely disjoint definitions …".

`_merge_symbol_ranges` did not exist before this round, and the padded-then-
merged order existed only inside commit `7d61da0` before `4b5aa16` reordered it.
Both changes land under the same `[Unreleased]` heading, so no reader ever ran
the described behaviour; the entry reads as a regression fix to a released
version. A changelog is a statement about shipped behaviour, so this is a truth
defect, not a style preference: the honest entry states the rule and its reason
("merging the padded windows would fuse two definitions a few lines apart and
make the segment count depend on a display flag") without the false history.
Fold 4.

### S-4 — should-fix — the docstrings cite their own documentation as the source of the rule

`src/code_indexer/core.py:66-68` — "The merge is part of the answer, never a
silent convenience: it is stated in ``Core.code_context`` and in
`SKILL-CLI.md`." — and `core.py:1228-1230` — "The merge is stated here and in
`SKILL-CLI.md` — it is part of the answer, never a silent normalisation."

The second half of each sentence is the mechanism: the merge is part of the
answer, never a silent convenience. The first half is a pointer to another
document, and a pointer is precisely what goes stale when someone edits
`SKILL-CLI.md` (as fold 1 is about to) — the code would then assert, in the
present tense, a fact about a file it cannot check. Docstrings state the rule;
they do not keep a bibliography. Fold 5 drops the pointers and keeps the
contract sentence in both places.

### S-5 — should-fix — the rule's consequence for a nested same-name definition is unstated

The merge is "same-name ranges that overlap or touch". A definition of the same
name *nested inside* another (a closure, a redefinition inside a method, a
conditional redefinition) overlaps the outer one, so it is absorbed: the outer
span answers, and the inner definition never appears as its own segment. That
follows from the option the issue sanctioned ("merge ranges of the same name
that overlap (or touch) into one range per contiguous span"), so it is not a
defect — but the documentation claims only that "genuinely disjoint ranges stay
separate" (`core.py:1224-1225`, `SKILL-CLI.md:371-372`), which a reader will
take as the complete partition rule. The honest pair is: disjoint stays
separate, nested is absorbed by the outer span. With `symbol_rows` from fold 6
the absorption becomes visible in the payload instead of silent — which is a
second reason to land fold 6. Fold 6 carries the sentence (one clause in the
`core.py` docstring and in `SKILL-CLI.md`).

### S-6 — pass, with one pin tightened — test quality

Ruled on the three questions asked:

- **The #24 test is the honest way to pin that branch.**
  `test_re_resolved_symbol_mode_matches_the_exact_name_only` monkeypatches
  `ts_chunker.chunk_text` to emit one chunk named `pkg.prepare`, edits the file
  so the changed-file path runs, and requires the refusal. It is synthetic
  because the real extractor cannot produce a dotted row — the test asserts on
  the *rule at the seam that could reach the branch*, which is the only place
  the rule has content; a "natural" fixture is impossible by construction, and a
  test that only asserted the extractor stores leaves would leave the deleted
  branch's semantics unpinned. It is a behaviour pin, not a shape pin: the base
  tree passes the same chunk through and answers it, so the assertion fails
  there (gate bar 2, `probe-base.txt`). Accepted. The residue is that the
  premise — extraction stores the leaf — is asserted only in a comment and in
  the test's own docstring. Checked: `tests/test_csharp_symbols.py:150-151`
  hedges (`assert find_symbols("Estate.Sim") or find_symbols("Sim")`), so the
  store-side rule is *permitted* rather than *pinned*. Fold 8 tightens it to
  require the leaf and require the dotted name to be absent — which is what the
  corrected comment now promises.
- **The eval test's use of private helpers is acceptable.** It calls
  `_symbol_ranges` and `_hit_correct` directly, which is implementation-named,
  but there is no public scoring surface to call instead and a full `eval` run
  would not isolate the second-row case. It also pins the control (a hit in the
  gap is wrong) and asserts the rows are genuinely disjoint before scoring, so
  it cannot pass vacuously. Accepted as behaviour at the harness's own seam.
- **The padding tests pin behaviour, not implementation.** They assert the exact
  span lists and, in two cases, assert the precondition that makes the pin
  meaningful (the padded windows really do touch; the two rows really are
  disjoint). Exact span tuples are the observable contract, not an internal
  detail. Good.

No test pins `_merge_symbol_ranges` directly; it is exercised only through
`code_context`, which is the right level — the merge is a rule about answers, not
a utility with its own contract.

### S-7 — observation, no fold — three fixtures repeat the same teardown

`disjoint_rig` and `decorated_rig` (`tests/test_symbol_resolution.py:160-201`)
each re-implement the teardown already in `repeated_rig`. A single parameterised
rig factory would carry all three. Not worth a fold in a doc-and-pin round; noted
so the next round that grows this file does not add a fourth copy.

### S-8 — pass — Terminology rule and surface discipline

No coordinate labels, no round or wave numbering in code identifiers, test names
or file names; `runs/issue24-26/` is issue-number provenance, which the rule
allows and the repository already uses for `runs/issue21/`, `runs/issue22/`,
`runs/issue23/`. Test names are natural language
(`test_a_decorated_definition_is_one_segment`,
`test_context_padding_keeps_two_close_definitions_apart`). The repository carries
no coordinate-label lint script (`fd -g '*lint*'` finds none at depth 2), so
there was nothing mechanical to run; the review's own read covers it. Publishing
`runs/issue24-26/` is consistent with the 134 already-tracked files under
`runs/`, and the captures carry a declared `paths elided` header with no host
paths, agent names or board ids.

---

## Spec axis

### P-1 — #24: delivered, with the record's reason rescoped

Fidelity: the branch is deleted, the exact-name comparison is the whole rule,
the extractor comment is corrected, the test pins the rule. Nothing asked for is
missing, and the record correctly notes that the C++ helper needed no change.
The one correction is the truth of the *why* (ruling 3, fold 7). Bar 2 stands.

### P-2 — #25: delivered exactly as specified

`_symbol_ranges` reads every row uncapped, `_hit_correct` accepts any overlap,
the disjoint-rows fixture pins a hit in the second row as correct and a hit in
the gap as wrong, and the past-the-cap fixture still passes. The issue's
suggested direction is implemented literally; nothing beyond it was added. The
surviving single-span wrapper is standards finding S-2, not a spec gap.

### P-3 — #26: behaviour delivered, second requirement not delivered

Both halves of the issue's first option are implemented and pinned: overlap-or-
touch merges per contiguous run, and the decorated case answers as one segment
with one honest range (live: `cli.py --symbol get_code_context` → one segment
`1435-1452`, where the base gave `1435-1452, 1436-1452`). The requirement to make
the decision visible in the JSON contract was answered in prose only; ruling 1
and fold 6. This is the one outstanding item of the round, and #26's closing is
contingent on it.

### P-4 — padding order: delivered and load-bearing

`4b5aa16` moves the merge ahead of padding, and the three pins hold the property
that matters: a display flag cannot change segment identity. The gate's ruling on
the residue (overlapping padded spans) is confirmed; only its documentation
conclusion is overturned (ruling 4, fold 2). Note the order dependency is real,
not theoretical: at `7d61da0` six definitions collapsed to one span at
`--context-lines 6`, so this commit is not a tidy-up of the previous one — it
fixes a defect the previous commit introduced in the same round. That is also
why `CHANGELOG` fold 4 must not present the order change as a regression fix to
a shipped version.

### P-5 — scope: nothing beyond the three issues; one thing the issues named is missing

Nothing in the diff is outside the three issues. `ts_chunker.py` is the #24
comment; `_merge_symbol_ranges` and the padding order are #26; `_symbol_ranges` /
`_hit_correct` are #25; `SKILL-CLI.md` and `CHANGELOG.md` are the documentation
the issues name. No version bump, no schema change, no chunker behaviour, no
reindex — confirmed by reading the hunks, not only the gate's `rg`.

Missing against the issues' own words:

1. the JSON-contract visibility of the merge (#26) — fold 6;
2. the consequence for padded overlap in `SKILL-CLI.md` — not named by an issue,
   owed by truth, fold 2;
3. `docs/json-schema.md` was not updated by a round that changed what a segment
   means. The document is already stale independently of this round: it lists
   segment keys `"text"` and `"symbol"|null` (`docs/json-schema.md:46-47`) while
   the emitted segment carries `"lines"` and no `symbol` key (see
   `runs/issue24-26/live-get-code-context-cli.json`). Pre-existing, not
   introduced here, and out of this round's diff surface — but fold 6 has to
   touch that document anyway, so it should leave it correct rather than
   correct-plus-two-stale-keys.

---

## Fold list

Each fold is atomic: one intent, one commit-worthy change. Folds 3, 4, 5, 7 and 8
are documentary or test-pin. Fold 6 is the only additive change to behaviour,
and it adds a field without changing an answer.

**Fold 1 — `SKILL-CLI.md:555-556`: restore the verb and the run wording.**

Old:
```
  ranges of that one name that overlap or touch merged into one span each, so
  a decorated definition answers as one segment; the rows' own ranges merge
```
New (rewrap the paragraph after editing):
```
  ranges of that one name that overlap or touch are merged into one span per
  contiguous run, so a decorated definition answers as one segment; the rows'
  own ranges merge
```

**Fold 2 — `SKILL-CLI.md:377`, after "Spans are listed in start-line order.": state the
padding consequence.**

Add, in the same bullet:
```
  Padded spans of nearby definitions may overlap: a segment is a padded
  definition, not a disjoint window, so concatenating segment bodies can repeat
  lines. `--context-lines` never changes how many segments answer.
```

**Fold 3 — prune `evalharness._symbol_range`.**

- `src/code_indexer/evalharness.py:120-130` — delete the function and its two
  preceding blank lines.
- `tests/test_symbol_resolution.py:237-240` — replace
  `rng = evalharness._symbol_range(m, "late_definition.py", SHARED_NAME)` /
  `assert rng == (5, 6)` with
  `ranges = evalharness._symbol_ranges(m, "late_definition.py", SHARED_NAME)` /
  `assert ranges == [(5, 6)]`.
- `tests/test_symbol_resolution.py:7` — `evalharness._symbol_range` →
  `evalharness._symbol_ranges` in the module docstring.
- Run `uv run pytest tests/test_symbol_resolution.py -q -W error`.

**Fold 4 — `CHANGELOG.md:42-49`: state the rule, not a shipped regression.**

Replace "`code_context` padded every row's range with `context_lines` before
calling `_merge_symbol_ranges`, whose adjacency test (`start <= previous_end +
1`) then fused two genuinely disjoint definitions of the requested name that sit
a few lines apart: with `--context-lines 2` and definitions four lines apart, the
padded windows touch and the whole file collapsed into one segment that included
the code between the definitions." with:

```
  `_merge_symbol_ranges`' adjacency test (`start <= previous_end + 1`) fuses any
  two windows that touch, so merging AFTER padding would fuse two genuinely
  disjoint definitions of the requested name that sit a few lines apart: with
  `--context-lines 2` the padded windows touch and the whole file would answer
  as one segment including the code between the definitions.
```

**Fold 5 — `core.py`: drop the two "stated in" pointers, keep the contract sentence.**

- `src/code_indexer/core.py:66-68` — delete "It is stated in ``Core.code_context``
  and in SKILL-CLI.md."; keep "The merge is part of the answer, never a silent
  convenience." and the rest of the paragraph.
- `src/code_indexer/core.py:1228-1230` — delete "The merge is stated here and in
  SKILL-CLI.md — "; keep "It is part of the answer, never a silent
  normalisation."

**Fold 6 — make the merge visible in the JSON contract (#26's outstanding requirement).**

One unit of work, three surfaces, one commit:

- `src/code_indexer/core.py` — `_merge_symbol_ranges` returns
  `list[tuple[int, int, int]]` (`start`, `end`, `symbol_rows`), incrementing the
  count for each row folded into a span; the segment builder
  (`core.py:1291-1303`) adds `"symbol_rows": n` to a segment **only when
  `symbol` is set**, so line mode's shape is unchanged.
- `src/code_indexer/core.py` docstring (symbol-mode paragraph) — add the
  consequence clause: "each segment carries `symbol_rows`, the number of stored
  rows it merged, so the merge is auditable and never silent; a definition
  nested inside another of the same name is absorbed by the outer span."
  (`SKILL-CLI.md`, same paragraph as fold 2, gets the matching clause.)
- `docs/json-schema.md:42-48` — rewrite the `get-code-context --json` shape to
  what the code emits (`lines`, optional `error`) plus
  ``"symbol_rows": int`` present in symbol mode only; keep `schema: 1` (additive
  field, no bump).
- `CHANGELOG.md` — replace "The JSON answer shape (`file` / `stale` /
  `re_resolved` / `segments`) is unchanged" with the added field and its meaning.
- `tests/test_symbol_resolution.py` — in
  `test_a_decorated_definition_is_one_segment` assert
  `segment["symbol_rows"] == 2`; in the disjoint-rows test assert `[1, 1]`; in a
  line-mode assertion assert the key is absent.
- Run `uv run pytest tests/ -q -W error` and `uvx ruff check src tests`.

**Fold 7 — `runs/issue24-26/gate-verification.md:69-71`: scope the differential.**

Replace "So the suffix branch was live behaviour on the changed-file path, not
merely dead code: on the base the command answered a symbol that is not the
requested name. Tip refuses it." with:

```
  So the branch's rule was real on the changed-file path even though no stored
  row could reach it: the probe hands the base tree a live chunk whose symbol is
  `pkg.prepare` — a row extraction never produces — and the base answers it,
  while the tip refuses it. The issue's "can never fire" therefore stands for
  every indexed file, and deleting the branch changes no real answer; what the
  differential adds is proof that the rule being deleted was the suffix match
  and nothing else.
```

**Fold 8 — `tests/test_csharp_symbols.py:150-151`: pin the leaf, not either form.**

Replace `ns = cs_project.find_symbols("Estate.Sim", symbol_type="namespace")` /
`assert ns or cs_project.find_symbols("Sim", symbol_type="namespace")` with:

```
    assert cs_project.find_symbols("Sim", symbol_type="namespace"), \
        "the leaf of a qualified namespace is the stored name"
    assert not cs_project.find_symbols("Estate.Sim",
                                      symbol_type="namespace"), \
        "the dotted qualified name is never stored"
```

Run the single test after the edit; if the dotted read returns a substring match,
pass `substring=False` to both calls rather than weakening the assertion.

---

## What I checked and held

- **Gate bar 1** (512 passed, 2 deselected, `-W error`; ruff clean) — accepted as
  measured; not re-run. Independent check: no capture in the round shows a
  warning line, and the diff adds no new module-level import beyond `ts_chunker`
  in the test file.
- **Gate bar 5** (surface discipline, no reindex) — re-derived by reading the
  hunks: `EXTRACTION_VERSION` is not in the diff, `code_context`'s return
  statement and segment builder are untouched apart from the added range
  computation, and the store's rows are unchanged. Confirmed.
- **Gate bar 6** (no collateral damage) — the diff leaves line mode's branch
  untouched (`elif start_line is not None`), so the only shared code path is the
  segment builder, whose behaviour for line mode is unchanged. Confirmed by
  reading; the gate's captures agree.
- **`_symbol_from_declarator`** — read: its docstring already describes the
  `Foo::bar` → `bar` rule correctly, so leaving it untouched was right and the
  record's account of "the two helpers" is accurate.
- **`find_symbols` call sites** — `code-indexer changed-symbols --project
  code-indexer --base 8254894 --impact` reports the round's surface as
  `_merge_symbol_ranges` (impact: `code_context`), `_symbol_ranges`,
  `_symbol_range`, `_hit_correct` (impact: the one test), and
  `_symbol_from_node` (impact: `_walk` and the comment only), plus the new tests.
  Cross-checked with `rg` on the exact literal: `code_context` and
  `evalharness` are the only consumers of the exact-name read the round touched,
  and no other caller relied on the dotted suffix.
- **Ordering** — segments are ascending by start line before and after; the tip
  sorts explicitly inside the merge where the base relied on store row order.
  The gate's captures show no reordering in any fixture, and the added sort is a
  strengthening.
- **Arithmetic** — the one figure this review re-derived (the padded-overlap
  duplication share) is quoted from the gate and checked: `calc eval
  '46/74*100'` → `62.1622`.
