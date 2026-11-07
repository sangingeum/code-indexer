# Re-verify record — the documentation folds and the derived compact roster

Base `5178220`, fold tip `8b6d639` (`origin/main`, tracked tree clean). Fold
commits `16f0977` (four reference sentences, the derived compact cases, the
captures), `b4998d4` (the fold record), `8b6d639` (the mask in a mutation
capture). Source read first: the fold-now items in `runs/r3-review/record.md`
and `runs/r3-folds/record.md`. Every figure below was measured here at
`8b6d639`; the commands and their outputs sit in the captures named beside
them, masked (`<checkout>`, `<scratch>`).

## Verdict

**CHANGES REQUESTED — one documentation sentence, nothing else.** Four of the
five fold-now items landed in the reviewer's own words, the fifth landed with
a measured narrowing that keeps its intent, the derived roster is provably
able to fail in all three directions asked for, no source file moved, and
every standing bar is green at the tip. What does not hold is the sweep the
fold leg reports: the rule is stated in a *seventh* place, in
`skill/references/failures.md:47`, and there it still names `--json` together
with `--format` as a cause of `ArgumentError` with no "when they disagree"
qualifier -- the superseded rule, one row below a sibling item ("two
disagreeing spellings of one symbol") that does carry the qualifier. Measured
against the shipped behaviour, the unqualified claim is false: the agreeing
pair exits 0 with the payload. The fold's sweep searched `exact alias`, which
that row does not contain, so six sites agreed and the seventh was never in
the enumeration. One clause fixes it; see *Carried open*.

## Bar 1 — every fold-now item landed as specified

| # | location | shipped text | verdict |
|---|---|---|---|
| 1 | `skill/references/query.md:110-114` | "Every read command here takes `--format text\|json`, and `--json` is the EXACT alias of `--format json`: both spellings render the same bytes. Passing both is accepted when they agree (`--json --format json` asks the same question twice) and is `ArgumentError`, exit 2, when they name different formats -- never a silent precedence, one invocation, one answer format." | **PASS** — the reviewer's replacement verbatim, re-wrapped only |
| 2 | `skill/references/semantic.md:56-59` | "`--json` is its exact alias; passing both is accepted when they agree and is `ArgumentError`, exit 2, when they name different formats (see [machine-output.md](machine-output.md))." | **PASS** — verbatim, including the markdown link the reviewer wrote |
| 3 | `CHANGELOG.md:29-30` | "... no reindex, and every invocation that worked keeps its answer except the one combination named above." | **PASS** — the reviewer's clause, word for word |
| 4 | `skill/references/machine-output.md:61-63` | "A compact symbol record carries the file, the range, the name and the kind; the text form's `confidence`, `evidence` and signature are not part of it -- read `--format json` when those are the question." | **PASS** — verbatim; `<confidence>`, `<evidence>` and the signature are named as absent, as asked. Measured: `find-symbol build_overview --format compact` prints `src/code_indexer/overview.py:60-83 build_overview (function)`, the text form adds `confidence=exact, evidence=ast` and the signature (`probe-*` captures) |
| 5 | `tests/test_output_format.py:446-500` | four cases: `test_the_compact_roster_is_a_subset_of_the_reads_that_take_the_flag`, `test_compact_is_offered_exactly_where_the_roster_says` (parametrised over `sorted(FORMAT_ROSTER)`), `test_the_only_reads_that_answer_compact_unlisted_are_the_two_aliases`, `test_the_documented_compact_roster_matches_the_frozenset` | **PASS, with a measured narrowing** — see below |

Item 5 was written as "from `sorted(READ_COMMANDS)`"; the landed case runs
over `FORMAT_ROSTER` (the 19 reads that carry the shared flag) and pins the
other three by name (`READ_COMMANDS - FORMAT_ROSTER == {"deps", "eval",
"eval-compare"}`). The narrowing is not a weakening but the only true form: I
re-measured the reason at the tip -- `deps --format compact` is not the shared
flag at all and `eval`/`eval-compare` declare no `--format` -- so a literal
`READ_COMMANDS` sweep would assert a refusal those three never reach. Both
halves the reviewer asked for exist: every flag-holding read is a case, and
the doc-vs-frozenset equality is its own test. The reviewer had carded this
item ("Carded, not folded"); it was folded anyway, which is more than asked
and is measured in bar 3.

## Bar 2 — the rule sweep, enumerated from the tree

`rg -n -i 'exact alias' skill CHANGELOG.md SKILL-CLI.md` finds six sites (one
of them, `query.md:110-111`, only because the phrase wraps across two lines).
A second sweep for the refusal vocabulary -- `together with|passing both|name
different formats|disagree` -- was needed to find a site the fold leg's sweep
could not reach. Capture: `rule-sites.txt`.

| site | what it states | verdict |
|---|---|---|
| `SKILL-CLI.md:73` | alias; accepted when it agrees; exit 2 when the two name different formats | canonical statement; **holds** |
| `skill/references/machine-output.md:36-41` | the same rule, fully, plus the message's reason | **holds** |
| `CHANGELOG.md:8-16` | the same rule plus the one behaviour that changed | **holds** |
| `skill/references/query.md:110-114` | the same rule | **holds** |
| `skill/references/semantic.md:56-59` | the same rule, with a pointer to `machine-output.md` | **holds** |
| `skill/references/indexing.md:203` | `--json` is the exact alias -- no conflict rule | alias only; **true** |
| `skill/references/failures.md:47` | lists "`--json` together with `--format`" among the causes of `ArgumentError`, exit 2, with no qualifier | **FAIL — the superseded rule, stated as current** |

The row is prose the round itself wrote (the line was replaced in the surface
commit, not inherited), and the fold did not touch it. Its sibling item in the
same row, "two disagreeing spellings of one symbol", does carry the qualifier,
so the omission reads as a rule rather than an abbreviation: a caller
following the failures page refuses the agreeing pair.

Shipped behaviour, measured at the tip on `find-symbol build_overview` with
`--project code-indexer`:

| invocation | exit | stdout | stderr |
|---|---|---|---|
| `--json --format json` | 0 | the JSON payload (`"schema": 1, "symbols": [...]`) | empty |
| `--json --format text` | 2 | 0 bytes | `ArgumentError: --json and --format text disagree: --json is an alias of --format json; pass one of them` |
| `--json --format compact` | 2 | 0 bytes | `ArgumentError: --json and --format compact disagree: ...` |

So the five rule sites and the one alias-only site agree with `_output_format`;
the seventh does not. `query.md:131` ("two spellings of one symbol that
disagree are refused too") is about symbol spellings, not the flag pair, and
is correct as written.

## Bar 3 — the derived compact roster can fail in all three directions

Throwaway detached worktree at `8b6d639`, baseline before any edit:
`uv run --extra semantic pytest tests/test_output_format.py -q -k compact` ->
34 passed, 50 deselected. Each mutation was applied to the worktree only, run,
and undone; the worktree's `git status --porcelain` was empty afterwards and
its HEAD was still `8b6d639`. Captures: `mutation-a.log`, `mutation-b.log`,
`mutation-c.log`.

| mutation | what I changed | red test ids |
|---|---|---|
| a | added `"find-references"` to `COMPACT_FORMAT_COMMANDS` -- a name with no renderer | `test_compact_is_refused_where_there_is_no_compact_form[find-references]`, `test_the_documented_compact_roster_matches_the_frozenset` -- 2 failed, 32 passed |
| b | removed `"overview"` -- a name that has a renderer | `test_overview_compact_is_one_tagged_line_per_record`, `test_the_documented_compact_roster_matches_the_frozenset` -- 2 failed, 32 passed |
| c | renamed `overview` -> `overviews` in the documented compact row of `SKILL-CLI.md` | `test_the_documented_compact_roster_matches_the_frozenset` -- 1 failed, 33 passed |

Two observations the fold record already states and that I confirm by
measurement. First, in (a) the *offered* case for `find-references` stays
green (a member is accepted and renders text, exit 0), so the witness is the
refusal case, not the roster case -- declared roster cases pin the set, the
per-command shape tests pin the renderer. Second, in (b) membership gates the
refusal, so removing a name makes an existing renderer unreachable rather than
deleting it: the red is the shape test for that command. The tooling note:
the worktree's fresh environment does not carry the optional semantic extra,
so `semantic-search`'s offering case fails there on `ConfigError` until it is
run with `--extra semantic` -- an environment fact, not a suite defect, and
the baseline above is green with the extra.

## Bar 4 — nothing else moved

`git diff --name-only 5178220..8b6d639` returns exactly these 15 paths:
`CHANGELOG.md`; `skill/references/machine-output.md`,
`skill/references/query.md`, `skill/references/semantic.md`;
`tests/test_output_format.py`; and ten files under `runs/r3-folds/` (the
record and nine captures). `git diff --stat` is 15 files, +332/-10.

**No `src/` change at all** -- `git diff 5178220..8b6d639 -- src/` is empty,
so `_output_format`, `command_classes.py` and every renderer are byte-identical
to the base. No schema or extraction-version change, no reindex, no positional
change. A documentation round that moved behaviour would be out of scope; this
one did not.

## Bar 5 — standing bars at `8b6d639`

| command | observed | verdict |
|---|---|---|
| `uv run pytest tests/ -q -W error` | `1088 passed, 2 deselected in 120.44s`, exit 0 (`pytest-tip.log`) | PASS |
| `uvx ruff check src tests scripts` | `All checks passed!`, exit 0 (`ruff.log`) | PASS |
| `uv run python scripts/publishing_hygiene_lint.py` | `scanned=1278 skipped=0 violations=0`, exit 0 (`hygiene.log`) | PASS |
| `uv run python scripts/generate_skill_docs.py --check` | exit 0, no output (`generate-check.log`) | PASS |
| `pytest tests/test_docs.py::test_installed_copy_digest_matches_the_tracked_copy` | 1 passed (`digest.log`) | PASS |
| `git log --format='%h %G?' 5178220..8b6d639` | `8b6d639 G`, `b4998d4 G`, `16f0977 G` | PASS |
| `git status --porcelain --untracked-files=no` | empty (the pre-existing untracked captures are other seats' scratch and are not tracked changes) | PASS |
| `git ls-remote origin refs/heads/main` | `8b6d639a848d2f735d9bc91059f0ba3b5280fe87 refs/heads/main` | PASS |
| `gh run list --commit 8b6d639... --json ...` | run 42373039252, workflow `ci`, `status=completed`, `conclusion=success` | PASS |

The suite is run with `-W error`; I do not claim the tree is warn-clean, only
that the suite passes under that flag. The hygiene count is the fold record's
`1268` plus the ten `runs/r3-folds/` captures that became tracked after it ran;
the same command at the same tree semantics now reads 1278. With this record
and its own captures staged it reads `scanned=1294 skipped=0 violations=0`,
exit 0 -- and the first pass over this record found six violations on a single
line, its own probe pattern, which is why bar 6 states that probe in prose
instead of reproducing it.

## Bar 6 — the masking commit

`git show --stat 8b6d639` -> one file, `runs/r3-folds/mutation-unrenderer-member.log`,
3 insertions / 3 deletions, `%G? = G`. The diff replaces the absolute fixture
path in the `rig =` repr and in two expected-output lines with `<scratch>/...`;
nothing else in the commit changed. The current file carries no absolute path,
no home directory, no index root and no profile name: a sweep of that file
for absolute-path prefixes, for the household's internal profile names and for
the scratch-path segment returns nothing (the patterns themselves are not
reproduced here, which is why this paragraph is prose). Recorded, not acted
on: whether the published history should
carry that name at all is an owner question outside this bar, and no history
was rewritten.

## Carried open

1. **`skill/references/failures.md:47` still states the pair is refused.** The
   fix is one clause -- "`--json` together with a different `--format`", or
   "... together with `--format` naming another format" -- in the exit-2
   table's `ArgumentError` row. The owning leg lands it; a QA leg does not.
   Until then the failures page and the five rule sites contradict each other.
2. **The sweep method, not just the sentence.** The fold leg swept the alias
   phrase. A rule restated in refusal vocabulary ("together with", "passing
   both", "cannot be combined") is invisible to that grep; the sweep that
   would have caught this row is the second one in `rule-sites.txt`. Worth
   recording as the sweep to run for the next round that touches the rule.
3. **The `--json` quantifier residue** the review carried as info (nine
   commands accept `--json` outside the read class) is untouched by the fold
   and still open; nothing in this round made it worse.
4. **The fold record's hygiene figure is of its own moment** (1268 before its
   captures were tracked). Neither figure is wrong; they measure different
   trees. No action, recorded so the two numbers do not read as a drift.
