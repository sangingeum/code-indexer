# Index-surface rulings

Five questions about what the index surface may claim, ruled together because
none of them can be answered by the leg that measured it: each is a choice
about the contract, not a defect with one obvious repair. Every measurement
below was taken at the tip this record landed on, and each ruling says what was
landed now, what it means for an index already on disk, and what a later card
would have to change.

One rule governed which rulings could move behaviour in this pass: behaviour
changes only where the change is contained in one function and a test that
fails on the old behaviour can state the new contract. Question 1 met that bar
and landed. Questions 2, 3 and 5 are answered in prose, with the behaviour left
where it is and a follow-up named; question 4 is a quote correction the owner
ruled.

No architectural record other than the quoted banner in ADR-0003 section 3 was
touched. Reversing any ruling here, or writing one of them into an ADR, needs
the owner's word: ADR-0003 section 2 binds the staleness check to the scanner's
own rules, and ADR-0002 fixes the row identity and the argument shapes of the
name reads, so both records would have to move with a reversal.

## One: how nested ignore files compose

### Measured

`scan_project` composed the per-directory ignore files with
`any(spec.match_file(...) for spec in specs)`: a file was skipped if ANY spec
between its directory and the root matched it, which makes an exclusion
absolute. Reproduced with a root `.codeindexignore` holding `secret.txt` and a
nested `src/.codeindexignore` holding `!secret.txt`: `git check-ignore` says
`src/secret.txt` is NOT ignored, and the scan dropped it. Git does the
opposite -- a deeper negation re-includes what a shallower spec excluded -- and
the scanner's own docstring claimed to follow git, while
`tests/test_repo_exclusions.py` pinned the divergence under the name
`test_a_root_negation_cannot_re_include_a_nested_exclusion`.

### Ruling

**Git-faithful.** The specs are consulted from the root outwards and the last
matching rule decides; a deeper `!pattern` re-includes.

### Why

The scan set is not a private opinion about the tree: callers reason with
`git check-ignore`, and the staleness check reuses the same include and exclude
rules, so a divergence removes a file from two surfaces at once. Worse, it
removes it invisibly: an empty answer says "the name is absent from an index
that parsed M of N files", and N is computed from the same scan set, so no
coverage block can reveal that a file was never a candidate. Git's order is
also the only one in which a re-include is expressible at all, which is the
whole reason a nested ignore file exists.

The cost is named and paid once: a project that relied on an absolute nested
exclusion now scans more files, and the manifest agrees only after a pass.

### Landed

- `src/code_indexer/scanner.py` -- `_ordered_specs` (root first) and
  `_ignored` (last matching rule wins, via `check_file`'s pattern index); the
  directory prune and the file loop both use them. The function's docstring
  now states the composition instead of claiming git "closely enough", and the
  dead second docstring below it was folded into the real one.
- `skill/references/indexing.md` -- the exclusion paragraph states the
  composition rule beside the anchored-pattern sentence.
- `tests/test_repo_exclusions.py` -- the test that pinned `any()` is replaced
  by `test_a_nested_negation_re_includes_what_a_shallower_spec_excluded`,
  which fails on the old composition, and a second test,
  `test_the_scan_agrees_with_git_check_ignore`, writes the same rules to
  `.gitignore` and `.codeindexignore` in two directories and asserts the scan
  set equals what `git check-ignore` calls unignored.

### What it means for an index on disk

A scan set can only grow under this rule, never shrink, so no row is orphaned
and nothing is dropped from a manifest. Files a project's nested negations had
been losing become `added` staleness reasons on the first read after the
upgrade and gain rows on its next pass; a full `reindex-project` produces the
same set from scratch. No manifest schema change, no extraction-version bump.

### Follow-up

Two neighbouring divergences were measured while ruling and deliberately left
unruled, because fixing either widens the change beyond one composition
function:

- a nested spec's patterns are matched against root-relative paths, so an
  anchored pattern (`/secret.txt`) inside a nested file can never match;
- the prune loop now consults the root spec where it previously consulted only
  the directory and its descendants, so a root-level directory exclusion prunes
  instead of walking the tree and skipping each file. The file set is
  identical; only the walk is shorter.

Both want a card if the owner wants the remaining distance to git closed. The
scoped-indexing include and priority patterns are untouched.

## Two: who owns a name's fan-in

### Measured

`Manifest.file_fan_in` computes the name half of the aggregate as
`SELECT name, MIN(file) ... GROUP BY name`, so every reference to a name is
attributed to the lexicographically first defining file. A test or spec module
that sorts before the source owning the same name takes that name's fan-in,
and no query-time ordering repairs a number computed from the wrong owner --
the class ladder decides which files fill the ten hotspot slots, not what the
numbers say. `tests/test_ordering.py` keeps a source-owned name in its hotspot
assertion for that reason, and `tests/test_overview.py` pins the unscoped
ownership rule outright.

### Ruling

**The aggregate is declared per name.** The file column names the name's
representative definer, and a reader concludes from it that the file defines
that name -- never that the code lives there.

### Why

The reference store records the name a reference observed and leaves
`target_symbol_id` unwritten, so which definition a reference meant is not
knowable from the row; a per-definition split would be a guess dressed as
attribution. Naming the aggregate for what it is costs nothing and removes the
misreading, and the class ladder already does the work that keeps a test module
out of the hotspot list. Doing it properly is an index-time change (populate the
resolved target, aggregate over it) that would force a reindex for a number
whose reader-facing meaning would not move, so it is a card, not this pass.

### Landed

- `src/code_indexer/manifest.py` -- the `file_fan_in` docstring declares the
  per-name contract, why it is the contract, and what the row may be read to
  mean.
- `src/code_indexer/overview.py` -- the hotspot comment says the same in the
  place a reader of the overview lands.
- `skill/references/semantic.md` -- the ordering caveat no longer calls it an
  "attribution artefact"; it states the rule and the conclusion a reader draws
  when the definer is a test module.
- `docs/json-schema.md` -- the `hotspots.fan_in` paragraph names the per-name
  aggregate beside the completeness guarantee.

No behaviour, no test change: the existing whole-store assertion in
`tests/test_overview.py` already pins the ownership rule, so the prose and the
code now agree on it.

### What it means for an index on disk

Nothing. No number moves, no reindex is needed.

### Follow-up

A card only if the owner wants per-definition attribution: populate
`refs.target_symbol_id` through the resolver where a name resolves to one
definition, aggregate the name half over target rows, and reindex -- an
index-time change with its own manifest and reindex cost.

## Three: a dotted qualified name as a locator

### Measured

`find-symbol` reports `qualified_name` dotted (`Core.search`) and accepts
`--container Core`. At the tip, `get-code-context --symbol Core.search` refuses
with `NotFoundError: no indexed definition for 'Core.search'` and exits 1. The
query reference documented the bare-name form, called the dotted spelling "not
a locator here", and quoted an error (`InternalError: symbol 'Core.search' not
indexed in ...`) the project-wide form does not raise.

### Ruling

**The bare declaration name stays the locator on every name read**, and the
spelling is documented in one place.

### Why

The help text promises one argument shape across `find-symbol`,
`find-definition` and `get-code-context`, so teaching one of the three a dot
grammar would make the same argument mean two things. The store cannot honour
the dotted spelling anyway: no row carries a dotted `name`, only the
`qualified_name` a read reports back. The two-step costs one call in the one
case where a name is ambiguous, and `--container` -- which `find-symbol`
already exposes -- is the disambiguator.

### Landed

- `skill/references/query.md` -- the paragraph is now the single statement:
  the row's `name` is the locator, the dotted `qualified_name` is a field a
  read reports, the refusal quoted is the one measured at the tip.
- `src/code_indexer/cli.py` -- the `get-code-context --symbol` help and the
  `find-symbol --container` help point at the same rule, so the two surfaces
  cannot describe different spellings.

### What it means for an index on disk

Nothing. No row, no answer and no exit code changes.

### Follow-up

None. If the owner later wants dotted locators, the change belongs to all
three name reads at once, with one resolution rule: split at the last dot,
filter the definitions of the trailing name by `container ==` the prefix, and
refuse with the candidate list when more than one row survives -- the same
refusal shape an ambiguous bare name uses. That is a card.

## Four: the banner ADR-0003 quotes

### Measured

ADR-0003 section 3 quoted the pre-fix line: a parenthesised fact list with a
timestamp and a separate `Refresh:` sentence. The shipped line, at
`src/code_indexer/staleness.py`, is one line -- count phrase, meaning sentence,
repair -- and keeps "answers describe the index, not the tree".

### Ruling

**Amend the quote** (owner's ruling, same date), keeping the meaning sentence,
which section 3 makes normative.

### Landed

- `docs/adr/0003-read-only-and-staleness-contract.md` -- section 3 now quotes
  the shipped one-line form, with a placeholder path, and the header carries an
  `Amended:` bullet naming what the quote used to say and why the meaning
  sentence is not optional.

### What it means for an index on disk

Nothing. The line the tool prints did not change; only the record's copy of it.

### Follow-up

None.

## Five: does a path prefix narrow the coverage scope

### Measured

`overview --path-prefix src/code_indexer` and `skeleton src/code_indexer`
answer with `coverage.scope: project` and the whole index's counts (186 files
on the project measured), while `outline`, `get-code-context` and
`find-symbol --file` answer `scope: answer`. A caller who narrowed the question
still reads project-wide coverage.

### Ruling

**Project-wide is correct for a prefix.** A prefix is a filter over a
project-scoped answer, and the documentation says so in one place.

### Why

The block's job is to say whether an absence in THIS answer can be trusted, and
a project-wide count is the conservative superset: a file outside the prefix
can only make a prefix answer look less complete, never more. Narrowing would
need the withheld list intersected with the prefix (a withheld file has no row
at all, so the intersection cannot be derived from rows) and would cost the
comparability that makes the counts checkable against `index-status`. The price
of the choice is that a prefix answer over-warns when files elsewhere in the
project are unparsed -- the safe direction.

### Landed

- `skill/references/machine-output.md` -- the coverage section states that a
  prefix narrows the files an answer reports, not the scope or the counts, and
  why.
- `tests/test_read_coverage.py` --
  `test_a_prefix_narrows_the_answer_not_the_coverage_scope` pins both shapes
  (`--path-prefix` and the `skeleton` subtree argument): the answer's file
  count narrows, `scope` stays `project`, the coverage count stays the index's.

### What it means for an index on disk

Nothing. No number moves, no reindex is needed.

### Follow-up

None. A caller that wants a subtree's own totals narrows its own reading of the
answer; the block does not report them.

## What this record does not decide

- Any other architectural-record amendment. The banner quote was the one the
  owner ruled for; a ruling that needs an ADR to move is named as needing the
  owner's word, not written here.
- The two scanner divergences listed under the first ruling: anchored patterns
  inside a nested ignore file, and the prune's earlier stopping point.
- Per-definition fan-in, and dotted symbol locators. Both are described above
  precisely enough to card without re-deriving the measurement.
