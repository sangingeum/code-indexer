# Round 1, leg 1 — the read contract, landed

Author: the Python developer leg. Governing record: ADR-0003 (all seven
sections). This is the leg record for the behaviour change, its measurements
and what it carries open.

Point-in-time counts are marked as such. Every number below names the command
that produced it and quotes what it printed.

## What changed, by section

### 1. Two command classes

New `src/code_indexer/command_classes.py` holds the closed roster of all 27
registered commands, alias spellings included: six write-only commands
(`add-project`, `index-more`, `remove-project`, `reindex-project`, `watch`,
`unwatch`), one conditional writer (`sweep-orphans`, write only with
`--apply`), and every other command as a read. A command outside the roster
is a defect, not a third class; `tests/test_read_contract.py::test_roster_is_
closed_and_every_command_classified` compares the roster with the typer
registry in both directions and pins the count at 27.

`Core.maybe_refresh` is gone. The implicit `STALE_TTL` pass no longer exists
on any read path; `Core.refresh(entry)` is the explicit write and raises
`LockedError` on a flock miss. `--skip-stale-check` is still accepted by every
command and is inert; `--fresh` keeps its live-read meaning only on
`get-code-context` and is accepted-and-inert on the other six commands that
still expose it.

### 2. Staleness: detect, never repair

New `src/code_indexer/staleness.py` (verdicts, reason cap, block, exact text
line) and `Core.staleness(entry)` in `src/code_indexer/core.py`. Detection
reads the manifest's `files` rows and compares the indexed path set and each
file's stat identity against a walk with the scanner's own include/exclude
rules; content is hashed only where the stat triple changed (the scanner's
existing fast path). `src/code_indexer/scanner.py` gained `ScanBudget` /
`ScanBudgetExceeded`, so the walk stops at 5 s / 200,000 entries and the
verdict becomes `unknown` rather than blocking. No service is contacted.

### 3. How it is reported

Additive JSON, `schema` stays 1: `stale`, `stale_known`, `stale_reasons`
(capped at five), `stale_reason_count`, `index_revision`, `last_indexed`.
`index_revision` is stamped by each committed pass (`_next_revision`,
`src/code_indexer/indexer.py`) and falls back to `last_indexed` on manifests
written before this leg. Text mode prints exactly one stderr line in the
record's shape when stale, nothing when fresh, and one "the check did not
run" line when unknown.

### 4. Refresh is explicit only

`--refresh` is accepted by every read command. It takes the per-project
flock, runs one incremental pass and answers from the result; a held lock is
`LockedError: indexing in progress`, exit 5, stdout empty. `doctor` refreshes
each registered project before its checks; `eval` refreshes the evaluated
project; `eval-compare` accepts the flag and does nothing with it because it
reads report files, not an index (documented as such). No `refresh` command
was added.

### 5. Exit-code taxonomy

`src/code_indexer/errors.py`: 0 success, 1 other runtime failure, 2 usage,
3 backend/extension unavailable (`BackendError`), 4 rebuild required (new
`RebuildRequiredError`), 5 lock/concurrency conflict (`LockedError`).
`classify` maps a fingerprint mismatch (a message naming `reindex-project`)
to 4 and an absent model / unreachable service to 3. `search_for_display` no
longer catches exceptions and returns them as the answer: failures propagate
to the CLI seam, which prints one `ErrorType: message` line on stderr and
exits with the code.

### 6. Bounded cost

`Core.search` resolves ONE project by default (containing project, the rule
the read commands already used), fans out only with `all_projects` /
`--all-projects`, computes the query vector once with
`Embedder.embed_query_once` (one attempt, 10 s timeout, no retry chain) and
reuses it for every project, and stops the fan-out at a 30 s wall, adding the
remaining projects to `skipped` with `reason=budget`. Results carry
`partial: bool`.

### 7. `--json` on five read commands

`list-projects`, `lookup-project`, `index-status`, `find-definition` and
`find-references` gained `--json` (schema 1, the fields the text renderer
prints, `ErrorType` in the failure object, and the section 3 block). The
JSON modes that already existed (`semantic-search`, `skeleton`, `outline`,
`find-symbol`, `overview`, `changed-symbols`, `find-callers`,
`find-callees`, `deps`, `get-code-context`, `sweep-orphans` dry run) also
carry the block. `watch`, `unwatch`, `eval`, `eval-compare` stay text-only.

## The two headline defects: before and after

### Failed search: exit code and stream

**Before** (the defect the record fixes; from ADR-0003's context section,
which cites audit section 4 and gate F5): `Core.search` raised when every
project failed, `search_for_display` caught the `ValueError` and returned the
message as the answer, and the CLI printed
`error: search failed on every project (...)` on **stdout** and exited 0 —
indistinguishable from an empty result. That is a reading of the base commit,
not a fresh run of it; the base tree is no longer in the working directory.

**After**, run once from the repository root with both services pointed at a
closed port:

```
QDRANT_URL=http://127.0.0.1:1 OLLAMA_URL=http://127.0.0.1:1 \
  code-indexer semantic-search "anything"
```

- exit code: `3` (captured `search-scoped.code`)
- stdout: `0` bytes (`search-scoped.out`)
- stderr, one line:
  `BackendError: embedding service unavailable: Failed to connect to Ollama. Please check that Ollama is downloaded, running and accessible. https://ollama.com/download`
- wall time (`time`, `search-scoped.time`): `real 0m0.996s`

The empty-result side is pinned by
`tests/test_exit_codes.py::test_empty_result_is_code_0_on_stdout`: exit 0,
`no results` on stdout, no `BackendError` on stderr. The two now differ in
both code and stream.

### Unscoped query cost

**Before** (ADR-0003's context section, quoting audit section 4 and gate F5):
the unscoped default took **804.2 s over twelve registered projects**,
because the query path iterated every project and retried the embed call per
project.

**After**, the same fan-out against the same closed port:

```
QDRANT_URL=http://127.0.0.1:1 OLLAMA_URL=http://127.0.0.1:1 \
  code-indexer semantic-search "anything" --all-projects
```

- exit code: `3` (`search-all.code`)
- stdout: `0` bytes (`search-all.out`)
- stderr: the same one `BackendError:` line
- wall time (`search-all.time`): `real 0m1.722s`

The three factors behind 804 s are removed rather than tuned: project count
is one by default, the retry chain is not on the query path, and the wall is
30 s. The per-invocation budget is asserted structurally by
`tests/test_read_contract.py` and the exit-code module; the wall's
budget path is exercised through `Core.search(budget_s=...)` semantics rather
than a 30 s sleep.

## Acceptance commands (actual output)

Run at the tip of this leg (point-in-time, 2026-11-07):

| Command | Result |
|---|---|
| `uv run pytest tests/ -q -W error` | `602 passed, 2 deselected` at the code commit; see the final run below for the docs commit |
| dead-backend `semantic-search` | exit 3, stdout empty, one `BackendError:` line on stderr, 0.996 s |
| dead-backend `semantic-search --all-projects` | exit 3, 1.722 s |
| `code-indexer overview` | exit 0, answer on stdout, one stale line on stderr |
| `code-indexer find-symbol Core --json \| jq .stale_known` | `true` |
| `uv run pytest tests/test_read_contract.py tests/test_exit_codes.py -q` | `40 passed` |

### The `overview` stale line is point-in-time, not a clean measurement

`code-indexer overview` printed:

```
Warning: index is stale (36 files changed since 2026-11-06T16:23:29Z: 15 modified, 21 added); answers describe the index, not the tree. Refresh: code-indexer reindex-project <path>
```

The 21 "added" files are partly this leg's own capture files, which live
inside the indexed tree (`runs/round1-leg1-read-contract/`): each measurement
written there changes the next measurement's count (a before/after pair read
34 then 37 for that reason). Treat the number as evidence that the verdict is
produced and line-shaped, not as a count of the tree's edits.

The "writes nothing" property is not measured by that diff; it is asserted by
tests that snapshot the manifest's logical content (pass stamp, revision,
file rows with content hashes, symbol rows) before and after each read and
compare it, and by recording embedder/store call counts on stub seams
(`tests/test_read_contract.py`).

## Test modules touched

- Added: `tests/test_read_contract.py` (roster closure, no-write/no-dial
  reads, three verdicts and the exact stale line, `--refresh` locking with a
  real cross-process flock, JSON block).
- Added: `tests/test_exit_codes.py` (taxonomy, backend failure vs empty
  result in code and stream, rebuild 4, lock 5, usage 2, JSON `ErrorType`).
- Rewritten: `tests/test_query_staleness.py` — from "the read repaired the
  index" to "the read reported it", plus explicit refresh and a held-flock
  test.
- Moved to reporting semantics: `tests/test_concurrency.py`,
  `tests/test_output_contract.py`, `tests/test_search_isolation.py`,
  `tests/test_stale_safe_context.py`, `tests/test_evalharness.py`,
  `tests/test_subcommands.py`, `tests/test_graph.py`,
  `tests/test_result_shaping.py`, `tests/test_docs.py` (the last one gained
  five assertions for the new documented behaviour; before that edit it was
  green only because the docs still described the old behaviour — a false
  green in exactly the place this leg was meant to fix).

## Docs

- `SKILL-CLI.md`: read/write classification; the staleness block and the
  record's exact stderr line; `--refresh` named as the only read-to-write
  trigger; `--skip-stale-check` documented as inert; `--all-projects` and the
  30 s budget with `partial`; the exit-code table; `--json` on the five
  commands (and the block on every other JSON read answer).
- `README.md`: the staleness/repair paragraph and the exit codes in the
  indexing section. The opening paragraph was not touched (round 5 owns it).
  Claims are scoped: `--refresh` is described as the read-to-write trigger,
  and the `eval-compare` exception is stated where the flag is listed.
- `docs/json-schema.md`: the shared staleness block, the five new shapes, the
  0-5 exit table and the `--json` failure object.
- `CHANGELOG.md`: one `[Unreleased] — read contract` section naming the
  visible breaking changes (reads stop repairing; a failed query stops
  exiting 0).

## Carried open

- **The MCP surface is not touched, and its prose is now false.**
  `src/code_indexer/server.py` still annotates the read tools with
  `readOnlyHint=False` and explains that a stale index triggers
  `Core.maybe_refresh`. That function no longer exists and reads no longer
  write. The relay's stream/exit-code behaviour is leg 2's (a separate
  card); this leg deliberately did not edit `server.py`, so the false prose
  is carried open and owned there.
- `eval-compare` accepts `--refresh` and does nothing with it, because it
  reads report files rather than an index. This is documented rather than
  hidden; if the flag surface should exclude non-index reads, that is a
  follow-up decision, not a silent omission.
- `list-projects`, `lookup-project`, `index-status` and `doctor` still probe
  the running configuration for the fingerprint verdict (one short attempt,
  cached per process). ADR-0003 section 1 forbids dialling a service **to
  reach a staleness verdict**; the staleness path itself dials nothing. The
  per-side fingerprint that removes the probe is ADR-0001's and lands later.
- `get-code-context` text mode computes the verdict twice when its file-level
  warning did not fire (once for the block, once for the line). Correct, but
  a second walk; a small follow-up, not a contract gap.
- The multi-project JSON answer keeps the aggregate block plus a `staleness`
  array of per-project blocks. The single-project shape is the record's table
  verbatim; the fan-out aggregate is documented in `docs/json-schema.md`.
