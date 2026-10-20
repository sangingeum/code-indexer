# Two-axis review — issue #23 (get-code-context --symbol resolution)

Verdict: **PASS WITH FOLDS**

- Under review: `f9e819b..c284906` — `81f0c8c` (fix), `c5d60fb` (docs),
  `51e98c0` (gate record), `c284906` (gate-record redaction). All four
  `%G? = G`; `origin/main` = `c284906`.
- Changed surface: `src/code_indexer/core.py` (`Core.code_context`),
  `src/code_indexer/evalharness.py` (`_symbol_range`),
  `tests/test_symbol_resolution.py` (new, 6 tests), `CHANGELOG.md`,
  `SKILL-CLI.md`, `runs/issue23/*`.
- Issue #23 stays open.

The fix is correct, minimal, and pinned by tests that genuinely fail on a
base build. Two documentation folds and one test-hygiene fold are owed, plus
the publication remedy in ruling 5. Nothing here is a reason to reopen the
behaviour.

## Method, and what this review did not do

Read: the repo `AGENT.md`, issue #23, `runs/issue23/gate.md` and the tracked
evidence files, the full source/doc diff, `Manifest.find_symbols` and
`definitions_of`, `Core._fuse_lexical_dense`, `evalharness.load_queries` /
`_hit_correct`, `ts_chunker._symbol_from_node` / `_symbol_from_declarator`,
`overview.format_overview`, the new test module against its sibling.

Ran (narrow, read-only; no source, test or index write):

- `uv run pytest tests/test_symbol_resolution.py tests/test_units.py tests/test_unwatch.py -q -W error` → 28 passed (state-leak probe, fold 1).
- `code-indexer get-code-context src/code_indexer/cli.py --symbol get_code_context --json --skip-stale-check` → 2 segments, `1435-1452` and `1436-1452` (fold 3).
- `code-indexer find-symbol --help` (cap wording).
- Read-only SQL against the project's manifest symbol table and the manifest
  tables of the other ten indexes registered on this host: dotted-name count,
  same-name-per-file counts, ast/regex mixtures, and the row position of every
  symbol label in `eval/queries.jsonl`.
- `jq` over `runs/issue23/find-symbol-init.json` for row positions.

Not done, by rule: the gate's bars were not replayed, the full suite was not
re-run, and the filed repro was not re-issued — the gate record and the
dispatching round's fresh verification own those. I ruled on the gate record,
not on a second execution of it.

## Standards axis

**S1 (medium) — the new fixtures leak CLI module state between tests.**
`tests/test_symbol_resolution.py:88-89` installs a clean `cli._core` /
`cli._core_skip` at fixture setup, but `wide_rig`
(`tests/test_symbol_resolution.py:107-113`) and `repeated_rig`
(`:117-122`) only close the registry at teardown, and the CLI test itself sets
`cli._core = core; cli._core_skip = True` (`:158-159`) and never restores.
The sibling module this file was modelled on does restore it
(`tests/test_stale_safe_context.py:88-89`). After the module finishes,
`code_indexer.cli` still points at a `Core` whose index root was a pytest tmp
directory that no longer exists, with the staleness check skipped. It does not
bite today (the probe above passes; the later modules that touch the CLI either
reset `_core` themselves or `setattr` `_core_skip`), but the safety is
ordering-dependent, not designed. Three lines in each fixture's teardown, per
the sibling's pattern, removes the dependence.

**S2 (low) — "every definition" is one word stronger than the mechanism.**
`CHANGELOG.md:23-27`, `src/code_indexer/core.py:1187-1191` and
`SKILL-CLI.md:364` promise every *definition*; the code returns every matching
symbol *row*, and one decorated definition contributes two rows. Measured on
the live index:

```
$ code-indexer get-code-context src/code_indexer/cli.py --symbol get_code_context --json --skip-stale-check
segments: 1435-1452, 1436-1452     (one definition: def get_code_context at cli.py:1436)
```

The duplication is pre-existing and orthogonal to this fix (it happened inside
the old five-row cap too); what the fix changes is that the prose now promises a
count the sibling surfaces of the codebase already overstate. Say "every
matching symbol row (a decorated definition surfaces as two overlapping
ranges)", and a caller counting segments to count definitions has the right
expectation.

**S3 (low) — matching asymmetry, noted, not a gap.** `core.py:1212-1213`
(re-resolve on a changed file) accepts `chunk.symbol.endswith("." + symbol)`
while the manifest path (`core.py:1226-1227`) is exact-name only. I went
looking for the case where the two disagree and could not construct one:
extraction never stores a qualified name — `ts_chunker.py:286-290` takes the
leaf of a C# `qualified_name`, `ts_chunker.py:317-320` takes the last part of a
C++ `Foo::bar` — and a read-only count across all eleven manifest tables on
this host (6 841 rows in the largest, a C# tree) found **0** dotted names. So
the asymmetry is unobservable on any index this household holds; it is dead
code, and the comment at `ts_chunker.py:286-287` ("find_symbol callers can
match either the full name or the leaf") is the false half of it. Leave the
behaviour, clean the branch and that comment in a follow-up.

**S4 (low) — two internal references survived the redaction.** `c284906`
correctly removed the verifier name and the checkout paths from
`runs/issue23/gate.md`, but `runs/issue23/gate.md:9` still cites a local work
board card id and `:198` refers to "the brief" — the dispatch text. Neither is
repo-documentable provenance: a card id is local coordination bookkeeping
(house rules treat board content as local working material), and "the brief" is
private working material. Neutral phrasings ("the gate's card", "the
dispatching round's description") are enough.

**Not findings, checked and clean.** Terminology: no coordinate labels in the
new identifiers, test names or file name (`wide_rig`, `repeated_rig`,
`test_the_name_population_outgrows_the_default_cap` are all concept words).
Git hygiene: no agent names, no VM paths in the code/doc/test diff. Commit
discipline: four signed commits, each self-consistent; the docs commit does not
reference a symbol that lands later. Lint/test: ruff and warn-clean pytest are
the gate's bar 6 and are green at the tip (not re-run here). The new module
follows the repo's test pattern (path insert, stubbed seams, `__main__` block)
exactly as its sibling does.

## Spec axis

**P1 (medium) — the eval half of the fix moves a shipped score, and no record
says so.** `CHANGELOG.md:18-22` states the harness defect correctly. What is
missing is the consequence. `eval/queries.jsonl:4` (query
`q-concept-04`) labels `src/code_indexer/manifest.py` / `__init__`; that name
has 40 rows and `manifest.py`'s is **row 33**
(`runs/issue23/find-symbol-init.json`, position 33) — i.e. the one shipped label
the old `_symbol_range` could never resolve, so overlap scoring was silently
unavailable for it and only an exact `hit.symbol` match could score the query
correct. Of the 33 symbol labels in the shipped set, this is the only one whose
name exceeds the cap (verified by counting rows per label). Therefore:

- the fix does change eval scoring, for exactly one shipped query;
- an `eval-compare` against any report recorded before `81f0c8c` is not
  apples-to-apples for that query.

No eval report is tracked in the repo (checked), so this is a documentation
fold, not a data repair: one clause on the changelog bullet.

**P2 — scope is drawn right, and slightly wider than the filed repro by the
issue's own instruction.** The issue asked to resolve the symbol from the
file's own rows, "no cap and no population question, and the answer shape stays
the same". The diff does exactly that, plus the harness path the issue itself
named as the same shape, plus the `[:5]` removal the issue's "no cap" implies.
Nothing else moved: `find_symbols`' default stays 25, browse and substring caps
stay, the fused-ranking pin stays, no schema/chunker/embed fingerprint changed
(`EXTRACTION_VERSION` 4 at both ends), no reindex owed. Behaviourally that is
the whole claim, and the existing test battery plus the new pins cover it.

**P3 — the issue's wider claim now holds for store reads, not for presentation.**
The release-note sentence the issue warned about is now true of the store: the
three capped reads that remain (`find_symbols`' default, the browse/substring
cap, the fused pin) are either announced in `--limit` help or provably
cap-invariant (prefix of a total order). The text formatter still slices
`overview` output to 5/6 entries (`overview.py:225,231,236,246,249,253,257`)
without a marker, but the JSON behind it is complete and uncapped, so no *store
read* misreports completeness. A release note should say "store reads", not
"any answer".

## Rulings

**1. The `[:5]` segment cap: removing it was the right call, and no announced
cap was owed.**
The cap was not a bounded, documented answer — it was `[:5]` on a list the
caller never sees, applied at two different places (manifest rows and
live re-resolve), and it is exactly the defect class the issue filed: an
unannounced truncation that reads as a complete answer. The command has no
`--limit` for `--symbol` and no help text that mentions five; announcing a cap
here would have meant inventing an option for a set that is already small and
finite (one file's own definitions of one name). It also would have left the
harder half unfixed: with an announced cap, a caller who wants the sixth
definition has nowhere to go. Removing it keeps the answer shape intact — the
same keys, the same segment shape, the same order — while the *content* becomes
complete. Our house precedent agrees: issue #22 fixed the same shape by ruling
that a definition list is an answer, not a sample, and pushed caps up to the
caller. Data point on how live the boundary was: across the eleven indexes on
this host the worst same-name-per-file count is **5**
(`tests/unit/test_ops.py` in the calculator project; `Vivarium.Sim/Census/PawnInfoQueries.cs`,
symbol `Of`, in the C# tree) — the cap was one definition away from truncating
in a real project, not hypothetical.

**2. Matching asymmetry: leave it. Not a correctness gap today, dead code
tomorrow.**
The two paths cannot disagree on any name shape the extractor actually stores:
no qualified name ever reaches the symbol table (`ts_chunker.py:286-290`,
`:317-320`), and every index on this host has zero dotted names. So the
`endswith("." + symbol)` branch in the re-resolve path never fires, and there is
no file whose answer depends on whether it happens to be dirty. I would not
spend a fix on it; I would spend a cleanup: delete the branch and correct the
`ts_chunker.py:286-287` comment, which claims callers can match "either the
full name or the leaf" when only the leaf is ever stored. Worth a follow-up
issue (house tool feedback), not a blocker for #23.

**3. `_symbol_range` returning the first row is what the harness means, and the
fix changes exactly one shipped score — correctly.**
The harness asks "where is this labelled symbol in this file" to decide whether
a hit overlaps it. Without a file+name scope you cannot answer it; with the
scope, the name's rows in that file are ordered ast-first then `start_line`, so
`rows[0]` is the earliest definition — a reasonable reading, and the same one
the old code produced when the row was inside the cap (it returned the first
in-file match of the same total order). So no existing score regresses; the
change is confined to labels whose row sat past the cap, which is exactly
`q-concept-04` (row 33 of 40). Caveat, pre-existing and untouched by this fix:
one row's range is only a proxy for the definition, and in every index I
measured the only within-file duplicates are decorator double-rows with
near-identical ranges (`locks.py` `project_lock` 27-51 / 28-51;
`cli.py` `get_code_context` 1423-1440 / 1424-1440), so first-row scoring is
currently safe by the symmetry of that duplication rather than by design.
Follow-up worth doing: score against *every* row of the label in that file
(any-overlap), which is a one-line change over the list the fix now already
reads uncapped. See fold 2 for the documentation half.

**4. Uncapped output size: not a problem, and the alternative would be a worse
answer.**
The bound is one file's definitions of one name, not the project. Measured
maximum on this host is 5 (above); the realistic ceiling is a large C# file of
overloads or a test module of same-named helpers, i.e. tens of segments of a
few dozen lines each — well inside any agent loop's budget, and the caller can
narrow with a line range once it sees the ranges. Token blowout would require an
adversarial file, and for an adversarial file the honest answer is still the
honest answer: truncating it silently is precisely the defect that was filed.
The one genuine imperfection is duplication, not size — a decorated definition
appears twice (fold 3), so a name with many decorated definitions doubles before
it grows. De-duplicating overlapping ranges would be a defensible follow-up for
the same cleanup issue as ruling 2; it is not owed by #23.

**5. The published evidence carrying VM paths: publish a redacted copy, keep the
raw local and untracked.**
Ruling: option (a). The repo's publication rule is explicit — no VM-specific
paths in GitHub content, and where a verification record must be published, a
redacted version is the sanctioned form. Option (c) is the one thing that is not
available: the file is tracked and pushed, so "leave it" means the rule is
violated now, not deferred. Option (b) would work mechanically and is the wrong
instinct: ignoring a class of evidence to protect it from publication hides the
evidence a reader needs to believe the gate.

Concretely: `runs/issue23/gate-bar3-base-cli.txt` prints four absolute paths
(line 1's import path, and the `--project` argument of lines 2, 6, 10), and it
is not the only one — `runs/issue23/gate-bar3-base-pytest.txt:17` prints a
pytest tmp path that also carries the host user name. Redact both, each with a
declared header line stating that paths were elided in publication and where the
unmodified capture lives, and keep the raw captures out of the tree (untracked,
or under an ignored local directory — an ignore entry for this evidence class is
acceptable *in addition*, as belt and braces, but never as the remedy). The base
build provenance stays auditable because the header states what was changed and
the worktree removal is already recorded in prose at `runs/issue23/gate.md:87-89`.

The same remedy already applied to `gate.md`'s prose in `c284906` is correct and
complete for the paths it covered; it simply did not reach the captured output
or the two internal references in fold 4.

**6. What the gate missed, inside this fix's surface.**
Three things, none of them behavioural:

- the eval-score consequence and the one live label it touches (P1) — bar 5
  pinned `_symbol_range`'s shape with a unit test but never asked which shipped
  query exercises it;
- the path leak in the second base-build capture (ruling 5) — the redaction
  audit named one file, `rg` over `runs/issue23/` finds two;
- the fixture-state leak (S1), which no test-observable assertion can catch,
  only comparison against the sibling module.

I also confirm the two things bar 5 and bar 8 claimed, because they are the load
-bearing ones: the remaining capped reads have no post-cut filter (checked at
each site, plus `find_symbols` itself — ast-first then `name, start_line, file`,
a total order), and the JSON key set is untouched by this diff.

## Folds owed

1. `tests/test_symbol_resolution.py` — reset `cli._core = None` /
   `_core_skip = False` in `wide_rig` and `repeated_rig` teardown, and after the
   CLI test's `_core` assignment, mirroring `tests/test_stale_safe_context.py:88-89`.
2. `CHANGELOG.md:18-22` — add that `eval-compare` against a report recorded
   before this change is not comparable for `q-concept-04` (`manifest.py` /
   `__init__`, row 33 of 40), because its overlap scoring was unavailable then.
3. `CHANGELOG.md:23-27`, `core.py:1187-1191`, `SKILL-CLI.md:364` — say
   "every matching symbol row", and note that a decorated definition surfaces as
   two overlapping ranges.
4. `runs/issue23/gate.md:9,198` — replace the local card id and "the brief" with
   neutral role wording.
5. Publish redacted copies of `runs/issue23/gate-bar3-base-cli.txt` and
   `runs/issue23/gate-bar3-base-pytest.txt` with a declared "paths elided"
   header; keep the raw captures local and untracked.

Follow-ups (separate issue, not folds): delete the unreachable
`endswith("." + symbol)` branch in `core.py:1212-1213` and correct the
`ts_chunker.py:286-287` comment; score `_symbol_range` against every row of the
label in the file (any-overlap) instead of `rows[0]`; consider collapsing
overlapping symbol-mode ranges so a decorated definition is one segment.
