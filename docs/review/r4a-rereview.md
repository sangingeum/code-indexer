# Re-review — the fold of the withheld-file round

The fold's base is `5e13ac0`, its tip `974f0ed` (equal to the published tip).
This leg did two jobs in order: repaired the previous review record's
publishing hygiene, which had made the tracked tree's lint red, and then ruled
on the eleven dispositions the fold applied. Evidence is in
`runs/r4a-rereview/` and was produced on this leg, not read from the fold's
captures.

## First, the record repair

The lint was red on six counts, all in the previous review record: two board
card ids in the header and four internal profile names — the header's
attribution, one quotation's attribution, and the sign-off. A committed
document is a published surface, so the names and ids had to go; the lint was
not weakened and no exemption was added.

Repaired as the author by rephrasing the provenance instead of deleting it:
the header names the round rather than the cards, the quotation is attributed
to the QA gate, the author is "the household reviewer", and the sign-off line
is dropped because the commit signature is the signature. The repair is
`8054b36`, signed and pushed before any review work, so every other leg saw a
green tree. `publishing_hygiene_lint`: `scanned=912 skipped=0 violations=0` at
that commit, and 0 again at this record's tip after the last capture.

## The bars

| Bar | Result |
|---|---|
| publishing hygiene at this tip | 0 violations |
| `pytest tests/ -q -W error` at the fold tip | 1 failed, 997 passed, 2 deselected |
| `uvx ruff check src tests scripts` | All checks passed |
| commits signed, pushed ref == local tip | yes (GPG verified) |

The single test failure is the installed-copy digest test in `test_docs.py` —
the installed-skill digest, which the dispatch predicted stays red until the
orchestrator installs the skill after the legs land. Nothing else is red. The difference from
the fold leg's own capture is instructive: it had a second failure,
`test_publishing_hygiene.py::test_tracked_tree_is_clean`, and that was this
reviewer's record. It is gone.

## What I ran

| Capture | What it shows |
|---|---|
| `override-and-budget-base.txt` | the fold base: override ignored, secret eaten by the budget |
| `override-and-budget-tip.txt` | the fold tip: override honoured, secret withheld |
| `fanout-collision-base.txt` | the fold base: `skipped_count 1` while the fan-out list has 2 |
| `fanout-collision-tip.txt` | the fold tip: both families of keys, fan-out count intact |
| `truncated-recheck.txt` | my own probe: the budget runs out and says so |
| `pytest-tip.txt` | the suite at the tip |

The base runs come from a throwaway worktree at `5e13ac0` (removed after
use); the tip runs from this checkout. The eleven items below are the fold
table's, in its order, each headed by its subject.

## 1. The fan-out key collision — PASS (test clause carried as F1)

At the tip, one `semantic-search --json` document carrying two failed
projects and one withheld file has both families: `skipped` /`skipped_count`
= 2 for the projects that could not answer, and `withheld_count` = 1 with
`withheld_files` and `withheld_summary` for the file the sensitive layer
dropped. The fan-out count equals `len(skipped)`.

At the base the collision is not hypothetical: the same probe yields
`skipped_count: 1` while `skipped` holds 2 projects — the staleness block's
`skipped_count` overwrote the fan-out's — and the withheld fact has no key at
all. `payload.update(staleness_block)` is the mechanism, so the rename is the
fix and not a cosmetic one.

The new names are documented in `docs/json-schema.md:11-21` (the two facts
never share a key), `:185-217` (block shape and semantics),
`skill/references/indexing.md:203-206` and `:319-323`, `skill/SKILL.md:103-104`,
and `CHANGELOG.md:14-16` and `:26`. The tests name `withheld_count` and
`withheld_summary`; `withheld_files` is the one new key no test names — see
F1.

## 2. The override decides the diff as the pass — PASS

With `allow_sensitive` set, the rowless secret is an `added` reason and nothing
is withheld; unset, the same path is the withheld state on a `fresh` verdict.
The first half is the fix: at the base both cases report `fresh` beside a
skip, which is the false fresh the previous record proved.

The fold's test fails at the base — I copied the tip's test module into the
base worktree and ran it: 9 failed, 1 passed (`AttributeError` on the new
fields, `KeyError` on the new keys, and one `assert 'fresh' == 'stale'`) — and
19 pass at the tip. That is a test which pins a change, not a description of
one. `core.py:544-548` reads the override from the same manifest open as the
scope, and `allow_sensitive` is bound before every return that uses it.

## 3. The budget is charged in bytes read — PASS

The 12 MiB probe reproduces the defect at the base and its absence at the tip:
charged by size, one big added file spends the whole 8 MiB allowance and the
secret sorted behind it comes back `added`; charged by the prefix actually
read, the big file costs 256 KiB and the secret is withheld.

I also exhausted the budget on purpose (33 candidates at the prefix size):
the two paths past the budget stay `added` reasons and
`withheld_unexamined_count` reads 2. The disclosure is live, not a field that
can only be zero.

## 4. The numeral only with more than one category — PASS

`staleness.py:215-234` tallies the two categories and emits the numeral only
when both are present. All three shapes are captured in the suite, each from a
real pass and diff: `1 file skipped (sensitive content)` (singular, one
category), `2 files skipped (sensitive content)` (plural, one category),
`2 files skipped (1 sensitive content, 1 sensitive filename)` (both).

## 5. One count on the status line — PASS

`cli.py:1178-1181` keeps the last pass's number in JSON only, as
`sensitive_skipped`; `:1245-1253` prints one phrase — this tree's count — with
the override hint inside the same phrase. `tests/test_sensitive_exclusion.py:275-283`
asserts the old `sensitive=` shape is gone, the new phrase is present, and the
hint is present, so the single-count rule is pinned rather than described.

## 6. Truncation disclosed with its own count — PASS

`withheld_unexamined_count` is emitted by `block()` and summed by
`aggregate()` (`staleness.py:260`, `:322`). The reference sentence is in
`skill/references/indexing.md:331-337` — an `added` reason says the path is not
in the index and nothing about its content — and the block's note is in
`docs/json-schema.md:208-213`. My probe produces the non-zero case; the suite
never does (F1).

## 7. One prefix constant, one composition — PASS

`SENSITIVE_READ_CHARS` is defined once (`sensitive.py:50`) and imported by both
sides: the pass at `indexer.py:171-178`, the diff at `staleness.py:202-207`.
`skip_verdict` is the only composition, with exactly those three production
call sites (two in the diff: the path-only layer, then path and text). No
`file_is_sensitive` remains in `src/`, and no second 256 KiB literal exists —
the two remaining `65536` literals are unrelated block reads in `scanner.py`
and `core.py`.

## 8. The reason mapping is an explicit lookup — PASS

`SKIP_REASON_BY_LAYER` (`sensitive.py:113-116`) and `skip_reason_text` indexes
it, so a layer the module does not know raises instead of being reported under
another layer's phrase. `tests/test_sensitive_exclusion.py:154-160` covers both
layers, the clean case, and the `text=None` case.

## 9. The consumer rule is written down — PASS

Both surfaces carry it in the same words: `skill/references/indexing.md:209-214`
and `docs/json-schema.md:215-217` — `stale: false` with a non-zero withheld
count is not "nothing was withheld", and a consumer branches on the count, not
on `stale`.

## 10. The staleness docstring — PASS

`core.py:508-520` now says a scanned path with no manifest row is opened for
the same bounded prefix the pass reads, so a withheld file is reported as
withheld rather than as an added reason, and that the project's override
decides whether anything is withheld at all. That is the behaviour the method
has.

## The one finding (non-blocking)

**F1 — the two new contract fields have no test naming them.**
`tests/test_sensitive_skip_staleness.py:167-176` and `:225-236`.

`withheld_files` is the one new key no test names: the JSON test asserts the
count and the summary, and its string match on the path would pass through
`withheld_summary` alone. And every use of `unexamined_count` in the suite
asserts `== 0`, so no test produces a truncated re-check — the budget branch
could be deleted, and the suite would stay green while the reference went on
promising disclosure.

Exact change, in that file: (a) in the JSON test assert
`block["withheld_files"] == [{"file": "secretive.py",
"reason": SKIP_REASON_CONTENT}]`; (b) one test that adds 33 or more rowless
candidates of at least the prefix size and asserts `unexamined_count > 0` with
those paths still present as `added` reasons — `runs/r4a-rereview/` is the
worked shape.

Disposition: should-fix at the next touch of this file; not a fold blocker.
I ran both behaviours and both are correct, so this is a missing regression
guard rather than a defect, and a whole fold cycle for two assertions is
disproportionate.

## Observations, no disposition

- `runs/r4a-staleness.md` still documents the pre-fold tip: its "what the
  reader sees" block shows `skipped_count` / `skipped_files` and names
  `file_is_sensitive`. It is honest as a snapshot — it states the
  implementation tip it describes — and the fold's own record supersedes it,
  but a reader who follows it into the shipped tree will not find those keys.
- The budget is charged `len(text.encode("utf-8", "replace"))`, not the literal
  byte count the read consumed. For invalid UTF-8 a replacement character
  encodes to three bytes, so the charge can exceed the bytes read. The
  direction is the safe one — fewer files examined, never a false withheld —
  so "bytes actually read" is approximate in that case only.

## What I did not check

The gate's ten bars are of record and were not re-run here. I did not audit
`runs/` prose beyond the claims it makes, and I did not review the pre-existing
detector's own coverage (patterns, prefix limit) — both are upstream of this
round. The base worktree was used only to run the fold's probe and test module
and was removed afterwards.

## Verdict

**Approve.** All eleven dispositions hold on my own execution rather than on
the fold leg's captures: the collision is real at the base and gone at the tip,
the override and the budget both change behaviour between the two commits, and
the four documentation promises the previous record found missing — the
renamed keys, the truncation count, the consumer rule and the docstring — are
present and true. One non-blocking should-fix (F1) is carried for the two
contract fields the suite never names; it is a regression guard, not a defect.

Nothing in this record asks for another fold. The published chain is
`974f0ed` (fold tip) plus this leg's commits: the record repair, this
re-review, and the one-line correction that followed this record becoming
tracked (see the commit that names the release step). All are signed.

> Evidence note: `runs/` is kept in the working checkout and is not
> published. A `runs/...` path cited here names a local artifact, not a
> link a reader can open.
