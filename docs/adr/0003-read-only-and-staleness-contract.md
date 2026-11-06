# ADR-0003: read-only and staleness contract

- Status: accepted (owner ratification 2026-11-07)
- Date: 2026-11-07
- Deciders: owner (ratification), architecture review
- Supersedes / Superseded by: --

## Decision in one paragraph

Every command is either a write command or a read command, and a read command never writes the manifest, the vector store or the sources. Staleness is detected cheaply from the manifest and the filesystem, reported in both text and JSON with the verdict `fresh`, `stale` or `unknown`, and repaired only by an explicit `--refresh` or a pass command. A failed query exits non-zero with a taxonomy line on stderr instead of printing an error string on stdout with exit 0. An unscoped query is bounded: it resolves one project by default, requires `--all-projects` to fan out, makes one embedding attempt rather than a retry chain, and stops at a wall budget with honest partial results.

## Context

1. Nine read commands re-index silently when a flag is absent: `semantic-search`, `skeleton`, `outline`, `find-symbol`, `find-definition`, `find-references` (and `get-code-context` under `--fresh`, `index-status` under `--refresh`) via `Core.maybe_refresh` (`src/code_indexer/core.py:220-250`, veto at `:232`), plus `eval` through `core.search`. `--skip-stale-check` is the only thing that stops them, so the default query path is a write path (audit section 1).
2. A stale read is observably wrong, not merely old. `overview --skip-stale-check` reported `core.py` at 1,626 LOC against a 1,358-line file; reproduced at the audit tip, where the same call still prints 1,626 and `wc -l` still prints 1,358 for 445 files and 45,460 LOC (audit section 10, item 9). Two further read commands, `list-projects` and `lookup-project`, are documented as registry-only but dial Ollama for a fingerprint probe -- 34 s and 5 connections offline (audit section 4).
3. A failed search cannot be told from an empty one. `Core.search` raises when every project fails (`src/code_indexer/core.py:711-712`), and `search_for_display` catches every `ValueError` and returns the message as the answer string (`src/code_indexer/core.py:913-914`). The CLI prints `error: search failed on every project (...)` on **stdout** and exits 0; measured in both the scoped and the unscoped shape (audit section 4; gate F5).
4. The cost is structural, not a timeout setting. The scoped call took 67 s and the unscoped default took 804.2 s over twelve registered projects, because the query path iterates every project and retries the embed call per project (audit section 4; gate F5). The expensive call is the default one, which is what an agent runs.
5. No consumer can branch on any of this today. The MCP relay concatenates stdout and stderr and discards the exit code (audit section 6), and `semantic_search` carries `readOnlyHint=False` with the reason stated in the source: the staleness probe writes (`src/code_indexer/server.py:207-209`).

## Decision

### 1. Two command classes

- **Write commands**: `add-project`, `index-more`, `remove-project`, `reindex-project`, `watch`, `unwatch`, `sweep-orphans --apply`, the three plan writers (`plan-replace-body`, `plan-insert-symbol`, `plan-delete-declaration`: each writes one plan document into the plan directory, and writes nothing at all under `--print`), `plan-apply` (the sources, through filekeeper, plus one apply receipt under the project's state directory), and any invocation carrying `--vectors on|off` or `--refresh`.
- **Read commands**: every other command. A read command opens no write transaction, calls no `maybe_refresh`, and dials no service to reach a staleness verdict. A read command invoked with `--refresh` performs one pass first and therefore writes; the guarantee below applies to every invocation without that flag.

- `--skip-stale-check` stays accepted and becomes inert; it is documented as "the default". `--fresh` on `get-code-context` keeps its meaning (read the live file, ignore cached symbol rows) and writes nothing.

The roster is closed and complete for the whole set: the twenty-seven commands that exist and the six plan commands together account for every command the plan schedules, and each is in exactly one of the two lists. The six are the three plan writers, `plan-validate`, `plan-show` and `plan-apply`; the four stages of ADR-0004 section 6 are the stages, not the count. The plan commands that only read are reads -- `plan-validate` and `plan-show` write nothing, and `plan-apply --dry-run` runs the same pre-pass and mutates nothing. A command a later record adds is classified by that record; no command classifies itself, and a command outside these lists is a defect in this list rather than a third class.

### 2. What "stale" means

- Every read answer describes the index as of `meta.last_indexed`, never the working tree. There is no implicit convergence.
- Detection compares the indexed path set and each file's stat identity (`size`, `mtime_ns`, `inode`, all already on `files`) against the tree, using the scanner's existing include and exclude rules and stat calls only; content is hashed only for files whose stat changed. No service is contacted, and the walk has its own budget so that a very large tree degrades to `unknown` rather than to a long silence.
- Three verdicts, never a boolean guess: `fresh` (nothing differs), `stale` (with reasons), `unknown` (the check could not run -- project path absent, or the scan exceeded its budget). `unknown` is not `fresh`.

### 3. How staleness is reported

JSON, on every read command's answer -- in the commands that have a JSON mode today and in the five that gain one under section 7 -- additive, `schema` stays 1:

| Field | Meaning |
|---|---|
| `stale` | boolean, unchanged meaning; keeps `get-code-context`'s existing field type |
| `stale_known` | boolean; false means the verdict is `unknown`, so `stale: false` never means "checked and fresh" on its own |
| `stale_reasons` | list of `{kind: modified|added|deleted, file}`, capped at five |
| `stale_reason_count` | total count before the cap |
| `index_revision` | the revision the answer was read from |
| `last_indexed` | the timestamp of the pass that wrote it |

Text mode prints exactly one stderr line and nothing on stdout: `Warning: index is stale (3 files changed since 2026-11-06T22:10:00Z: 2 modified, 1 deleted); answers describe the index, not the tree. Refresh: code-indexer reindex-project <path>`. A fresh answer prints nothing extra; an `unknown` answer prints one line saying the check did not run.

### 4. How refresh is triggered

- `--refresh` is accepted by every read command and is the only refresh trigger besides the pass commands and the watcher.
- `--refresh` takes the per-project flock, runs one incremental pass, and answers from the result. A flock miss is a hard failure: `LockedError: indexing in progress`, exit 5, and no answer on stdout. An explicit request for fresh data must never print a stale answer under a "refreshed" label.
- The watcher remains the only zero-flag writer and takes the same flock, so a refresh and a watcher pass serialize instead of interleaving.
- No separate `refresh` command: the three pass commands already are that command, with scope and exclusion handling this option reuses.

### 5. Exit-code taxonomy

| Code | Meaning |
|---|---|
| 0 | success, including zero hits -- "no results" is an answer |
| 1 | any other runtime failure |
| 2 | usage or argument error |
| 3 | backend or extension unavailable: service unreachable, extension not installed, no language server installed for the requested operation |
| 4 | the index requires a rebuild: fingerprint mismatch, vectors absent, needs-reindex |
| 5 | lock or concurrency conflict -- the same meaning filekeeper gives code 5, so an agent that branches on 5 learns one thing across both tools |

Two situations that look alike get different numbers, because a caller's next move differs. **Nothing installed or nothing reachable** -- no service, no extension, no language server -- is 3: the remedy is to install or start something. **A working provider answering that it does not implement the operation** is 2: the request asked the tool for something it does not offer, which ADR-0006 section 3 records as a usage-class refusal. A taxonomy that gave both 3 would tell a caller to install what is already running; one that gave both 2 would say the tool was asked wrongly when the provider is simply absent. Breaking changes, stated plainly: a failed search moves 0 -> 1 (this is the defect being fixed); 3, 4 and 5 were all 1 before, so only a consumer that parsed the error type notices; a consumer testing `== 0` is unaffected; and reads that used to index implicitly no longer write at all.

### 6. Unbounded cost

- Default project resolution is the containing project inferred from the invocation path, the rule every read command already uses for symbol and path hints. Fan-out requires `--all-projects`.
- The query path makes one embedding attempt with a short timeout. The retry chain belongs to the write path, where a retry can actually succeed.
- A wall budget (default 30 s per invocation) stops the fan-out; remaining projects are reported as skipped with `reason=budget`, JSON carries `partial: true`, and the exit code is 0 only if at least one project answered. If none answered, the exit code is 3 when the cause was backend unavailability and 4 when the index required a rebuild.
- All three factors behind 804 s -- twelve projects, a retry chain, an unbounded wall -- are removed by construction rather than tuned.

### 7. JSON mode on the commands that lack it

Thirteen registered commands answer in text only (audit section 8). This record decides the gap instead of leaving it to a round:

- **Gain `--json`**: `list-projects`, `lookup-project`, `index-status`, `find-definition`, `find-references`, and the four pass commands `add-project`, `index-more`, `remove-project`, `reindex-project`. The first five are read answers a consumer must parse; the four are the commands whose outcome a caller most often branches on, and a pass that reports its result in prose is a pass an agent cannot check.
- **Stay text-only**: `watch`, `unwatch`, `eval`, `eval-compare`. Two drive a daemon whose state is read through `index-status`, and two write report files that are already machine-readable; a second output format there would have no consumer.
- **Shape**: additive, `schema` stays 1, like section 3 -- the object carries the fields the text renderer prints, plus `ErrorType` on failure. The five read commands also carry section 3's staleness block, because they answer from the index; the four pass commands do not, because a pass reports its own work rather than a verdict about the index.
- **Scope**: this decides which commands have a mode, not what every shape contains. The plan commands' shapes are ADR-0004's, and they carry `--json` from their first commit.

## Requirements this record resolves

- **Requirement 4** in full. Default read-only is section 1; staleness reported as a JSON field plus a one-line notice is section 3; refresh made explicit is section 4; the exit-code taxonomy and the bounded cost are sections 5 and 6. Preview and validation writing nothing is satisfied by section 1's class rule; the content-addressed plan store it refers to is the successor's to design.
- Requirement 1's per-side fingerprint is ADR-0001's and is what stops `list-projects` and `lookup-project` from dialling Ollama. Requirements 2, 3 and 5 belong to ADR-0002 and the successor set.
- **Disagreement with the owner requirement's framing:** the stated risk is silent staleness. The sharper defect is a swallowed exception -- `search_for_display` catching every `ValueError` (`src/code_indexer/core.py:913-914`) means a total backend failure and an empty result are the same output with the same exit code. No staleness marker would catch that, and a record that fixes only staleness would leave it. Section 5 exists because of this, not as a tidiness item.
- **Disagreement with the audit's cost framing:** 67 s is not the number to design against. The unscoped default at 804 s is, because it is the call an agent makes, and it is bounded by registered-project count -- a quantity the tool does not control. Gate F5 says the same; this record draws the consequence.

## Alternatives considered, and why rejected

- **Invert the flag: keep implicit re-indexing and require `--no-refresh` to stop it.** The default is what agents invoke without thinking; a default that writes is a default that corrupts a read-only intent.
- **Report staleness in text only.** The consumers that matter are agents parsing JSON; a notice only a human sees is the current failure mode with better wording.
- **Add a separate `refresh` command as the only trigger.** Duplicates scope, exclusion and locking of three pass commands to express one boolean.
- **Fix the 804 s with parallel fan-out and a larger timeout.** Parallelism preserves an unbounded bill and makes the failure arrive faster; the unboundedness is the defect.
- **Keep the 0/1/2 taxonomy and rely on the `ErrorType:` line.** Some agent runtimes surface only the numeric code. Aligning 5 with filekeeper's concurrency conflict costs nothing and buys cross-tool consistency.

## Consequences

- Existing agents lose implicit freshening. This is the behaviour change the owner must see: an answer that used to be silently repaired is now flagged, and the repair requires `--refresh`. The two permissions are not the same thing.
- `overview`'s 1,626-versus-1,358 defect does not disappear -- a stale index still answers from stale rows -- but it becomes visible on every such answer, which is the difference between a wrong answer and an honest one.
- The watcher becomes the only writer an agent gets without asking, and it serializes with `--refresh` by construction.
- Nine test modules exercise the probe path (`test_staleness_reporting`, `test_query_staleness`, `test_stale_safe_context`, `test_output_contract` and their neighbours, audit section 5); their assertions move from "the read repaired the index" to "the read reported it". `--skip-stale-check` becoming inert changes the flag contract test.
- No schema change and no reindex. The cost of this record is behaviour and tests, not data.
- **MCP consequence, named and not decided here:** once this contract holds, `semantic_search` and every other read tool's `readOnlyHint` becomes true with `idempotentHint` true, and the only non-read tools are the four lifecycle ones. Two things follow for the later MCP record: the relay must stop merging stdout into stderr and stop discarding the exit code before any annotation is true of what a client actually sees, and exit codes 3, 4 and 5 stay invisible through the relay until it does.

## What this record does not decide

- Whether the MCP server is kept, rewritten or removed, and what its annotations are published as. The consequence above is named so that record cannot miss it.
- The plan store, preview output and validation protocol behind `show`/`validate`/`apply`.
- The filekeeper apply path and its multi-file recovery semantics (requirement 3).
- The watcher's default scope and debounce policy.
- Which members each `--json` shape carries beyond the staleness block and the `ErrorType` line. Section 7 decides which commands have a mode; the plan commands' shapes are ADR-0004's.
