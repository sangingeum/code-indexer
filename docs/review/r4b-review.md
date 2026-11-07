# R4b review — the stale banner states its meaning again

Two-axis review of the fix round that closed the two failing gate rows on the
staleness banner. Base `7704364`, tip `0993311`, range
`7704364..0993311`, three commits: `abd6540` (the sentence on the stale
branch plus a test that pins it), `d0210fc` (the document sweep), `0993311`
(the round record and its captures).

Reviewed at tip `0993311`. Tracked tree clean at the start:
`git status --porcelain | grep -v '^??'` returned nothing; the untracked
entries were `runs/` evidence directories from earlier rounds.

Authored by the household reviewer. The two axes were run separately —
correctness and contract on one side, documentation truth on the other — and
aggregated here. The QA gate is of record: its rows were not re-run, they were
re-decided against the shipped code.

**Verdict: approve with notes.** No blocking finding. Every behavioural claim
the round makes was reproduced by execution against a worktree of the previous
tip, and both halves of the new test were proven red by mutation. The notes
below are documentation and test-design gaps that the next round touching this
surface should pick up; none of them makes a shipped line false.

---

## Axis 1 — correctness and contract

### 1. The stale line — **pass**

One line, stateless, count + meaning + repair, in that order.

- One physical line, no wrapping, no carriage return: `wc -l` on
  `tip-text.stderr` is 1, `tip-three.stderr` 1, `tip-double.stderr` 1,
  `tip-status.stderr` 1.
- Length at the tip: 156 bytes for a one-reason verdict, 191 for three kinds,
  194 for stale-plus-withheld (base was 105 for the same one-reason case — the
  sentence costs 51 bytes and the line still fits a single terminal row).
- Order matches ADR-0003 section 3: count phrase, then
  `answers describe the index, not the tree`, then the refresh hint. The ADR's
  own quoted example puts the sentence after the state and before `Refresh:`;
  the shipped line does the same with the punctuation the current line uses.
- The `unknown` branch is untouched and already consistent:
  `Warning: index staleness check did not run (no index manifest for this
  project); answers describe the index, not the tree.` — byte-identical
  between base and tip (`diff` of `base-unknown.stderr` and
  `tip-unknown.stderr` empty, exit 0), same sentence in the same slot.
- Stateless, by observation rather than by reading: two consecutive reads of
  the same stale project produced byte-identical stderr (`cmp` clean), and the
  same read under `env -i` with `TZ=UTC LANG=C` and with
  `TZ=UTC-07 TERM=dumb SHELL=/bin/sh` produced stderr byte-identical to the
  plain read (`cmp` clean on both). Nothing in the diff introduces state; the
  change is a literal concatenated into one f-string branch.

### 2. The test that pins it — **pass, proven red by mutation**

`tests/test_read_contract.py::test_the_stale_line_states_what_a_stale_answer_means`.
I did not take its docstring's word for it. Three worktrees of the same commit:

| worktree | mutation | result |
|---|---|---|
| unmodified tip | none | 7 passed in the `-k stale` selection, exit 0 |
| sentence removed | the literal deleted from the return | `FAILED ...::test_the_stale_line_states_what_a_stale_answer_means`, assertion at `tests/test_read_contract.py:340`, 1 failed / 6 passed, exit 1 |
| clauses swapped | repair before the sentence | same test `FAILED`, assertion at `tests/test_read_contract.py:344`, 1 failed / 6 passed, exit 1 |

So the assertion is not substring-luck: removal trips the existence assert and
reordering trips the `index()` ordering assert. It also requires exactly one
`Warning: index is stale` line on stderr and no embedded newline, and the
full suite confirms it is collected: 1003 passed at the tip against the 1002
the gate recorded at `7704364` — the delta is this test.

### 3. JSON untouched — **pass, byte-identical**

Compared by execution, not by docstring: the same fixtures, the same index,
base source tree (`7704364`) and tip source tree through the same interpreter
with `PYTHONPATH` pointed at each (`probe_import.py` confirmed the loaded
module path in each case).

| read | stdout diff | stderr diff |
|---|---|---|
| `overview --json` (stale) | 0 bytes | 0 bytes |
| `index-status --json` | 0 bytes | 0 bytes |
| `overview --json` (unknown) | 0 bytes | 0 bytes |
| `overview --json` (three kinds) | 0 bytes | 0 bytes |
| `overview --json` (stale + withheld) | 0 bytes | 0 bytes |
| `overview --json` (sensitive override) | 0 bytes | 0 bytes |
| `semantic-search --all-projects --json` | 0 bytes | 0 bytes |

The double-fault payload keeps its shape: `stale_reasons`
`[{kind: added, file: extra.py}]`, `stale_reason_count` 1,
`withheld_count` 1, `withheld_files` `[{file: notes.md, reason: sensitive
content}]`, `withheld_summary` `1 file skipped (sensitive content)`,
`withheld_unexamined_count` 0, `index_revision`, `last_indexed` — identical
bytes against the previous tip. No reason kind, count, revision or timestamp
moved.

### 4. No collateral — **pass**

Exit codes and the stderr/stdout split are unchanged everywhere I could reach:

- stale text read: exit 0 both sides, stdout byte-identical, stderr the one
  changed line; the diff is a single-line replacement, nothing else.
- stale three-kind read: exit 0 both sides, stdout identical, stderr differs
  only by the inserted sentence.
- stale-plus-withheld read: exit 0 both sides, stdout identical, stderr differs
  only by the inserted sentence; the withheld clause keeps its place and its
  wording (`1 added file; 1 file skipped (sensitive content)`).
- `index-status`: exit 0 both sides, stdout identical (the JSON-stripped text
  output carries the withheld fact as its own line, unchanged), stderr gains
  the sentence only; the warning still omits the withheld clause.
- `get-code-context` line mode (the second caller that prints the line
  itself): exit 0 both sides, stdout identical, stderr differs only by the
  sentence.
- the sensitive override (`add-project --allow-sensitive`, then a stale read):
  exit 0 both sides, no withheld clause on the line, sentence present; JSON
  byte-identical. The override path is untouched, as expected — the diff does
  not touch the classifier.
- unsupported combinations fail the same way on both sides: `overview
  --allow-sensitive` and `overview --all-projects` are usage errors with exit
  2 and identical stderr; `semantic-search --all-projects` on a vectors-off
  index is a `ConfigError` with exit 4 and identical stderr on both sides.

The only behavioural delta anywhere is the text of the stale line.

### Scope of the round

`git diff --name-only 7704364..0993311`, excluding `runs/`:
`CHANGELOG.md`, `README.md`, `SKILL-CLI.md`, `skill/references/machine-output.md`,
`src/code_indexer/staleness.py`, `tests/test_read_contract.py`. The diff carries
no `schema` and no `EXTRACTION_VERSION` hunk, and
`git diff --name-only 7704364..0993311 -- docs/adr/` returns zero files. All
three commits verify `%G? = G`.

---

## Axis 2 — does the documentation now tell the truth

### 5. Every tracked quote of the banner — **pass**

`git ls-files -z | xargs -0 grep -n "index is stale"` over the tracked tree
(excluding `runs/` evidence) finds four quoting surfaces and two prose
mentions:

- `README.md:210` — the shipped line verbatim, in a fenced block.
- `SKILL-CLI.md:94` — the shipped line verbatim, one example count.
- `skill/references/machine-output.md:52` — the shipped line verbatim.
- `CHANGELOG.md:10-11` — the shipped line, split across a source wrap that
  renders as one line.
- `CHANGELOG.md:6` quotes the *previous* paragraph, and its sentence begins
  "printed the same paragraph"; `CHANGELOG.md:58` likewise. Both are history,
  labelled as what the tool used to print, not claims about the tool.
- `docs/overhaul-plan.md:92` and `docs/overhaul-baseline-audit.md:746` mention
  staleness in prose and quote no banner.

The old paragraph survives in exactly one tracked document that the round was
allowed to touch: `docs/adr/0003-read-only-and-staleness-contract.md:56`. That
is the owner-carded call this round was told to leave alone, and it did — the
diff touches no ADR file. Confirmed expected, not a finding.

`skill/references/indexing.md` describes withheld reporting and the
`--allow-sensitive` override, quotes no banner, and needed no change; its
consumer rule ("`stale: false` with a non-zero withheld count is not 'nothing
was withheld'") is intact.

### 6. The withheld fields — **pass in README and the entry document, gap in the machine reference**

- `README.md:204-207` now names `withheld_count`, `withheld_files`,
  `withheld_summary` and `withheld_unexamined_count` beside the six verdict
  fields, and states in the same sentence block: "a withheld file is counted
  beside the verdict, never as a stale reason". A reader cannot come away
  thinking a withheld file makes the index stale.
- `SKILL-CLI.md:97-98`: "A file the sensitive layer withholds is not a
  staleness reason ... (JSON: `withheld_count`) on a fresh verdict."
- `skill/references/machine-output.md:65-69` says the same thing in words
  ("a skip, not a stale reason") but names no field, and its field table at
  `:38-45` still opens "Every read answer that comes from the index carries
  these fields in JSON" and lists six of the ten the block actually carries.
  See note 1.

### 7. The prose/source boundary — **pass, with the duplication noted**

One document describes the banner in prose (`skill/references/machine-output.md`);
`SKILL-CLI.md` and `README.md` now quote it and add only what the example
cannot show (fresh prints nothing, unknown prints one line; README keeps the
field list). The entry document lost its two-sentence prose description of the
line in this round, which is the right direction — the prose had already
drifted once. Residual drift risk is inside the rationale sentence, noted
below.

---

## Non-blocking notes

1. **`skill/references/machine-output.md` never names the withheld fields.**
   Its staleness field table (`:38-45`) claims to list the fields "every read
   answer that comes from the index" carries and lists six of ten; the four
   `withheld_*` fields are absent from the file entirely
   (`grep -n withheld skill/references/machine-output.md` → no match). The
   round edited the paragraph below that table and left the table. Pre-existing
   rather than introduced, and README carries the full list, so it is not
   blocking — but this is the reference an agent loads for JSON, and a
   consumer reading only it cannot discover the fields the tool emits on every
   read. Four table rows close it.
2. **The documentation drift test cannot catch the defect this round fixed.**
   `tests/test_docs.py:738-745` asserts two loose substrings over
   `SKILL-CLI.md` plus `skill/references/*.md`; `README.md` is not in that
   surface at all, and the assertion passes if a document quotes the count
   prefix while the sentence appears only in surrounding prose. That is
   precisely how the README quote stayed stale through the previous gate. A
   per-document assertion on the full one-line form (count, sentence, hint, in
   order) would have caught it mechanically.
3. **`src/code_indexer/staleness.py:293` is 122 characters** where the file
   wraps at 88 (`[tool.ruff] line-length = 88`; `E501` is not in the selected
   rule set, so ruff stays green). The docstring edit joined
   "...prints them on request. Nothing is remembered between invocations -- a"
   into one long line. Cosmetic, but it is the only line over the project's
   own width in that docstring.
4. **The new test pins guidance-before-repair, not count-before-guidance.**
   `line.index("answers describe the index") < line.index("reindex-project")`
   is exactly what the docstring claims, so the test is honest; but a line that
   put the sentence in front of the count would still pass. One further
   `index()` comparison against the count phrase would pin the ADR order
   completely.
5. **The rationale sentence now lives in two places.** "A count without it
   reads as a fact about the tree ... the one misreading the verdict exists to
   prevent" appears in the `warning_line` docstring and, near-verbatim, in
   `machine-output.md`. Shape in the reference, rationale in the docstring
   would keep the next tone edit from having to be made twice.

---

## Why nothing here blocks

The two gate rows were the whole bar: the stale line must carry the meaning
sentence beside the refresh hint, and the README must quote the shipped line
with the withheld fields named. Both are true at this tip and both were
verified by running the tool, not by reading the diff. The test that guards
row 1 is genuinely load-bearing — it goes red on removal and on reordering.
The JSON contract is byte-identical to the previous tip across seven read
shapes, and no exit code moved. The remaining inaccuracy in the tracked tree
is the ADR quote, which this round was explicitly forbidden to touch and did
not.

## Evidence

Untracked, under `runs/r4b-review/`: `pytest-tip.log` (full suite, 1003
passed, exit 0), `capture.sh` / `capture2.sh` / `capture3.sh` with
`captures/` (base-vs-tip text, JSON, exit codes, cross-shell reads), and
`captures/mutation-*.log` for the three worktree runs. Host paths in those
captures are redacted to `<checkout>` and `<scratch>`; the evidence directory
is not committed.

> Evidence note: `runs/` is kept in the working checkout and is not
> published. A `runs/...` path cited here names a local artifact, not a
> link a reader can open.
