# Decision record gate

- Status: verification record -- decides nothing, edits nothing
- Date: 2026-11-07
- Verifies: the decision record set (`docs/adr/README.md`, records 0001 to 0008) and
  `docs/overhaul-plan.md` at tip `ab7d9de`, against `docs/overhaul-baseline-audit.md`,
  its gate record, and the five owner requirements forwarded on 2026-11-06
- Range: `fec2d58..ab7d9de`
- Owner action this record informs: ratification of the set (plan, "What the owner is asked to decide", item 1)

## How this was checked

Every verdict below rests on a command run at the tip named above, not on reading the
records and agreeing with them. Each bar prints the command it used, so the claim can be
reproduced without the capture files. The captures themselves live under
`runs/milestone0-gate/decision-gate/` and are deliberately untracked, as the two earlier
milestone-zero legs left theirs; a later reader reproduces rather than trusts them, which is
also the finding raised against the records' own artifact citations in bar 3.

Nothing that mutates the live index was run. No record, plan or audit file was edited. Where
the set contradicts itself, this record quotes both sides and names the fixer's decision; it
does not take the decision.

## Verdicts at a glance

| Bar | Verdict |
|---|---|
| 1. Docs only | **PASS** |
| 2. House bars hold at the tip | **PASS** |
| 3. Format conformance | **PASS**, with two editorial findings |
| 4. Every owner requirement resolved | **PASS** |
| 5. The audit's ten open questions settled | **PASS** |
| 6. Cross-record consistency | **FAIL**, two contradictions, four checks clean |
| 7. The plan matches the records | **FAIL**, four ungoverned requirements and one wrong round |
| 8. Milestone mapping complete | **PASS** |
| 9. Feasibility spot-checked | **PASS**, one host fact wrong |
| 10. Publication hygiene of the documents | **PASS**, one observation |
| 11. No decision left implicit | **PASS**, one decider unnamed |

No bar is NOT TESTED. Two bars are FAIL, and both lie in the plan and in one pair of records
rather than in the substance of the decisions: the identity, read-only, apply and provider
decisions themselves survive every check they were put to.

## Bar 1 -- docs only: PASS

```
git diff --name-status fec2d58..ab7d9de
git diff --stat fec2d58..ab7d9de
git diff --name-only fec2d58..ab7d9de | rg -v '^docs/(adr/|overhaul-plan\.md$)'
```

Seven paths, all additions or the README's index line: records 0004, 0005, 0006, 0007, 0008,
`docs/adr/README.md` (six insertions, one deletion) and `docs/overhaul-plan.md` (387 lines).
999 insertions, 1 deletion. The filter for paths outside `docs/adr/` and the plan returns
nothing: no source, no test, no dependency, no `CHANGELOG.md`, no `AGENT.md`. Records 0001 to
0003 landed before the range base and are therefore verified by content, not by this diff.

## Bar 2 -- house bars hold at the tip: PASS

```
git rev-parse HEAD                      # ab7d9de6658ec97428005c908a75bb25ddea7916
git ls-remote origin main               # the same object id
uv run pytest tests/ -q -W error        # 564 passed, 2 deselected in 81.14s, exit 0
uvx ruff check src tests scripts        # All checks passed!, exit 0
uv run python scripts/publishing_hygiene_lint.py  # scanned=319 violations=0, exit 0
git log --format='%h %G? %s' fec2d58..ab7d9de     # three commits, all %G? = G
```

The tip equals the published `main`, every commit in the range carries a good signature, and
the three house bars hold exactly as the brief stated them: 564 passed with 2 deselected,
ruff clean, publishing hygiene `violations=0` over 319 tracked files with none skipped.

## Bar 3 -- format conformance: PASS

Checked per record with `rg -n '^## |^- Status|^- Date'` and `wc -l`.

- All eight records carry the six required sections in order: the one-paragraph decision
  first, then context, decision, alternatives, consequences and what the record does not
  decide. All eight also carry one further section, "Requirements this record resolves",
  between decision and alternatives -- uniformly, so it reads as part of the template.
- All eight say `Status: proposed (awaiting owner ratification)`, all eight carry
  `Date: 2026-11-07`, all eight declare a decider and an empty supersession line.
- Lengths are 90 to 157 lines, against the ~160 ceiling: 921 lines over eight records,
  a mean of 115.1, with a three-line margin at the longest (`calc batch`).
- `docs/adr/README.md`'s index lists exactly the eight records that exist, no more and no
  fewer, each with `proposed` status, and each one-line decision is a faithful compression
  of the record's own paragraph -- including the qualifying clauses, not only the headline:
  the surrogate id's exclusion from edit plans, the read-only/`--refresh` split, the
  structural-not-semantic identity claim, keeper's missing hash guard, and the naming
  record's five conditions.

Two editorial findings, neither of which changes a verdict:

- **`README.md` calls the section list "fixed" and enumerates six; every record ships
  seven.** The list in `README.md` reads "Sections are fixed: the one-paragraph decision
  first, then context, decision, alternatives, consequences, and what the record does not
  decide." A reader comparing the template to the records finds a section the template does
  not name. Either the convention line gains the requirements section or the section is
  renamed as optional.
- **`ADR-0004` states one consequence twice.** "A plan records the tree it was prepared
  against and nothing about the user's intent, so the preview is the only place intent is
  checked -- which is why preview writes nothing and shows every affected file rather than a
  summary" appears as the tail of the third bullet under `## Consequences` and again as the
  fifth bullet in the same section. One of the two should go.

**Citation integrity, raised here because it is a format question.** The records cite
measurements by artifact path -- `runs/milestone0/probe-symbol-identity.txt`,
`probe-ref-attribution.txt`, `keeper-capabilities.txt`, `probe-lsp-availability.txt`,
`probe-language-workload.txt`, `probe-naming-inventory.txt` and their scripts. `git ls-files`
returns nothing under `runs/milestone0/`: the artifacts are on the machine that produced them
and in no published tree. The README's own provenance rule accepts "a measured artifact" as a
citation, so the set satisfies the letter of its convention and fails its purpose -- a reader
of the published repository cannot open a single one of these numbers' sources. Tracking the
two directories, or inlining the measurements into the audit, is the fixer's call.

## Bar 4 -- every owner requirement resolved: PASS

| Requirement | Record and section that resolves it | How |
|---|---|---|
| 1. Three version axes, service-free index | ADR-0001 sections 1, 2, 3, 5 | three named axes with a written policy matrix; extras for embedding, vectors and MCP; a `chunks` table that makes a vectors-off pass complete and a later vectors-on pass reparse nothing; per-side fingerprint comparison |
| 2. Identity is not the locator; revalidate symbol and file hash at apply | ADR-0002 sections 1, 2 and ADR-0004 section 2 | the line-free key; the surrogate id explicitly barred from plans; apply revalidation that names its basis as `structural` with `file_hash`, `decl_hash` and `region_hash` |
| 3. Per-file commits, compensation and recovery, no atomicity claim | ADR-0005 sections 1, 4 | four observables stated as what a caller may assume; a per-file after-failure table; ordered undo ids and keeper's journal as the recovery reference; the words "atomic", "transaction" and "all-or-nothing" forbidden |
| 4. Read-only query, preview and validation; staleness separated from refresh | ADR-0003 sections 1 to 4 and ADR-0004 section 6 | two command classes; three staleness verdicts in text and JSON; `--refresh` as the only read-path repair; preview and validate write nothing |
| 5. One language server, chosen by real workload | ADR-0006 section 2 | Python first on a counted workload, C# second by a stated reversal rule, C++ and TypeScript deferred with reasons, the install left to the owner |

No requirement is mentioned without being resolved. Two of them are resolved with an explicit
addition or disagreement rather than by agreement, which is the behaviour the requirement
forward demanded: ADR-0001 rejects the embedding-dimension probe inside a staleness verdict
and resolves the proposal's `auto` conflict in favour of its own section 4.3; ADR-0004 adds a
region hash the requirement did not ask for and says so; ADR-0005 replaces the reviewed
position's "ordered best-effort undo" with a fail-closed recovery and explains why.

Every record states a disagreement with the proposal, the audit or a reviewed position: two
each in records 0001, 0002, 0003, 0004, 0006, 0007 and 0008, four in 0005. The set was
required to disagree where it disagreed, and it does -- including against the audit, which is
harder to write than against the proposal.

## Bar 5 -- the audit's ten open questions settled: PASS

| Audit question (section 11) | Settled in |
|---|---|
| Identity: gain one, or keep `(file, name, start_line)`? | ADR-0002 section 1 |
| What a vector-less index stores per chunk; is Qdrant still the chunk store? | ADR-0001 section 3 (a `chunks` table; Qdrant becomes a vector store only) |
| Is `mcp` a hard dependency or an extra? | ADR-0001 section 1 and ADR-0007 section 1 |
| Are the vector clients importable-but-optional? | ADR-0001 section 1 (both moved behind the `semantic` extra, imported in constructors only) |
| What may a read do when stale? | ADR-0003 sections 2, 3, 4 |
| Does a failed search still exit 0? | ADR-0003 section 5 (moves 0 to 1) |
| Which language gets the first provider? | ADR-0006 section 2 |
| Is the apply path filekeeper-only or also direct? | ADR-0005 section 5 (filekeeper only; a direct path is refused) |
| Does structural search extend `find-symbol` or become a new command? | ADR-0002 section 4 |
| Does `overview` get MCP parity? | ADR-0007 section 1 (`overview` is kept as one of the eight tools) |

Ten of ten, each answered in a named section rather than gestured at. The only one answered by
removal rather than addition is the last: `overview` gains a tool, and the tools it would have
been mirrored by (`outline`, `deps`, `find_callees`, `changed_symbols`) are the ones dropped.

## Bar 6 -- cross-record consistency: FAIL

Four checks are clean:

- **The surrogate id.** ADR-0002 section 2: "It is **not** the edit-plan locator." ADR-0004
  section 2 builds the locator from the key plus the file hash, and section 8 says "None of
  the three uses the surrogate id". Consistent, in both directions.
- **Read-only against post-apply reparse and preview.** ADR-0005 section 7: "The reparse
  writes nothing." ADR-0004's lifecycle table gives preview and validate the same verdict.
  Consistent in substance -- see the contradiction below on who classifies `plan-apply`.
- **The extras against the MCP verdict and the provider dependency.** ADR-0001 makes `mcp` an
  extra imported only by the entry point; ADR-0007 section 1 repeats that rule and narrows the
  surface to eight read-only tools; ADR-0006 treats a server as a lazy, per-invocation
  dependency and neither record exposes the provider over MCP. No drift. One asymmetry is
  unaddressed by both: ADR-0007 registers `semantic_search` "only when the semantic extra is
  importable", so the MCP extra depends on the semantic extra under a condition, which the
  ADR-0001 table does not model.
- **The schema change and the reindex register.** ADR-0002 says a shared landing means one bump
  ("manifest version 7"); the plan's register says "**one** schema revision, **one** reparse".
  One, not two, in both places. The register's label is the problem -- see below.

**Contradiction: `plan-apply` is a read command under ADR-0003 and a write command under
ADR-0004.**

> ADR-0003, section 1: "**Read commands**: every other command."
> ADR-0004, Consequences: "`plan-apply` is a write command: ADR-0003 section 1's roster
> predates it, so this record adds it to the class that record defined rather than superseding
> that record."

ADR-0003's enumeration is exhaustive by construction: the write list is closed and everything
else is a read. `plan-apply` is not in the write list, so ADR-0003 says it is a read; ADR-0004
says it is a write. The set's own reconciliation does not work: a record may not amend another
record's enumeration without superseding it (the README's amendment rule says a decision
"changes only by a superseding record"). One side must win. Either ADR-0003's roster gains the
plan commands in place of a supersession, or ADR-0004's consequence is rewritten to declare
ADR-0003 amended. The plan cannot close round 4 with a contradictory classification, because
round 4 ships the command the classification is about.

**Contradiction: two different refusal tests for a converted position.**

> ADR-0004, section 3 rule 8: "a converted position that does not land on a line boundary is
> refused for phase-1 operations"
> ADR-0006, section 5: "A converted position that does not land on a byte boundary of the
> decoded line is refused (`PositionConversionFailed`), because a silently rounded position is
> the same defect class as a plan column nobody checks."

These are not the same rule. A position in the middle of a line lands on a byte boundary of
the decoded line and does not land on a line boundary: ADR-0006 accepts it and normalizes the
edit to whole lines in section 6, while ADR-0004 refuses it. Read ADR-0004's clause as scoped
to phase-1 operations only and the pair reconciles -- every phase-1 operation is whole-line,
so a mid-line position has no phase-1 use -- and ADR-0006 section 6 is the phase-2 mechanism
that ADR-0004 left open, which ADR-0006 says explicitly ("Section 6 adds the mechanism that
record left open"). But the reconciliation lives in a reader's inference, not in either text:
ADR-0004 states the boundary test as the adapter's rule, and the adapter is one module. The
fix is one clause on either side, naming which test the adapter applies and when.

**A third, softer one: "exactly" plus a range.**

> ADR-0004, section 2: "A locator is exactly ADR-0002's key plus the file hash:"
> -- followed by `path, language, qualified_name, symbol_type, decl_hash,
> range{start_line, end_line, start_byte, end_byte}`

ADR-0002's key is four members plus the file hash; the locator shown carries a range that is
neither. The range is evidence rather than identity, and section 2 uses it correctly, but the
word "exactly" invites a reader to check membership and find a sixth member. Wording only.

**One more, on the register's label rather than its count.** The register marks the chunk-table
change "a **migration plus one text-fill pass**, not a reparse: chunk text is produced by the
chunker from source the pass already reads". ADR-0001's consequences describe the same step as
"the pass ... reprocesses each file once". Running the chunker over every file is what the
records elsewhere call a parse, so "not a reparse" is a promise the mechanism does not keep.
The count still says one, which is what matters for the owner's approval; the label should say
so too.

## Bar 7 -- the plan matches the records: FAIL

What is right: every round names the records that govern it; each acceptance block names
observables rather than claims; the "not doing" list contradicts no record (no rename, no
Serena, one server, no persisted semantic identifier, no direct-write fallback, no nested
subcommands, no chunker rewrite); the register covers both index-rewriting changes and excludes
the four rounds that rewrite none; and the sizing section's counts arithmetically agree with
the round sections -- five rounds, nine legs (2 + 2 + 1 + 2 + 2), five gates, five reviews.

What fails:

- **Round 5 runs a command no record enables.** Acceptance includes
  `code-indexer rename-symbol ...  # no server: exit 2, UnsupportedOperation, no text edit`.
  ADR-0004 section 1 reserves the operation and refuses it in phase 1 (`rename_symbol` is
  "reserved and refused in phase 1"); ADR-0006 section 6 says enabling it "is a **schema bump
  to version 2** ... This record fixes the normalization rule so that record can be written; it
  does not enable the operation." No record in the set grants a `rename-symbol` command, and the
  round that ships one is governed by records that say it does not exist yet.
- **Round 5's expected exit code contradicts the exit-code taxonomy.** A language server that
  is not installed is, in ADR-0003 section 5's words, "backend or extension unavailable:
  service unreachable, extension not installed" -- exit 3. The plan's acceptance line expects
  exit 2, matching ADR-0006 section 3, which defines 2 for "an operation the selected provider
  does not support". Those are two different situations and the plan collapses them: no server
  at all is not the same as a server that answered `unsupported`.
- **Round 1 and round 5 both require `--json` on commands no record grants.** Round 1 adds it
  to five read commands and round 5 to four write commands. ADR-0003's "What this record does
  not decide" says: "Whether the thirteen registered commands that have no `--json` mode gain
  one (audit section 8 tracks them as a separate gap)." The record the round is authorized by
  explicitly declines to decide the thing the round delivers. This is the cleanest instance of
  a plan requirement without a grant, because the record names the gap and refuses it.
- **Round 5 splits the skill, and no record decides that.** The deliverable includes "the SKILL
  split into a short entry document plus reference files". Round 5's records are ADR-0006,
  ADR-0007 section 5 and ADR-0008; ADR-0007 section 5 governs the MCP documentation, not the
  skill's shape, and neither other record mentions the skill at all.
- **The plan schedules the description correction in the wrong round.** Round 5's record line
  reads "ADR-0008 (no rename, and the description correction that round 2 owes)", and the
  round 5 deliverable is "the README rewritten around the new behaviour". ADR-0008 section 2:
  "`pyproject.toml`'s description is corrected in the round that makes it false (ADR-0001's)".
  ADR-0001's dependency split lands in round 1 and its vectors-off pass in round 2; neither is
  round 5. The plan says "round 2 owes" and then pays it in round 5.
- **Round 1 names a flag round 2 introduces.** Round 1's first deliverable names "the two flags
  that turn a read into a write (`--refresh`, and `--vectors` on the pass commands)". `--vectors
  auto|on|off` is ADR-0001 section 4, which the plan assigns to round 2's second leg; round 1's
  record line covers ADR-0001 sections 1 and 5 only. A help-text promise for a flag that does
  not exist yet is a deliverable the round cannot meet.
- **Two smaller ones.** The defect section promises "`index-status` counting parse failures, so
  the verdict is reachable from the CLI", which no record grants -- ADR-0004 section 7 grants a
  per-file verdict, ADR-0001 section 4 grants `index-status` rows and vectors fields only. And
  the register's exclusion sentence says round 1 "changes behaviour and tests and touches no
  data", which omits the one thing round 1 does write: ADR-0007 section 3's usage record,
  appended under the index root on every invocation.

## Bar 8 -- milestone mapping complete: PASS

The proposal defines nine milestones, numbered 0 to 8 (section 10 of the proposal). The plan's
mapping table carries nine rows, one per milestone, each with a disposition and a landing place:
absorbed for 0; split for 1, 2, 5 and 7; merged into round 4 for 3 and 6; round 4 for 4;
deferred for 8. Every disposition is consistent with the round sections that follow -- the
splits name the round each half lands in, the merges name the round and the pair of records,
and the deferral names the trigger ("the first item after round 5 ... from measured use"). No
milestone is unaccounted for and no round is unexplained by the table.

## Bar 9 -- feasibility spot-checked: PASS, with one wrong host fact

Re-measured at the tip, by `command -v` and the package manager rather than from scratch:

```
clangd, pyright-langserver, pylsp, roslyn-language-server,
OmniSharp, csharp-ls, gopls, typescript-language-server   all ABSENT
apt-cache policy clangd        Candidate 1:18.0-59~exp2 (noble/universe)
node --version                 v26.7.0
npm --version                  11.19.0
dotnet --version               8.0.133
jedi (project interpreter)     spec None, not importable
keeper --version               keeper 0.1.8
```

The two facts ADR-0006 and the plan rely on hold: no language server resolves, and each named
install route exists as described -- an apt candidate for `clangd`, node and npm present, dotnet
present, `jedi` absent from the project interpreter. `keeper --version` is still 0.1.8.

ADR-0005's claim about `apply-patch` matches the issue filed on the filekeeper tracker, which is
open and unanswered: it reports the hunk relocation (declared at line 4, applied at line 7, exit
0, no warning), the absence of any `--expected-hash` equivalent on the patch path, and the
opposite half ADR-0005 also states -- that a two-file patch whose second file fails exits 7 with
the first file untouched. The record's three claims and the issue's three observations agree,
including the one that weakens the record's own argument.

**The wrong fact.** ADR-0006 context item 4 says: "`dotnet` 8.0.133 is present with **no** global
tools installed, so a Roslyn server is one `dotnet tool install`." `dotnet tool list -g` returns
`dotnet-script 2.0.1` as an installed global tool. The conclusion survives -- a Roslyn server is
still one `dotnet tool install` -- but the premise as written is false at the tip, and a record
whose measured premises are its authority cannot carry one that a single command contradicts.

## Bar 10 -- publication hygiene of the documents: PASS, one observation

Swept with `rg` over `docs/adr/*.md` and `docs/overhaul-plan.md`, one class at a time
(the patterns are described rather than printed, because printing them would publish the
very strings this bar forbids -- the lint caught that in this record's first draft):

- absolute host paths, account-home roots and Windows user paths -- none
- the house's internal profile names, whole-word and case-insensitive -- none
- board card ids and house issue numbers -- none
- em dashes, against the README's "--" rule -- none

```
uv run python scripts/publishing_hygiene_lint.py   scanned=319 violations=0 (none skipped)
```

No absolute host path, no internal role name, no board card id, no em dash. The whole-word
sweep for role names returns nothing; an earlier substring sweep lit up on "deliverable" and
"several" only -- two ordinary English words that contain a house name as a substring -- which
is why the word-boundary form is the one relied on. Quoted text in the
set is provenance for a disagreement -- the proposal's own words, a reviewed requirement's
words, keeper's README headings -- never the owner's plan text; the brief's prohibition is on
quoting the owner, and no file does. The publishing lint, which also enforces the role-name and
card-id classes by digest over tracked paths, reports zero violations across 319 files with
none skipped.

**The observation.** "Household" appears eight times across records 0006, 0007 and 0008, and
ADR-0008 twice cites "the household convention document" for a count of 13 mentions. It is not
a role name and the lint permits it, but it is a reference a reader outside the house cannot
follow, in a document set that is otherwise careful to cite things a reader can open. The same
fixer's pass that makes citations followable can decide whether "the house's convention
document" stays.

## Bar 11 -- no decision left implicit: PASS, one decider unnamed

Read against the bar's question -- does the paragraph answer a question, or restate the context
-- all eight answer. Each names a choice, its scope and its refusal: services off the core by
default, identity line-free with the surrogate id barred from plans, reads that do not write,
plans named by their own bytes, one hash-guarded primitive with no second path, an interface
before a server, a narrowed MCP surface on probation, and the name kept with five conditions for
changing it. None of the eight ends at "more evidence is needed" in place of a decision; the one
that comes closest, ADR-0007, decides first (keep, narrow, instrument) and defers only the
survival question.

That deferral is where the one gap is. The bar allows a "needs more evidence" conclusion only
when the record says what evidence and who decides. ADR-0007 section 4 names the evidence
precisely -- one line per invocation, one release cycle, zero invocations means removal and any
invocations means a review of which tools were called -- and never names a decider. The plan
supplies it at "What the owner is asked to decide", item 5: "Take the MCP keep-or-remove
decision after the usage record has one release cycle of data." So the set answers the question
the bar asks, in two documents instead of one; a reader of ADR-0007 alone cannot tell whose
decision it is. One sentence in the record closes it.

## Disagreements found, in one place

Everything the set's parts say against each other, whether or not a later document resolves it:

1. **`plan-apply`'s command class** -- ADR-0003 section 1 makes it a read command by its
exhaustive "every other command"; ADR-0004's consequences make it a write command and say the
roster is not superseded. Unresolved.
2. **The converted-position refusal test** -- a line boundary in ADR-0004 section 3 rule 8, a
byte boundary of the decoded line in ADR-0006 section 5. Resolved in practice by ADR-0006
section 6, which is the more specific and later mechanism, and by reading ADR-0004's clause as
phase-1-scoped; not resolved in either text.
3. **The locator's membership** -- "exactly ADR-0002's key plus the file hash" in ADR-0004
section 2, immediately followed by a range the key does not contain. Wording only; the range is
used as evidence.
4. **"Not a reparse" versus "reprocesses each file once"** -- the plan's register against
ADR-0001's consequences, over the same chunk-table pass. The count agrees (one); the label does
not.
5. **The description correction's round** -- ADR-0008 section 2 says "the round that makes it
false (ADR-0001's)"; the plan says round 5 and calls it "the description correction that round
2 owes". The plan is wrong on its own terms.
6. **The missing-server exit code** -- ADR-0006 section 3 puts an unsupported operation at exit
2, ADR-0003 section 5 puts an uninstalled backend at exit 3, and the plan's round 5 acceptance
expects 2 for "no server".
7. **The `--json` gap** -- ADR-0003 explicitly leaves thirteen commands' `--json` undecided;
the plan delivers it for nine of them across rounds 1 and 5.
8. **The MCP extra's conditional dependence on the semantic extra** (ADR-0007 section 1's
`semantic_search` registration rule) is modelled by neither record's dependency table. Not a
contradiction, an unmodelled case.

## Three things to look at hardest before ratifying

**1. Round 5 asks the owner to ratify work no record authorizes.** A `rename-symbol` command
(which ADR-0004 reserves and ADR-0006 defers to a schema-2 successor), a skill split, and
`--json` on four write commands all appear in round 5's deliverable and acceptance with no
granting record -- and round 1 has the same `--json` problem against ADR-0003's explicit
non-decision. Ratifying the plan as written ratifies requirements the records decline. Either
the plan loses those lines or a record gains them before ratification; that is a one-line
decision per item but it has to be made now, because the plan is what the rounds are held to.

**2. ADR-0003's write-command roster does not contain the command that writes sources.** The
read-only guarantee is the set's most consequential promise, and its own definition makes
`plan-apply` a read command. ADR-0004's attempt to fix this without superseding ADR-0003 is the
one place in the set where a record knowingly bends the amendment rule. Ratifying ADR-0003 as
written ratifies an enumeration that its own successor contradicts -- and round 4 ships the
command in question.

**3. The two records that must agree about positions refuse on different tests.** ADR-0004 says
a converted position not on a line boundary is refused; ADR-0006 says a converted position not
on a byte boundary of the decoded line is refused and then normalizes mid-line edits to whole
lines. Until one clause names the adapter's actual rule, the pair leaves a reader unable to say
whether a rename plan can be built at all -- and the answer differs by a whole class of edit.

A fourth, smaller, for the ratification note rather than the record: ADR-0006's "no global
dotnet tools" premise is false (one is installed), and six measurement artifacts the set cites
are not in the published tree.

## What this record does not decide

Nothing about the records or the plan. It does not edit them, does not choose between the two
sides of any contradiction, does not ratify, and does not schedule a fix round. The findings
above are for the fixer to route; a FAIL here is a finding with both sides quoted, not a rewrite.

> Evidence note: `runs/` is kept in the working checkout and is not
> published. A `runs/...` path cited here names a local artifact, not a
> link a reader can open.
