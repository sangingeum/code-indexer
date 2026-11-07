# R4b notes fold -- the four non-blocking review notes

Base `2822102` (the two-axis review's tip, verdict approve with notes). This
round folds notes 1-4 from that review; it is a documentation and test round
plus one docstring reflow, and nothing else. Every note names the file and
line it changed.

## The four notes

1. **The withheld fields are named in the machine reference.**
   `skill/references/machine-output.md:46-49`: four rows added under
   `stale_reason_count` -- `withheld_count`, `withheld_files`,
   `withheld_summary`, `withheld_unexamined_count` -- with the meanings the
   schema reference already states (capped like the reasons, present on a
   `fresh` verdict). The prose below the table still says a withheld file is
   a skip, not a stale reason.
2. **The drift test matches the whole banner per document.**
   `tests/test_docs.py:755-784`: a new
   `test_every_document_quoting_the_stale_banner_quotes_the_whole_line`, with
   `_STALE_BANNER` anchored at both ends (count phrase, meaning sentence,
   repair hint, in that order). `README.md` is in its surface; README and the
   entry document must quote a line, and any reference that quotes one must
   quote the whole form. The loose substring check that let the README quote
   go stale stays in place beside it, unchanged.
3. **The long docstring line is reflowed.**
   `src/code_indexer/staleness.py:293-297`: the 122-character line in
   `warning_line`'s docstring is wrapped at 88; the longest line in the file
   is now 79. No executable line moved.
4. **The test pins count before guidance.**
   `tests/test_read_contract.py:344-345`: one further `index()` comparison
   against the count phrase, so the stale line is pinned count -> guidance ->
   repair rather than guidance -> repair alone.

## Mutation proof for note 2

Throwaway worktree at `2822102` with only the new `tests/test_docs.py` copied
in, run with the repo's interpreter.

| run | README quote | new test | loose check | log |
|---|---|---|---|---|
| base, unmutated | shipped line | 1 passed | passed | `mutation-base-run.log` |
| mutation A | the pre-fix paragraph form | FAILED -- "stale line is not the shipped one-line form" | passed | `mutation-old-quote.log` |
| mutation B | shipped line with the sentence dropped | FAILED -- same assertion | passed | `mutation-sentence-dropped.log` |

The loose check passing under both mutations is the defect: it matched the
count in one place and the sentence elsewhere. The worktree was removed after
the runs.

## No behavioural change

`runs/r4b-notes/bytecode-compare.txt` compiles the base and tip copies of
`src/code_indexer/staleness.py` and compares every code object: 21 in each,
none differing once the leading docstring is excluded, `warning_line`'s
bytecode and non-docstring constants identical (`bytecode_compare.py`). The
only `src/` hunk in the round is that docstring wrap.

## Verification

| check | result | log |
|---|---|---|
| full suite `pytest tests/ -q -W error` | 1004 passed, 2 deselected, exit 0 (base 1003 + the new drift test) | `pytest-tip.log` |
| `ruff check src tests scripts` | all checks passed | `ruff.txt` |
| publishing hygiene, before the evidence commit | 1122 scanned, 0 violations | `hygiene.txt` |

The seven read shapes and the exit codes are untouched: no executable line
changed, and the full suite (which covers the staleness JSON block across
verdicts, the withheld cases and the read/write split) is green.
