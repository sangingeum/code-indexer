# Revised overhaul plan

- Status: awaiting owner ratification
- Date: 2026-11-07
- Runs alongside: ADR-0001 to ADR-0009

## What this document is

This is the order in which the overhaul lands. It decides no behaviour: each round is
authorized by the records it names, and where this plan and a record disagree, the record
wins and the plan is corrected. What the plan adds is sequencing, the observable acceptance
of each round, the gate each round must pass, and the owner decisions that block a round.

It replaces the proposal's milestone list as the working order. The proposal's milestones are
not discarded -- the mapping below says what happened to each one and why.

## How the proposal's milestones became these rounds

| Proposal milestone | Disposition | Where it lands |
|---|---|---|
| 0 -- repository audit and decision record | absorbed, complete | the audit, its verification gate, ADR-0001 to ADR-0005 and this record set |
| 1 -- core/extension separation | split | the dependency boundary, lazy construction and the extras move into round 1; the vectors-off manifest, the chunk table and the three version axes into round 2 |
| 2 -- structural search and symbol reliability | split | identity and ambiguity in round 2; the query filters and evidence labels in round 3 |
| 3 -- edit plan foundation | merged into round 4 | a plan format with no apply path is untestable end to end, and ADR-0004 and ADR-0005 were written as one pair |
| 4 -- structural editing | round 4 | joined by the plan foundation and the filekeeper adapter |
| 5 -- LSP rename provider | reordered, split | the interface and its contract suite in round 5; a real server only if the owner installs one, which may leave that half of the round undone |
| 6 -- filekeeper adapter and recovery | merged into round 4 | the apply half of the same pair of records |
| 7 -- agent experience and MCP decision | split | the MCP relay, tool narrowing and usage record in round 1; the skill split and the documentation in round 5; the keep-or-remove decision after one release cycle of measured use |
| 8 -- semantic extension reassessment | deferred | no round. It is the first item after round 5, and it starts from measured use rather than from a schedule |

Why the mapping is not one-to-one:

- **Milestone 1 was scheduled as packaging and is not.** Its acceptance criterion -- a clean
  core install can index and query a supported repository -- fails today before a single file
  is scanned (`src/code_indexer/indexer.py:105-106` asks the vector store for a collection and
  the embedder for a dimension first). That is a data-model change, and it belongs beside the
  identity change rather than before it.
- **Milestones 3, 4 and 6 are one unit.** A plan that cannot be applied proves nothing, an
  apply path with no plan is filekeeper's job, and the two records that define them were
  written together.
- **Milestone 5 is last because one of its two halves needs an install decision** and the other
  half -- the interface and its contract suite -- must not wait for that decision. The order
  inside the round is interface first, always.
- **Milestone 7's measurement cannot be a milestone.** Adoption is measured continuously; the
  decision it feeds is a date, not a deliverable.

## The rounds

Every round has the same closing gate: a verification pass that reproduces the round's own
acceptance commands from a clean checkout, and a two-axis review (specification and standards)
run in fresh contexts by someone other than the author. A round is not closed because its
tests are green; it is closed when both axes have reported.

### Round 1 -- the surfaces stop lying

**Goal.** A read command never writes, a failed query exits non-zero, and the default query
cost is bounded by construction. The MCP relay stops reporting every failure as success, and
the package stops importing three client libraries it may not need.

**Records.** ADR-0003 in full; ADR-0001 sections 1 and 5 (the dependency-boundary rules only);
ADR-0007 sections 1 to 3 (extra, narrowed surface, relay, usage record); ADR-0008 section 2
(the description correction, which this round makes false and therefore owes).

**Deliverable.**

- every one of the 27 commands classified as read or write, with the flag that turns a read into
  a write (`--refresh`) named in the help text; `--vectors` belongs to round 2 (ADR-0001 section 4)
  and is documented with the pass commands there; the six plan commands round 4 adds are already
  classified in ADR-0003 section 1, which this round lands;
- staleness reported as `fresh`, `stale` or `unknown` in text and in JSON, and repaired only by
  an explicit `--refresh`; `--skip-stale-check` accepted and inert;
- the exit-code taxonomy of ADR-0003 with a failed search moving off 0;
- one project by default, `--all-projects` to fan out, one embedding attempt, a wall budget
  with honest partial results;
- no client library imported by `code_indexer/__init__.py`, no eager `Core` construction, and
  `mcp`, `ollama` and `qdrant-client` behind extras;
- the MCP server as an extra with up to eight read-only tools, a relay that preserves both
  streams and the exit code, and a one-line usage record per invocation;
- `--json` on the five read commands that answer under this contract and lack it today:
  `list-projects`, `lookup-project`, `index-status`, `find-definition`, `find-references`
  (ADR-0003 section 7, which decides the whole gap of thirteen commands and not only these five);
- the package description corrected, because this round's dependency split is what makes it false
  (ADR-0008 section 2); the README's opening paragraph is still round 5's.

**Acceptance.** These are the commands a gate runs; each one is an observable, not a claim.

```
uv run pytest tests/ -q -W error                      # green and warn-clean
QDRANT_URL=http://127.0.0.1:1 OLLAMA_URL=http://127.0.0.1:1 \
  code-indexer semantic-search "anything"             # exit 3; ErrorType: on stderr; stdout empty; under the 30 s budget
code-indexer semantic-search "anything" --all-projects # exits 3 within the budget, never after 804 s
code-indexer overview                                  # exit 0; writes nothing; one stale line when the index is stale
code-indexer find-symbol Core --json | jq .stale_known  # a boolean, present on every read answer
python -X importtime -m code_indexer.cli --help 2>&1 | rg 'mcp|qdrant_client|ollama'   # no output
uv run pytest tests/test_read_contract.py tests/test_exit_codes.py -q
uv run pytest tests/test_mcp_server.py -q              # registered tool set equals the documented list
```

The budget and the exit codes are asserted by tests, not by a human reading a table: the two
behaviour changes here are exactly the kind that a later refactor reinstates silently.

**Gate.** Verification plus two-axis review. The review's standards axis checks that no read
command still reaches a writer through a path the classification missed; the specification axis
checks ADR-0003's five sections against the diff.

**Risk that could sink it.** Agents have been relying on the implicit refresh. The behaviour
change is real and intended, and it will look like a regression to a workflow that used to
self-heal. The observable that reveals it is a stale notice in a transcript followed by an
answer the user treats as current.

**Owner decision.** None blocks the round. The behaviour change is a visible breaking change and
belongs in the release note as one.

### Round 2 -- one manifest revision: identity, and an index that needs no service

**Goal.** A pass with vectors off writes a complete manifest on a machine with no vector store
and no embedding service, and a symbol's identity survives a line shift.

**Records.** ADR-0001 sections 2 to 5; ADR-0002 sections 1 to 3; ADR-0004 section 7's parse-verdict
rule, which this round must make satisfiable before round 4 relies on it.

**Why one round and one schema revision.** The chunk table and the identity columns are both
manifest schema changes, and the pass that writes them is the same pass. Landing them as two
revisions would cost a second migration and a second full traversal of every project for no
intermediate benefit; ADR-0001 already states that a shared landing means one bump.

**Deliverable, in two legs, committed as each builds.**

- *Leg A, identity and parse verdicts.* `qualified_name`, `container`, `language`, `decl_hash`
  and a revision-scoped surrogate `id`; the null-target and resolution columns on references;
  `symbol_id` and `index_revision` in JSON; and a per-file parse verdict of `parsed`,
  `parse_error` or `parse_unavailable` produced by reading the parser's own result, never by
  inferring from the chunker.
- *Leg B, the service-free pass.* The `chunks` table with text and hash, three version axes with
the policy matrix, `--vectors auto|on|off`, a per-side fingerprint comparison, and
`semantic-search` refusing on a vectors-off project with the remedy named.

**Why the parse verdict is here and not later.** `chunk_text` cannot fail: it falls back to the
regex chunker when a language has no grammar, when the parse raises and when it yields no
chunks, and it walks an error-tolerant tree when `root.has_error` is set
(`src/code_indexer/ts_chunker.py:122-160`). ADR-0004 forbids taking a syntax verdict from it,
which leaves exactly one place to fix: the extractor's own return value, in the rewrite that is
already touching that function for qualified names and declaration hashes. Fixing it in round 4
would make round 4 the first round whose claim depends on a verdict that cannot fail.

**Acceptance.**

```
# throwaway root, both services pointed at an unroutable address
code-indexer add-project <tmp>                         # exit 0; writes a complete manifest
code-indexer index-status <tmp> --json | jq '{rows,vectors,vectors_state}'   # rows=current, vectors=off
code-indexer find-symbol <name> --json | jq '.symbols[0]'                  # qualified_name, decl_hash, symbol_id
uv run pytest tests/test_identity.py tests/test_vectors_off.py -q
uv run pytest tests/test_parse_verdict.py -q            # syntax error -> parse_error; no grammar -> parse_unavailable
uv run pytest tests/test_vectors_on_reads_no_sources.py -q   # the vectors-on pass opens no source file
```

`tests/test_vectors_on_reads_no_sources.py` is the one that keeps ADR-0001 honest: it makes the
source tree unreadable to the pass and expects the embedding to succeed from stored chunk text.

**Gate.** Verification plus two-axis review, after leg B at the tip; leg A is reviewed on its own
commit first, because a schema change reviewed together with a second schema change hides which
one broke a query.

**Risk that could sink it.** The fingerprint gate is a one-way door: after the schema bump every
`find-*` answers exit 4 with "requires rebuild" until the pass has run for that project. The
observable is `index-status` reporting `rows=absent` and a query exiting 4.

**Owner decision.** The reindex approval in the register below. The round cannot close without it,
because the tests can run on throwaway roots but the live indexes cannot be migrated silently.

### Round 3 -- structural query on a stable identity

**Goal.** The identity model becomes a query surface, and ambiguous names return candidates with
disambiguating metadata rather than a sorted guess.

**Records.** ADR-0002 sections 4 and 5.

**Deliverable.** `--name-pattern`, `--container`, `--visibility`, `--language`, `--inherits` and
`--has-call` on `find-symbol`, with `--type` and `--symbol-type` kept as aliases; the ambiguity
envelope with `match_count` and `ambiguity.candidates[]`; `evidence: ast|heuristic|unknown` on
every candidate that depends on attributed syntax; `find-callers` and `find-callees` unioning over
candidates and labelling each hit instead of taking the first row.

**Acceptance.**

```
uv run pytest tests/test_structural_filters.py -q
uv run pytest tests/test_ambiguity.py -q           # a duplicated name refuses with two candidates, never one
code-indexer find-symbol --name-pattern '*Manager' --json | jq '.match_count'
code-indexer find-symbol --inherits Base --json | jq '.symbols[].evidence'   # ast|heuristic|unknown, never absent
```

**Gate.** Verification plus two-axis review.

**Risk that could sink it.** The filters answer for AST-parsed files only, and 101,197 of
113,751 reference rows are textual. The observable is an answer that says `unknown` where a user
expected a fact -- which is the honest outcome, and the round fails only if `unknown` is rendered
as "no".

**Owner decision.** None. This round adds a column's worth of query surface and no data change.

### Round 4 -- plans that can be applied, and recovery that can be read

**Goal.** A change is prepared as a content-addressed document, validated without writing,
previewed without writing, applied through filekeeper with a hash guard per edit, and reported
per file when it fails partway.

**Records.** ADR-0004 and ADR-0005, which are the format and its apply path.

**Deliverable.** Plan files named by the hash of their canonical bytes; the six plan commands
(three writers, validate, show, apply), each with `--json`; the three structural operations; the
read-only pre-pass that refuses before the first mutation; one keeper mutation per edit with
`--expected-hash` and, where it fits, `--expected-content`; the per-file report with applied,
failed and not-attempted; the ordered operation ids and the receipt path.

**Acceptance.**

```
uv run pytest tests/test_plan_format.py tests/test_plan_apply.py -q
code-indexer plan-replace-body ... --print | jq -r .plan_id    # equals the hash of the document's own bytes
code-indexer plan-validate <ref>                                # writes nothing; a stale file refuses
code-indexer plan-apply <ref>                                   # applied; a changed file -> exit 5, zero mutations
PATH=/usr/bin:/bin code-indexer plan-apply <ref>                # exit 3, ErrorType: KeeperUnavailable, no fallback
uv run pytest tests/test_partial_apply.py -q                    # file 3 of 5 fails; 1 and 2 applied; 4 and 5 untouched
```

**Gate.** Verification plus two-axis review. The standards axis checks that no command applies a
plan without a plan reference and that preview and validation stay write-free; the specification
axis walks ADR-0005's four observables against the report.

**Risk that could sink it.** A plan format that is correct and unusable: the locator refuses on
ordinary code because the body does not begin at a line start, or because two identically
signatured overloads collide. The observable is the refusal rate in the round's own report and
the frequency of `SubLineEditUnsupported` in the fixtures.

**Owner decision.** None. keeper 0.1.8 is installed and its guards are measured; the adapter is
the only new dependency, and it is a dependency of the feature rather than of the install.

### Round 5 -- a provider behind a contract, and an agent surface that fits in a context window

**Goal.** The refactoring interface exists and was proved before any server was installed --
including the rename operation it will carry once the plan format enables it -- and the
documentation an agent loads is small enough that it does not displace the work.

**Records.** ADR-0006; ADR-0009 (the skill layout, its budgets, the owner of the installed copy
and the drift test); ADR-0007 section 5 (the MCP documentation); ADR-0003 section 7 (the four write
commands' JSON); ADR-0008 (no rename -- the description correction it carries is round 1's, not
this round's).

**Deliverable.** The provider interface, a stub provider and the contract suite; a real provider
only if the owner installs a server, with Python first; the skill split as ADR-0009 decides it --
an entry document of at most 120 lines and 8 KiB, five reference files read on demand, the command
roster generated from the registry, and the installed copy pinned by the new drift checks; the
README rewritten around the new behaviour and carrying the MCP parity list on its own; and
`--json` on the four write commands that still lack it (`add-project`, `index-more`,
`remove-project`, `reindex-project`), granted by ADR-0003 section 7, so an agent can read a pass
result without parsing prose.

**Acceptance.**

```
uv run pytest tests/test_provider_contract.py -q     # the stub passes with no server installed;
                                                     # an absent provider is exit 3, an operation
                                                     # it does not support is exit 2 (ADR-0006 s.3)
code-indexer --help | rg -c rename-symbol || echo 0  # 0: the command does not exist in phase 1
uv run pytest tests/test_plan_schema.py -q           # a plan declaring rename_symbol is refused
                                                     # (exit 2, UnsupportedOperation, no text edit)
uv run pytest tests/test_docs.py -q                  # entry document, references, registry and
                                                     # installed copy agree (ADR-0009 section 5)
uv run pytest tests/ -q -W error                     # still green and warn-clean
uvx ruff check src tests scripts                      # clean
uv run python scripts/publishing_hygiene_lint.py      # violations=0
```

**Gate.** Verification plus two-axis review.

**Risk that could sink it.** The chosen server cannot rename, and the first provider proves the
interface works while delivering no operation a user wanted. The observable is the contract
suite's capability report saying `unsupported` for rename -- the accepted answer, and the trigger
for the C# candidate named in ADR-0006.

**Owner decision.** The language-server install. It is optional: the interface half closes without
it, and the round is reported as partially delivered rather than blocked.

## The two defects that are scheduled first, by name

These are ordered by severity, not by appeal. Everything else in the plan is a feature; these two
are wrong answers the tool gives today.

1. **A failed query exits 0, and the default query is unbounded** (round 1, ADR-0003). Measured:
   in scope, a search with both backends unreachable exits 0 with `error:` on stdout after 67 s;
   unscoped, the same call takes 804.2 s over twelve projects and then reports the same success
   (audit section 4, gate finding F5). **Proof of the fix:** the acceptance commands above -- exit
   3, stderr, empty stdout, within the budget, in both shapes -- plus a test that a total
   backend failure and an empty result are different answers with different codes.
2. **The chunker cannot fail** (round 2, ADR-0004 section 7). Measured by reading the code path:
   `chunk_text` returns the regex fallback on a missing grammar, on a parse exception and on zero
   chunks, and accepts an error-tolerant tree when `root.has_error` is set
   (`src/code_indexer/ts_chunker.py:122-160`). A broken file is therefore indexed as if it parsed,
   and no surface can report otherwise. **Proof of the fix:** `tests/test_parse_verdict.py` -- one
   file with a deliberate syntax error reports `parse_error`, one file in a language with no
   grammar reports `parse_unavailable`, a clean file reports `parsed` -- plus `index-status`
   counting parse failures (ADR-0004 section 7 gives `index-status` that scope), so the verdict is
   reachable from the CLI and not only from a test.

Round 1 comes first because a wrong exit code poisons every consumer that tries to branch. Round 2
carries the parse verdict because the extractor is rewritten there anyway; round 4 is the first
round that would otherwise depend on a verdict that cannot fail.

## Owner-approved reindex register

Every change that rewrites existing index data, with what it costs and what it is not.

| Change | Record | What it forces | Measured cost |
|---|---|---|---|
| symbol identity: qualified names, declaration hashes, surrogate id, reference target columns | ADR-0002 | a **reparse** of every file, because a declaration hash cannot be derived from rows written by the old extractor | one full parse of the indexed tree: 445 files, 45,460 lines, 1,733 symbol rows and 113,751 reference rows on the live index |
| the `chunks` table with text and hash, and three version axes | ADR-0001 | a **migration plus one full pass that re-chunks every file** -- a reparse of the chunk rows, not a second read of the file: the pass reads each file once and chunks it from that same read, so it writes the chunk rows the old extractor never stored without adding a traversal | a re-traversal of the same 445 files, 2,198 chunks, and roughly the indexed source volume (about 2.4 MB against a 21,557,248-byte manifest); no re-embedding, because unchanged chunk text is a cache hit |
| both, landed together | round 2 | **one** schema revision, **one** reparse | one traversal instead of two, and one migration note instead of two |

What is **not** in the register, and why: the read contract (round 1) changes behaviour and tests
and rewrites no index data -- it does write one file this register does not track, the MCP usage
record ADR-0007 section 3 appends under the index root on every invocation, so round 1 touches the
index root without changing a row; the structural filters (round 3) are query-only; the plan format
and the apply path (round 4) write plan files and receipts outside the index; the provider work
(round 5) persists no server identifier at all.

What the owner is approving, stated plainly: the tool stops answering from the old rows, refuses
with exit 4 until each project has been passed once, and the pass is the ordinary per-project
command that already exists. It is not a paid operation and it does not rebuild the vector
collection, because the embedding identity does not change.

## Not doing

Drawn from the proposal's non-goals and from what the audit showed is a rewrite rather than a
change:

- **No rename** of the product, package, repository, executable or state directory (ADR-0008).
- **No Serena integration**, and no second implementation of an operation a provider already
  answers (proposal sections 7.1 and 7.2).
- **No second language server** in this plan: one real provider, chosen by measured workload, with
  Python first and C# next only if Python's contract run fails (ADR-0006).
- **No persisted semantic identifier** on symbol rows.
- **No resolution of the 101,197 textual reference rows.** ADR-0002 pays for the columns and
  stages the population; resolving them needs an import graph and a scope model, which is a
  different project.
- **No direct-write fallback** beside filekeeper (ADR-0005 section 5).
- **No split of `cli.py` (1,716 lines) or `core.py` (1,358 lines) into a service layer.** The
  audit calls the split a real option; it is also a rewrite of every command's construction path,
  and nothing in this plan needs it. The lazy-constructor work of round 1 is the part that pays.
- **No migration of the 27 flat commands into nested subcommands.**
- **No rewrite of the chunker.** Only its verdict path changes.
- **No `--json` on `watch`, `unwatch`, `eval` or `eval-compare`.** Two of them drive a daemon and
  two write report files; a JSON mode would be a second output format with no consumer.
- **No making filekeeper language-aware, no making this tool a filesystem manager, no making the
  task board analyse code** (proposal section 12).
- **No replacement of semantic search, and no new vector store.** Round 5 ends with a
  reassessment, not a rewrite.

## Risk register

Each row names the observable that would reveal the risk early enough to act.

| Risk | What would reveal it | Round |
|---|---|---|
| Stale answers are now labelled instead of repaired, and users read them as current | a stale notice followed by an answer the user treats as fresh in a report or transcript | 1 |
| Agents depended on implicit refresh and their workflows break | a workflow that used to self-heal now returning a stale answer with exit 0 | 1 |
| The schema bump locks every query until each project is passed | `find-*` exiting 4 with `IndexRequiresRebuild`, `index-status` reporting `rows=absent` | 2 |
| The reparse is heavier than the migration note implies | per-project pass duration in the round report, and the manifest size against today's 21,557,248 bytes | 2 |
| Two overloads with identical normalized signatures collide | a validation refusal listing two candidates with the same `decl_hash` | 2 and 4 |
| Parse verdicts disagree with an editor or a compiler | `parse_error` on a file a compiler accepts, or `parse_unavailable` where a grammar exists | 2 |
| A keeper upgrade keeps its version and drops a guard | the capability probe failing with `KeeperCapabilityMissing`, exit 3 | 4 |
| Plans are correct and unusable on ordinary code | the refusal rate in the round report, and `SubLineEditUnsupported` on the fixture corpus | 4 |
| The first language server cannot rename | the contract suite's capability report saying `unsupported` for rename | 5 |
| Narrowing MCP breaks a client nobody knew about | the usage record's first window: unknown-tool errors, or no lines at all | 1 and after |
| Documentation drifts from behaviour again | the docs-to-registry drift test, and the generated tool list replacing the stale count | 1 and 5 |

## Sizing, in units this plan can defend

No durations. The shape is: **five rounds, nine implementation legs, five verification gates, five
two-axis reviews.** Round 1 is two legs (read contract; dependency boundary and MCP), round 2 is
two (identity; the service-free pass), round 3 is one, round 4 is two (plan format; apply path),
round 5 is two (provider interface; agent surface). A leg ends with a signed commit that builds;
a round ends with its gate.

The only ordering that is not negotiable: round 1 before round 2, because a schema migration on a
codebase whose reads still write is a migration that cannot be verified; round 2 before round 3,
because the filters have nothing to filter on; round 2 before round 4, because a plan locator is
the identity; round 4 before round 5, because a provider produces edits that only a working plan
path can carry.

## What the owner is asked to decide

1. **Ratify ADR-0001 to ADR-0009 and this plan.** Nothing in the rounds starts before that.
2. **Approve the round 2 reindex**, as described in the register: one schema revision, one reparse
   per project, no collection rebuild, no service cost.
3. **Accept the round 1 behaviour change** as a visible breaking change: reads stop repairing the
   index, and a failed query stops exiting 0.
4. **Decide the language-server install before round 5's second half.** It is optional; the
   interface half of round 5 closes without it, and its absence is reported rather than absorbed.
5. **Take the MCP keep-or-remove decision after the usage record has one release cycle of data.**
   The narrowing and the relay fix are already decided; the survival question is not.

Nothing in this plan asks for a rename decision. ADR-0008 records why, and under what conditions
that changes.
