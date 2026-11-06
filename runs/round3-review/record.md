# Round 3 two-axis review

- Reviewed tip: `67afca4` (round base `84fd194`); `origin/main` == `67afca4` at review start
- Reviewer: the household reviewer/architect
- Card: identifier withheld (an authored `runs/*.md` record is hygiene-governed and a board id is a
  prohibited token there; the dispatch brief carries it)
- Gate verdict reviewed: **PASS with findings** (four, all low). Its bars are of record; I ruled on
  them and re-ran none of them.
- Deliverable: this record and three captures beside it. No product code, no dev record, no gate
  record, no ADR and no plan was edited by me.

## Verdict

**PASS WITH CHANGES.**

The delivered surface answers ADR-0002 sections 4 and 5, and it answers them with the honesty the
round claimed: every filter is one AND clause in one read, `match_count` comes from the same clause
set as the rows, a capped answer says it was capped, an ambiguous name reports every candidate, and
the two graph commands union over candidates instead of taking the first row. I reproduced the
behavioural claims I doubted and all of them hold, including the branch none of the tests reach
(`find-symbol NAME --limit 1 --json` returns `match_count: 2`, one row, `truncated: true`, and two
candidates — the envelope is built from a full re-read, not from the capped page).

There is no blocker: no contract breaks, no deliverable is missing, and nothing here reports a
negative that is false. What keeps this from a clean pass is a class the gate found once and the
round contains four times: **a positive claim attached to an identity the store cannot support.**
The gate's F4 is one instance (a graph root labelled `ast` while the same payload's candidate record
for that row says `unknown`). Two more are mine: `--has-call`/`--inherits` credit one name-level
reference to every same-named symbol in the file, and the `heuristic` label is documented but
unreachable. All are small, located, and fit the follow-up vehicle the board already has.

## Findings

### Blocker

None. Stated rather than implied: the read path stays read-only (`core.read_manifest`,
`core.py:226-233`), the schema is untouched, the exit taxonomy matches the round-1 contract, every
plan deliverable is present, and every defect below over-answers or mislabels — none of them turns an
absent fact into a denial, which is the one outcome the round's own risk line called a failure.

### Should-fix

**S1 — a graph definition root still asserts `ast` where the same payload says `unknown`.**
`graph.py:288` and `:420` build every root `confidence="ast"` unconditionally. The round's own
ambiguity envelope reports these rows with `evidence_of`, so one JSON object now carries two answers
for one row: `runs/round3-review/surface-probes.txt` shows `match_count: 9` for `Core`, the two
`skeleton.out` candidates with `evidence: "unknown"`, and all nine roots with `confidence: "ast"`,
the two `.out` roots included. This
is the gate's F4, and the payload contradicting itself is why I fold it rather than carry it: the
round rewrote both lines and added the surface that disagrees with them.

**S2 — `--has-call` / `--inherits` credit one reference to every same-named symbol in the file.**
The EXISTS clause is `r.file = s.file AND r.from_symbol = s.name` (`manifest.py:663-671`), and
`refs.from_symbol` is a bare name with no container (0 of 12,636 non-null rows carry a dot). Two
symbols called `run` in one file are therefore both credited with the union of both bodies' calls.
Frozen read at `index_revision=62` (`runs/round3-review/over-attribution.txt`): of 1,488 symbol rows
the call filter hits, **37 share their `(file, name)` with another symbol row**; `base_class` has
none (20 hits, 0 shared). The live case is `runs/milestone0/probe_offline.py`: `Probe.run:39` and the
module-level `run:63` both report
`accept,append,time,settimeout,recv,sendall,close,dict,update,monotonic,run,...` as their attributed
callees. This is a false positive of the same class as F4 — an `evidence: ast` claim attached to an
identity the store cannot distinguish. It is not fixable in this round by code: ADR-0002 section 4
defines the join as `from_symbol`, and the store has no container column to join on. So the fold is
disclosure (record + docs), and the structural fix belongs to the staged reference-resolution work
that ADR-0002 section 3 already schedules — no new card.

**S3 — one table has two column lists, and the evidence label depends on which one was used.**
`_SYMBOL_COLS` (`manifest.py:566-569`, 13 columns, no `parse_verdict`) still serves
`definitions_of`, `symbols_for_file` and `all_symbol_names`; `_SYMBOL_FILTER_SELECT`
(`manifest.py:600-605`) joins `files.parse_verdict` in. `identity.evidence_of` reads the verdict and
falls back to `source` when it is `None` (`identity.py:32-48`). Consequence: for a file whose verdict
is `parse_error`, an AST row is `unknown` on `find-symbol` and `ast` in an ambiguity candidate or an
`--exactly-one` refusal, because the candidate path never joined the verdict. Latent today
(`parse=parsed:170,parse_error:0,parse_unavailable:791`) and it becomes live the first time a
partially-broken file is indexed. The fix is the same edit as S1: one column list, one label
function, called by every surface that reports evidence.

**S4 — `heuristic` is unreachable, and its docstring describes a case the pipeline cannot produce.**
`identity.py:36-47` says `heuristic` is "a textual scan (no grammar, or a parse that fell back)". But
`ts_chunker.parse_file` sets `source = "ast"` on every chunk of a parsed file (`ts_chunker.py:168-170`)
and returns the regex fallback only together with `parse_unavailable` (`:144`, `:148`, `:155`, `:167`),
which the same function labels `unknown`. The frozen read agrees: the symbol ledger is exactly
`('ast','parsed') 2033` and `('regex','parse_unavailable') 286` — no third combination exists. So the
middle value of the round's three-value vocabulary has no producer, and the sentence explaining it
names a case the code routes to `unknown`. Either reserve the value explicitly for a future extractor
that supplements an AST with textual rows, or drop it; either way the docstring, `SKILL-CLI.md`
("a textual scan") and `docs/json-schema.md` must stop describing it as a state a row can be in. The
round record is honest about the empty bucket; the code's own docstring is not.

**S5 — the two graph commands' help text was not updated, and text mode never says the name was
ambiguous.** `cli.py:428-449` and `:469-477` still describe the pre-round surface ("json adds
truncated/omitted") with no mention of `match_count`, `ambiguity` or `targets`, while `SKILL-CLI.md`
and `docs/json-schema.md` promise all three. In text mode `_emit_graph` (`cli.py:487-512`) prints the
tree and notes only: the `[targets: ...]` labels appear, but nothing says nine definitions matched.
`find-symbol` text mode got exactly that sentence this round (`cli.py:1942-1945`); the sibling
surface did not. One sentence of help and one line of text output close it.

### Nice-to-have

**N1 — `EVIDENCE_VALUES` (`identity.py:29`) has no reader.** Dead surface, the same class as the
dead constants the previous round removed.

**N2 — test gaps, named by branch** (the test name is not the coverage):
`tests/test_structural_filters.py:258` is called *do not scan every row* but asserts only the two
shapes that do not scan — the leading-wildcard `SCAN s` that the docs must state as an exception
is not asserted, so
the claim "a suffix pattern is an index scan" is proven nowhere. Untested branches, each verified by
me only by hand: the exact-name + structural-filter path and its substring suppression
(`cli.py:1864`); the ambiguity re-read when the page was capped (`cli.py:1909-1911`);
`find-callees`' accumulation of a second target on a callee reached from two candidates
(`graph.py:451-456`, the gate measured it live on `Config` and no test pins it); the invalid
`--visibility` refusal (`cli.py:1844-1848`); a `parse_error` file's label (nothing constructs one); any
`heuristic` row (unconstructible — S4).

**N3 — `find-definition --exactly-one` silently changes the empty answer.** With no definition:
without the flag, `no exact-match symbols named '...'` and exit 0; with it, `NotFoundError: no indexed
definition for '...'` and exit 1 (`runs/round3-review/surface-probes.txt`). That is defensible for a
caller that asked for exactly one, but `docs/json-schema.md` documents only the ambiguous case. One
sentence, plus a decision recorded rather than implied.

**N4 — the false index claim lives in three places, not one** (gate F3 named one).
`SKILL-CLI.md:207` ("the name index answers it, no new index"), `CHANGELOG.md:9` ("a case-sensitive
glob the name index answers"), and the source docstring at `manifest.py:623-626`, which is worse than
either: "a prefix pattern is an index range and a suffix pattern an index scan, **never a pass over
the table**" — the planner answers `SCAN s` for `*Manager` (`runs/round3-gate/b3-query-plan.txt`). The
same capture shows `SCAN s` for `--language` and `--visibility`,
`SCAN s USING INDEX sqlite_autoindex_symbols_1` for `--kind`, and
`SCAN s USING INDEX idx_symbols_file_start` for `--inherits`, `--has-call` and plain browse,
so the honest sentence is "no index was added; the name index answers a prefix glob as a range, and
the remaining filters are scans that are cheap at this size" — measured, not asserted.

**N5 — one commit's message under-describes its content.** `a1cf445` ("feat(find-symbol): report
ambiguity with candidates instead of picking") carries 126 lines of `graph.py`, i.e. the union over
candidates — a deliverable of its own. History is not worth rewriting for this; it is recorded so the
next leg splits the concern.

**N6 — `Manifest.symbol_query_plan` is a product method whose only caller is a test**
(`manifest.py:737-761`; `rg` finds no other caller). I accept it: the alternative is an `EXPLAIN`
literal duplicated in the test, which is the drift this method exists to prevent, and its docstring
declares the audience ("diagnostics and tests only"). Named here so the acceptance is a decision and
not an oversight.

**N7 — one vocabulary, two spellings.** `find-references` labels a `.md` file's rows
`confidence=heuristic` (the reference confidence is `heuristic` for the textual fallback,
`manifest.py:950-958`), while `find-symbol` labels the same file's symbol rows `evidence=unknown`.
Both are honest under their own definitions; a reader holding both surfaces sees two words for one
condition. Not this round's to reconcile — the ADR owns the vocabulary — but the next record that
touches evidence should say which axis each word describes.

## Ruling on the gate's four findings

**F1 — record committed with an editorial placeholder: fold.** `runs/round3-structural-query/record.md:284`
is `(to be filled before the push)` under a verification block that is filled in. It is the round's own
record, one line, and the record is being edited anyway for F2. Checked: no other placeholder survives
under `runs/round3-structural-query/` or `runs/round3-gate/` (`rg 'to be filled|TBD|TODO|FIXME'`).

**F2 — the record's counts cannot describe one index state: fold, and the cause is sharper than the
gate recorded.** The gate said the record does not pin a revision. Reading the history says why: the
filter numbers were authored in `d27fbcb` (the first code commit) and never re-measured, while the
whole-index ledger (`2,311 rows: ast 2,032 / unknown 279`) first appears in `3d93972` — check
`git show d27fbcb:runs/round3-structural-query/record.md` versus the line's absence in that commit and
its presence at `3d93972`. So one document quotes two snapshots taken three commits apart, and the
commits in between added the two test modules whose rows the later numbers would include. Fold: quote
`index_revision` beside every block, or re-quote every block from one frozen snapshot, and state which
leg each was measured at. (The board's dispatch text for this item quotes 2,029 where the record and
the gate both say 2,026 — the numbers to annotate are the record's own; the fix is unaffected.)

**F3 — `SKILL-CLI.md` overstates the name index: fold, widened to three sites.** See N4: the same
claim is in `CHANGELOG.md:9` and, inverted into a stronger form, in `manifest.py:623-626`. Fixing one
of three leaves the round's own source comment contradicting its own capture.

**F4 — a graph root labelled `ast` where its file has no grammar: fold.** Pre-existing at the base,
but the round rewrote both root constructions and added the envelope that disagrees with them, so the
fix belongs to the same leg: derive the label from the row's evidence (`identity.evidence_of`) instead
of the constant — and do it together with S3, because the root rows come from `definitions_of`, which
does not join the verdict yet. One label function over one column list.

## Spec axis — ADR-0002 sections 4 and 5, against the shipped tree

**Each filter, answered where it says it is answered.** `--kind` is one option with three names
(`cli.py:1789`), and the test pins all three spellings to the same list. `--name-pattern` is `GLOB`
(case-sensitive by construction; the fixture shows `*Manager` hit and `*manager` empty) and the clause
is exactly `s.name GLOB ?` (`manifest.py:643`) — nothing widens it to `LIKE '%…%'`.
`--file`, `--container`, `--visibility` (NULL folded into public, `manifest.py:654-659`; the CLI
refuses anything but the two stored values, `cli.py:1844-1848` — and the store only ever writes those
two, `ts_chunker.py:220-236`, so no row is unfilterable), `--language` (the denormalized column, no
join), `--inherits` and `--has-call` (the attributed `base_class`/`call` rows through a correlated
EXISTS). All eight compose as AND in one statement, and one test composes four of them.

**Evidence.** Every row the filter read returns carries one of the three labels, read from the file's
verdict through the join, and a zero from an attributed filter carries `evidence_note` naming how many
files holding symbols have no AST extractor (`identity.py:50-63`, `manifest.py:763-775`). The plan's
risk line — "the round fails only if `unknown` is rendered as no" — is met: zero rows plus a note,
never a bare negative. S4 is about one of the three labels having no producer; it does not make any
answer false.

**Ambiguity.** `match_count` is emitted on every answer; `ambiguity.candidates[]` carries the seven
fields the record names plus `qualified_name` and `evidence` (declared in `identity.py:64-80`).
The envelope is emitted in exact-name mode only. I rule that a correct reading of section 5 rather
than a draw: the section is about a *name lookup* that resolves to several identities, and a browse
answer of 596 rows is a set the caller asked for, not a pick to be disambiguated. The record states
the scope explicitly, which is what matters — nothing is silently absent.

**The single-symbol refusal.** `find-definition --exactly-one` refuses with `AmbiguousSymbolError`
(an `ArgumentError`, exit 2, `errors.py:57-79`), the message lists every candidate with the
disambiguators that exist, and `--json` carries the list as data (`cli.py:404-414`). `resolve_one`
refuses at the helper too, so a future caller inherits the rule. N3 is the empty case, not the
ambiguous one.

**The graph union.** Reproduced: `find-callers Core` reports `match_count: 9`, nine roots, and both
`skeleton.out` candidates present. The gate's B6 measured that all 51 direct hits carry all nine
labels and that an import-resolved edge carries one; the fixture test pins both shapes. `find-callees`
labels each callee with the definition it was reached from and accumulates a second label when one
callee is reached from two — the gate measured five on `Config`, and that branch has no test (N2).

**Composition and truncation are honest, not plausible.** `count_symbols` and `find_symbols` are two
statements over one clause builder (`manifest.py:611-736`), so `match_count` cannot drift from the
rows; `truncated = total > len(rows)` (`cli.py:1882`) and text mode prints `... N of M matches shown
(--limit N reached ...)`. Verified on the branch the tests miss: 2 matched, 1 shown, `truncated: true`,
two candidates.

**Missing, or added without a grant.** Nothing the plan lists is missing. Four surfaces go beyond the
plan's deliverable sentence and each is either granted by the ADR or declared by the record:
`--exactly-one` (required to give section 5's refusal a non-vacuous home, declared); `evidence_note`
(the mechanism the plan's risk line asks for); `match_count`/`truncated` ("composition and truncation
are honest answers"); `Manifest.symbol_query_plan` (internal, test-only, N6). No second query command
was added — the ADR's rejection of one is honoured, and the `git diff` shows 27 `@app.command`
registrations before and after.

**Where the record and the code disagree.** F1 (placeholder), F2 (counts from two legs), F3/N4 (the
name index), F4/S1 (root label), S4 (`heuristic` described as reachable), and one sentence of the
record — "`find-callers` and `find-callees` carry `match_count`/`ambiguity` whenever the subject name
has several definitions" — which is true of JSON only; text mode carries the targets and not the
envelope (S5).

## Standards axis

**Terminology.** No coordinate label in an identifier, file name or test name: the new module is
`identity.py`, the tests are behavioural sentences (`test_a_duplicated_name_refuses_with_two_candidates_never_one`),
and the only numbered references are `ADR-0002 section N` in module docstrings and comments — the same
class as the schema-revision references the previous review accepted. `runs/round3-*` directories are
the process's own record locations, not code names.

**Read-only path.** Every surface under review opens through `core.read_manifest` (`core.py:226-233`,
`writable=False`) and the diff touches no `CREATE`/`ALTER`/`SCHEMA_VERSION`; the gate's B8 measured the
manifest byte-identical across reads. `symbol_query_plan` runs `EXPLAIN` on the same open, so the
diagnostic cannot become a writer.

**Exit codes.** `ArgumentError` 2 for a bad `--visibility` and for an ambiguous `--exactly-one`,
`NotFoundError` 1 for a missing definition, empty stdout on failure with the one-line
`<ErrorType>: <message>` shape in text and the same object in JSON — the round-1 contract, extended by
one documented flag (N3).

**Duplication.** One clause builder for three reads is the right shape and the round's best structural
decision; the two column lists (S3) are the one place the same table is described twice, and it has
already produced a divergence. No filter is implemented twice, and no second command duplicates
`find-symbol`.

**Over-engineering.** Only N1 is dead. `candidate()` adds two fields beyond the seven the record names,
and the docstring says why (disambiguation without a second read) — accepted. `GraphNode.targets` is
emitted as an empty list rather than omitted, which keeps the JSON shape stable — accepted.

**Tests.** 24 new tests over two modules, and they assert behaviour, not implementation: the filters
answer and their aliases agree, the glob is case-sensitive, composition narrows, truncation says so,
the envelope's fields are all present, the refusal carries data in both modes, and the graph labels
narrow on an attributed edge and widen on a name-level one. The gaps are enumerated in N2 — the two
that matter are the leading-wildcard plan (the doc's own exception) and the callee multi-target branch
(the gate's live evidence has no test behind it).

**Commits.** Seven, all signed (`%G?` = `G`), one coherent concern each except `a1cf445` (N5). The
round record is started inside `d27fbcb` and finished in `3d93972`; that is how F2 happened, and it is
worth not repeating: a record's numbers should be captured where the record is finished.

**Docs.** `SKILL-CLI.md` is the skill source and carries the filters, the evidence rule, the truncation
signal and the ambiguity envelope; `docs/json-schema.md` matches the payloads I read (including
`candidates` as data on the JSON refusal) except the `--exactly-one` empty case; CHANGELOG has the
section under `## [Unreleased]`, in the two-Unreleased shape the previous round already used. The three
index-claim sites are N4/F3, and the graph help text is S5.

## Evidence produced by this review

All captures are in this directory, masked by `mask.py` (repository root and home directory replaced
with their published spellings; re-running it is a no-op).

- `over-attribution.txt` — one **frozen** read (the standing watcher was stopped with
  `unwatch --all` at pid 340189, the probes run, and the watcher restarted at pid 345973):
  `index_revision=62`, 2,319 symbol rows, ledger `('ast','parsed') 2033` / `('regex','parse_unavailable') 286`,
  `parse_error: 0`, 31 `(file, name)` groups with more than one row, `call` filter hits 1,488 of which
  37 share their `(file, name)`, `base_class` 20 hits of which 0 do, and 0 of 12,636 non-null
  `from_symbol` values containing a dot. S2 and S4 rest on this file.
- `surface-probes.txt` — `find-callers Core` (nine candidates, two of them `evidence: unknown`, all nine
  roots `confidence: ast`), `find-symbol find_symbol --limit 1 --json` (`match_count: 2`, one row,
  `truncated: true`, two candidates), and `find-definition` with no definition both with and without
  `--exactly-one` (exit 0 versus exit 1).
- `mask.py` — the masking step, idempotent.
- No bar was re-run; the numbers the gate published are of record and I ruled on them as published.

## What I deliberately did not review

- Rounds 4 and 5 (the plan format, validate/show/apply, the per-file failure report, provider work):
  not started in this round and untouched by it.
- The test suite was not re-run (the gate's `704 passed, 2 deselected`, warn-clean, is of record), and
  no coverage number was measured.
- The MCP surface: `server.py` is byte-unchanged and the round declares the new filters CLI-only.
- Gate bars: ruled on, not re-established. The populations in `over-attribution.txt` are mine, frozen;
  the round's own populations I did not re-measure, because my ruling on F2 turns on the history of the
  numbers rather than on their current value.
- The ADR and the plan themselves: governing documents, not this round's output.
- I wrote no product code and made no board write.

## Folds for the fix leg

| id | file:line | change | kind |
|---|---|---|---|
| FOLD-A (F1) | `runs/round3-structural-query/record.md:284` | delete the placeholder line | docs |
| FOLD-B (F2) | `runs/round3-structural-query/record.md` | quote `index_revision` beside every population, or re-quote them all from one frozen snapshot; say which leg each block was measured at | docs (reproducibility) |
| FOLD-C (F3+N4) | `SKILL-CLI.md:207`, `CHANGELOG.md:9`, `src/code_indexer/manifest.py:623-626` | state that no index was added, that a prefix glob is a range and that a leading wildcard is `SCAN s`; drop "never a pass over the table" | docs |
| FOLD-D (F4+S1+S3) | `src/code_indexer/graph.py:288`, `:420`; `src/code_indexer/manifest.py:566-569`, `:600-605`; `src/code_indexer/identity.py:32-48` | one column list with the verdict join, one label function, and roots labelled from the row's evidence — so one payload cannot say `unknown` and `ast` about one row | fix |
| FOLD-E (S2) | `src/code_indexer/manifest.py:663-671`; record and docs | disclose that an attributed filter matches by name within the file, so two same-named symbols in one file are both credited; the structural fix waits for staged reference resolution | docs |
| FOLD-F (S4) | `src/code_indexer/identity.py:36-47`, `SKILL-CLI.md`, `docs/json-schema.md` | stop describing `heuristic` as a state a row can be in; keep the value as reserved or remove it | docs (+ optional dead-branch removal) |
| FOLD-G (S5) | `src/code_indexer/cli.py:428-449`, `:469-477`, `:487-512` | help text names `match_count`, `ambiguity` and `targets`; text mode says when the subject had several definitions | docs + one line of output |
| FOLD-H (N3) | `docs/json-schema.md`, `src/code_indexer/cli.py:1985-1990` | document the `--exactly-one` empty answer (exit 1) or align it; either way say it | docs |
| not folded | `src/code_indexer/identity.py:29` | `EVIDENCE_VALUES` is dead — remove it in passing or leave it; not worth a leg of its own | N1 |
| not folded | `tests/test_structural_filters.py` | the four uncovered branches of N2; worth tests when the branches are next touched, none of them a defect today | tests |
