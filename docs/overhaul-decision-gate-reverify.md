# Narrow re-verification of the folded decision set

- Status: verification record -- decides nothing, edits nothing
- Date: 2026-11-07
- Verifies: only what the fold touched, between tip `8d841ac` (the previous gate) and
  tip `413fb76` (four commits: `6e4129c`, `af09d53`, `1bbcb20`, `413fb76`)
- Range: `8d841ac..413fb76`, eleven paths, all under `docs/`
- Does not repeat and does not overwrite `docs/overhaul-decision-gate.md`

## How this was checked

Every verdict below rests on a command run at `413fb76`, and each item prints the
command that reproduces it, so no reader has to trust a capture. The captures are
untracked run evidence and are deliberately not cited: a path outside the published
tree is not a source.

Two constraints held. Nothing that mutates the live index was run: the manifest was
read through a `mode=ro` SQLite URI, and the only CLI invocations were `--help`
surfaces. No record, plan or earlier gate record was edited; the findings are here.

## Verdicts at a glance

| Item | Verdict |
|---|---|
| 1. `plan-apply` and the closed roster | **CLOSED** |
| 2. The amendment mechanism | **CLOSED** |
| 3. The position-boundary test | **CLOSED** |
| 4. `rename-symbol` | **CLOSED** |
| 5. Missing server, exit 3 | **CLOSED** |
| 6. The SKILL split and its budgets | **STILL OPEN**, two findings |
| 7. The `--json` promises | **CLOSED** |
| 8. The description correction's round | **CLOSED** |
| 9. `--vectors` | **CLOSED** |
| 10. `index-status` parse-failure counting | **CLOSED** |
| 11. The register's round-1 exclusion | **CLOSED** |
| 12. ADR-0002's locator wording | **CLOSED** |
| 13. ADR-0006's dotnet sentence | **CLOSED** |
| 14. Citation followability | **CLOSED**, three for three |
| 15. The register's chunk-table label | **CLOSED** |
| 16. The four extra folds | **CLOSED**, all four present |
| 17. Does the fold break consistency? | **STILL OPEN**, one false attribution |
| 18. House bars at the post-fold tip | **CLOSED** |

Sixteen closed, two open, and both open items are sentence-level repairs in
ADR-0009 and in one plan clause. No closed bar is re-argued below.

## 1. `plan-apply`'s class, and the closed roster: CLOSED

ADR-0003 section 1's write bullet now names the plan writers, `plan-apply` and its
receipt, and `plan-apply` appears in no other list. The count paragraph is explicit:

> The roster is closed and complete for the whole set: the twenty-seven commands that
> exist and the six plan commands together account for every command the plan
> schedules, and each is in exactly one of the two lists.

ADR-0004 no longer restates the class. Its consequence now reads: "`plan-apply` is a
write command, and the list that says so is ADR-0003 section 1's ... this record does
not restate it, because a second statement beside a closed list is what produced the
contradiction this fold closes."

Checked by enumerating both sides rather than by counting words:

```
# registry, walked off the click group the CLI builds its commands from
uv run python runs/.../dump_cmds.py        # count: 27
# write bullet parsed from ADR-0003 section 1, filtered to known names
uv run python runs/.../roster_check.py
```

Output:

```
registry commands: 27
plan commands: 6
write names parsed from ADR-0003: 11
  (add-project, index-more, plan-apply, plan-delete-declaration, plan-insert-symbol,
   plan-replace-body, reindex-project, remove-project, sweep-orphans, unwatch, watch)
write names not in registry or plan: []
reads today: 20
reads plan: 2 ['plan-show', 'plan-validate']
total classified: 33
overlap: []
union covers registry plus plan: True
```

Eleven writes plus twenty-two reads is thirty-three, the union of today's twenty-seven
commands and the six plan commands; no name is in both lists and no classified name
falls outside the union. The plan schedules exactly those six (`the six plan commands
(three writers, validate, show, apply)`, round 4), and ADR-0003's sentence about the
four lifecycle stages being stages rather than commands closes the one arithmetic that
could have read as a seventh. A command the plan names only as an absence,
`rename-symbol`, is classified nowhere, which is the correct handling: the plan's own
acceptance asserts it does not exist.

## 2. The amendment mechanism: CLOSED

`docs/adr/README.md` now distinguishes the two mechanisms and a third narrow case:

> a decision changes only by a superseding record, which names what it supersedes. Two
> narrow exceptions. **Before a record is accepted**, a fold applied in place is not a
> supersession: a proposal whose own gate found it contradicting a sibling record is
> corrected where it is written, its status stays `proposed`, and the gate record is the
> provenance for the fold ... After acceptance, a correction that changes what a record
> decides is a superseding record.

The fold is of the first kind, and the records say so consistently:

```
rg -n '^- Status' docs/adr/0*.md          # nine records, all "proposed (awaiting owner ratification)"
rg -n '^- Supersedes' docs/adr/0*.md      # nine records, all "--"
```

ADR-0004 delegates the class to ADR-0003 instead of claiming to amend it, which is the
behaviour the rule prescribes for a pre-ratification fold.

One wording observation, not a verdict: no record names the gate record. ADR-0004 says
"the contradiction this fold closes" and stops, so a reader who follows the README's
"the gate record is the provenance" finds the pointer only in the README itself.

## 3. The position-boundary test: CLOSED

Both sides name one test, one set of inputs, and one answer per phase.

> ADR-0004 section 3 rule 8: "The boundary test is not this record's to state: there is
> exactly one, it belongs to the adapter, and ADR-0006 section 5 states it -- a converted
> position must land on a byte boundary of the decoded line, and a position in the middle
> of a line is not a conversion failure at all. Phase 1 never receives one, because its
> operations are whole-line and its ranges are line-granular; phase 2 accepts it and
> expands the edit to the whole lines it touches (ADR-0006 section 6)."

> ADR-0006 section 5: "**The one boundary test, applied by the adapter to every
> converted position**: it must land on a byte boundary of the decoded line. A UTF-16
> offset that falls inside a surrogate pair, or anywhere the line's decoding cannot place
> exactly, is refused (`PositionConversionFailed`) ... **A mid-line position is not a
> failure of that test, and each phase says what it does with one** ... Phase 1 has no
> use for a mid-line position: every structural operation is whole-line, its ranges are
> line-granular (ADR-0004 section 3) and validation refuses a range that is not whole
> lines, so one never reaches a phase-1 operation. Phase 2 takes it as the normal case,
> because a rename edits identifiers inside a line, and expands the edit to the whole
> lines it touches (section 6)."

The test is a byte boundary of the decoded line; the inputs are every converted position,
including one inside a surrogate pair, which is refused; a mid-line position is a phase-1
absence and a phase-2 normal case expanded to whole lines. The earlier pair of rules (a
line boundary in ADR-0004, a byte boundary in ADR-0006) is gone from both texts.

One sentence is now too strong on the other side: ADR-0006 says its clause "is the only
place either answer is stated", and ADR-0004 rule 8 states both answers. The substance
agrees; the single-source claim does not.

## 4. `rename-symbol`: CLOSED

ADR-0004 section 1 still reserves it: "`rename_symbol` is reserved and refused in phase
1", and ADR-0006 section 6 still says enabling it "is a **schema bump to version 2** ...
This record fixes the normalization rule so that record can be written; it does not
enable the operation." The plan no longer promises it as work:

```
code-indexer --help | rg -c rename-symbol || echo 0  # 0: the command does not exist in phase 1
uv run pytest tests/test_plan_format.py -q           # a plan declaring rename_symbol is refused
                                                     # (exit 2, UnsupportedOperation, no text edit)
```

The round-5 goal says "including the rename operation it will carry once the plan format
enables it", which is a future tense governed by ADR-0004, not a deliverable. The exit-2
refusal is ADR-0003 section 5's usage class, which is what a plan naming an operation the
tool refuses is.

## 5. Missing server, exit 3: CLOSED

ADR-0003 section 5's row 3 now reads "backend or extension unavailable: service
unreachable, extension not installed, no language server installed for the requested
operation", and its paragraph draws the distinction ADR-0006 section 3 draws:
"**No provider installed is a different situation with a different number**: exit 3,
`ErrorType: ProviderUnavailable`" against "a request for an operation the selected
provider does not support is refused with `ErrorType: UnsupportedOperation` and exit 2".
Round 5's acceptance encodes both: "an absent provider is exit 3, an operation it does
not support is exit 2 (ADR-0006 s.3)".

## 6. The SKILL split: STILL OPEN

The grant is real and the citation is right. ADR-0009 exists, section 1 gives the entry
document a **120-line and 8 KiB** ceiling, section 2 gives each reference **400 lines
and 24 KiB**, section 3 states the single-source rule ("a flag documented in a reference
is not restated in the entry document; the exit code table exists once, in the entry
document"), and the plan's round 5 records line cites it: "ADR-0009 (the skill layout,
its budgets, the owner of the installed copy and the drift test)". The record's
measurable premises check out too:

```
rg -c '^def test_' tests/test_docs.py       # 13  ("carries thirteen tests today")
wc -lc SKILL-CLI.md                        # 613 39797  (its quoted figure, exactly)
code-indexer watch --help | wc -lc         # 84 7376    (against AGENT.md: 73 4283)
```

Two findings keep the item open.

**The reference table leaves seven registered commands with no home.** Section 1 sends
"per-command flag lists" and "per-command examples" out of the entry document, and
section 2 lists what the five references carry. Comparing that table against the
registry's twenty-seven names leaves these seven covered by no reference file:
`semantic-search`, `list-projects`, `lookup-project`, `index-status`, `doctor`, `eval`,
`eval-compare`. `query.md`'s contents list does not reach `semantic-search`, and
`indexing.md`'s stops at watch and unwatch. No check in section 5 catches it: check 1
tests the roster's names and classes, check 2 tests reference-to-file closure, and
neither asks whether every command's flags have somewhere to live. As printed, the
split deletes the documentation of the tool's flagship query command and its drift test
would pass.

**The digest check names no directory.** Section 5 check 6 pins the installed copy's
digest "when the installed skill directory exists on the machine", but the record never
says where that directory is, and the tree has no `skill/` path at all
(`ls skill` -> no such file; the tracked entry document is `SKILL-CLI.md` at the
repository root, and `skill/references/` is yet to be created). A test cannot pin the
digest of a directory the record does not name, and a clean checkout versus an installed
one is exactly the distinction the check turns on.

A related risk, recorded rather than counted: the existing module finds the documented
commands by scanning a fenced block in `SKILL-CLI.md` for lines beginning
`code-indexer `, so the first three of its thirteen tests key on that block. ADR-0009
says the six checks extend the module but does not say what happens to that parser when
flags leave the entry document. A roster rendered as a table outside a fence would make
`test_every_registered_command_is_documented` fail at the round-5 gate for a reason the
record does not anticipate.

## 7. The `--json` promises: CLOSED

ADR-0003 section 7 now decides the gap instead of declining it: "Thirteen registered
commands answer in text only (audit section 8). This record decides the gap instead of
leaving it to a round", granting `--json` to `list-projects`, `lookup-project`,
`index-status`, `find-definition`, `find-references` and the four pass commands, and
leaving `watch`, `unwatch`, `eval`, `eval-compare` text-only. The plan spends exactly
that grant: round 1 adds it to the five read commands, round 5 to the four pass commands
("granted by ADR-0003 section 7"), and the "Not doing" list repeats the four text-only
ones. ADR-0003's "What this record does not decide" no longer contains the thirteen-
command sentence; its five bullets concern the MCP server, the plan store, the apply
path, the watcher and the shapes' members.

## 8. The description correction's round: CLOSED

Three places, one round:

- ADR-0008 section 2: "`pyproject.toml`'s description is corrected in the round that
  makes it false -- round 1, which lands ADR-0001's dependency split".
- Plan round 1 deliverable: "the package description corrected, because this round's
  dependency split is what makes it false (ADR-0008 section 2)".
- Plan round 5 records line: "ADR-0008 (no rename -- the description correction it
  carries is round 1's, not this round's)".

## 9. `--vectors`: CLOSED

Round 1's deliverable names it only to exclude it: "`--vectors` belongs to round 2
(ADR-0001 section 4) and is documented with the pass commands there". It is introduced
where it belongs, in round 2 leg B: "`--vectors auto|on|off`, a per-side fingerprint
comparison". No round-1 help-text promise for the flag survives anywhere in the plan.

## 10. `index-status` parse-failure counting: CLOSED

Granted, in ADR-0004 section 7: "**In the index as well.** The same three verdicts are
what `index-status` counts per project -- files `parsed`, files `parse_error`, files
`parse_unavailable` -- so the verdict is reachable from the CLI and not only from a plan
report or a test." The plan's defect section now attributes that scope: "plus
`index-status` counting parse failures (ADR-0004 section 7 gives `index-status` that
scope)".

## 11. The register's round-1 exclusion: CLOSED

The register's exclusion sentence names the write instead of denying it: "it does write
one file this register does not track, the MCP usage record ADR-0007 section 3 appends
under the index root on every invocation, so round 1 touches the index root without
changing a row". The pass count is untouched, as it should be: the row is a line per
invocation, not an index pass.

## 12. ADR-0002's locator wording: CLOSED

> "Requirement 2's locator is `(path, language, qualified name, kind, decl_hash)` plus
> file-level preconditions -- the key above plus the file hash and nothing else ... The
> declaration's observed range travels with a plan as evidence about which bytes an edit
> touches; it is not a member of the identity."

ADR-0004 section 2 matches it: "A locator is ADR-0002's key plus the file hash -- five
members and the hash, and nothing else about the node is identity", followed by "The
range travels with the locator as evidence ... It is not a sixth member of the identity".
The word "exactly" is gone from ADR-0004, and the range now reads as evidence on both
sides.

## 13. ADR-0006's dotnet sentence: CLOSED

ADR-0006 context item 4 now says "`dotnet` 8.0.133 is present with one global tool
installed, and it is not a language server (`dotnet tool list -g` reports
`dotnet-script 2.0.1`), so a Roslyn server is still one `dotnet tool install`". Run:

```
dotnet --version        # 8.0.133
dotnet tool list -g     # dotnet-script  2.0.1  dotnet-script
```

The premise and the conclusion both hold.

## 14. Citation followability: CLOSED, three records, three numbers each reproduced

Three records picked at random, one number family from each, reproduced from the
citation as printed:

- **ADR-0008 context 1** ("Measured over the tracked tree at `8d841ac`"): `320` tracked
  files, `206` matching files, `1,478` occurrences, and excluding `runs/` and `eval/`,
  `93` files and `619` occurrences.
  ```
  git ls-tree -r --name-only 8d841ac | wc -l                                  # 320
  git grep -ilE 'code[-_ ]?indexer' 8d841ac | wc -l                           # 206
  git grep -oiE 'code[-_ ]?indexer' 8d841ac | wc -l                           # 1478
  git grep -ilE 'code[-_ ]?indexer' 8d841ac -- . ':(exclude)runs/**' ':(exclude)eval/**' | wc -l   # 93
  git grep -oiE 'code[-_ ]?indexer' 8d841ac -- . ':(exclude)runs/**' ':(exclude)eval/**' | wc -l   # 619
  ```
- **ADR-0002 context 2 and 4** (the inline SQL against `<index-root>/code-indexer/manifest.db`,
  read here through a `mode=ro` URI): `1,733` symbol rows over `1,104` distinct names,
  `141` names with more than one row covering `770` rows, `83` `(file, name)` groups;
  and of `113,751` reference rows, `101,197` `identifier/heuristic` rows carry none,
  `call/ast` is `8,698` with `7,575` attributed, `type/ast` `1,600` with `1,584`,
  `base_class` `15` of `15`.
  ```
  SELECT COUNT(*) FROM symbols;                                                  -- 1733
  SELECT COUNT(DISTINCT name) FROM symbols;                                      -- 1104
  SELECT COUNT(*) FROM (SELECT name FROM symbols GROUP BY name HAVING COUNT(*) > 1);   -- 141
  SELECT COALESCE(SUM(n),0) FROM (SELECT COUNT(*) AS n FROM symbols GROUP BY name HAVING COUNT(*) > 1);  -- 770
  SELECT COUNT(*) FROM (SELECT file, name FROM symbols GROUP BY file, name HAVING COUNT(*) > 1);          -- 83
  SELECT relationship, confidence, COUNT(*), SUM(from_symbol IS NOT NULL) FROM refs GROUP BY 1, 2 ORDER BY 3 DESC;
  -- identifier heuristic 101197 0 | call ast 8698 7575 | type ast 1600 1584 |
  -- call heuristic 1331 0 | includes ast 902 133 | base_class ast 15 15 | includes heuristic 8 0
  ```
- **ADR-0009 context 1 and 2**: `SKILL-CLI.md` at `613` lines and `39,797` bytes;
  `code-indexer watch --help` at `84` lines and `7,376` bytes against `AGENT.md` at `73`
  lines and `4,283` bytes.
  ```
  wc -lc SKILL-CLI.md                 # 613 39797
  code-indexer watch --help | wc -lc  # 84 7376
  wc -lc AGENT.md                     # 73 4283
  ```

Three for three. The convention change that made this possible is in `docs/adr/README.md`:
"No record cites an artifact path: a source a reader cannot open is not a source".

## 15. The register's chunk-table label: CLOSED

The row now says what the machinery does and keeps the count at one:

> a **migration plus one full pass that re-chunks every file** -- a reparse of the chunk
> rows, not a second read of the file: the pass reads each file once and chunks it from
> that same read, so it writes the chunk rows the old extractor never stored without
> adding a traversal

with the landing row still reading "**one** schema revision, **one** reparse". That is
ADR-0001's own account ("the pass ... reprocesses each file once", "the cost is one full
parse per project"), so label and count agree with the record they describe.

## 16. The four extra folds: CLOSED, all four present

- **The README section list.** It now names seven: "the one-paragraph decision first,
  then context, decision, the requirements the record resolves, alternatives,
  consequences, and what the record does not decide". All nine records carry the
  requirements section exactly once (`rg -c '^## Requirements this record resolves'
  docs/adr/0*.md` -> 1 each).
- **The duplicate ADR-0004 consequence.** The fifth bullet is gone; the phrase "preview
  is the only place intent is checked" now occurs once in the record, as the tail of the
  third bullet (`rg -c` -> 1, and the fold diff shows the bullet deleted).
- **The `parse_unavailable` vocabulary.** ADR-0004 section 7, ADR-0005 section 7 and the
  plan all use the three-verdict vocabulary, and the older `no_parser` token is gone from
  the whole tree (`rg -n no_parser docs SKILL-CLI.md tests` -> nothing).
- **ADR-0007 naming the decider.** Section 4 ends: "The decider is the owner: this record
  supplies the numbers the decision reads, and no second review cycle is needed to make
  it."

## 17. Does the fold itself break consistency? STILL OPEN

Three of the four readings hold. ADR-0009's roster is "the twenty-seven commands that
exist plus the six plan commands", which is ADR-0003 section 1's count and ADR-0004
section 6's six; its `plans.md` reference covers "the six plan commands"; the plan's
round 5 cites ADR-0009 for exactly what ADR-0009 decides (layout, budgets, owner of the
installed copy, drift test), and the budgets it repeats (120 lines and 8 KiB, five
references) match. Nothing in the new record contradicts ADR-0004's treatment of the
plan commands.

**The regression is one sentence, and it is in ADR-0009.**

> "**Disagreement with ADR-0007 section 5's placement of the parity table**: the table
> leaves both the skill and the README's prose for a single generated list in the README"

ADR-0007 section 5 already places the table in the README, not in the skill: "`README.md`'s
MCP section, the parity table and the client configuration block are rewritten in the
same commit", and its consequences say "The parity table in `README.md` becomes a short
list, and the 'no MCP tool' notes in the skill can be deleted rather than maintained".
What ADR-0007 leaves in the skill is the "no MCP tool" notes, and it already says they
can go. A reader sent by ADR-0009 to ADR-0007 section 5 for a two-home placement of the
table will not find one, so the new record attributes a position to the old one that the
old one does not hold. The decision both records reach (one list, in the README) is
identical; only the claimed disagreement is false.

A second, smaller drift in the other direction: round 5's deliverable says "the command
roster generated from the registry", while ADR-0009 section 3 lists as generated only
"each reference's per-command sections", and section 1 gives the roster "one line on
when to reach for it", which is prose. The plan claims a generation the record does not
grant.

## 18. House bars at the post-fold tip: CLOSED

```
git rev-parse HEAD                       # 413fb76651a0604189db93e847b91aa9555858fd
git ls-remote origin main                # the same object id
uv run pytest tests/ -q -W error         # 564 passed, 2 deselected in 81.08s, exit 0
uvx ruff check src tests scripts         # All checks passed!, exit 0
uv run python scripts/publishing_hygiene_lint.py  # scanned=321 skipped=0 violations=0, exit 0
git log --format='%h %G? %s' 8d841ac..413fb76    # four commits, all %G? = G
git diff --name-only 8d841ac..413fb76 | rg -v '^docs/'   # nothing
```

The tip equals the published `main`, every commit in the range is signed, and the diff
touches eleven paths, all under `docs/` (`docs/adr/0001` to `docs/adr/0009`,
`docs/adr/README.md`, `docs/overhaul-plan.md`); no source, test, dependency or changelog
path moved. The hygiene count rose from 319 to 321 with the two new documents this
range adds, and every one of them is clean.

## Two observations outside the eighteen items

Neither changes a verdict; both are pre-existing text the fold did not touch, recorded
so they are not lost.

- **ADR-0004's consequences say "Four commands appear that must carry `--json` from the
  first commit", while section 6 names six plan commands and says "Every command has
  `--json`".** The sentence is present at `8d841ac` unchanged, so it is not a fold
  defect; it is still a count a reader can check and find wrong.
- **ADR-0006's "this clause is the only place either answer is stated"** is contradicted
  by ADR-0004 section 3 rule 8, which the same fold rewrote to state both answers. One of
  the two sentences has to yield.

## What this record does not decide

Nothing about the records or the plan: it does not edit them, does not choose a fix, and
does not ratify. Its verdicts concern only the eighteen items the fold was asked to
close, at the tip named at the top.

> Evidence note: `runs/` is kept in the working checkout and is not
> published. A `runs/...` path cited here names a local artifact, not a
> link a reader can open.
