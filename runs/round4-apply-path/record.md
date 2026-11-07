# Round 4, leg 2 — the apply path: one keeper mutation per edit, a report, a way back

Card id withheld (an authored record is hygiene-governed; the dispatch brief carries it). Base `4afe05b`. Tree `<project-root>`.
This leg consumes the plan format that landed last leg and adds the only command that writes a source file.

## Commits

| SHA | slice |
|---|---|
| `e3cf8ee` | the filekeeper adapter, its flag probe and exit mapping, the shared pre-pass, and the stub keeper the tests run against |
| `53a8943` | the apply loop, the per-file report, partial failure and the recovery list, `--dry-run`, the receipt and the post-apply parse verdicts |
| the commit that carries this record | SKILL-CLI.md, json-schema.md, CHANGELOG.md and this record |

Every commit is signed (`git log -1 --format='%h %G?'` → `G`).

## What was built

`src/code_indexer/keeper.py` is the adapter; `src/code_indexer/apply.py` is the
run and its report; `plan-apply` in `src/code_indexer/cli.py` is a thin seam
over both.

- **The adapter** (ADR-0005 §6) starts `keeper` with an argv list — no shell,
  no `import filekeeper` — as
  `keeper --root <project root> --json --actor code-indexer --task-id <plan_id> file replace-lines …`.
  It reads the JSON envelope by name: `ok`, `operation_id`, `undo_available`,
  `before_hash`, `after_hash`, and on failure `error.code`, `error.message`,
  `error.details.path`. Human text is never parsed: stdout that is not one
  object carrying a boolean `ok` becomes an `UNREADABLE_JSON_ENVELOPE` (or
  `NO_JSON_ENVELOPE`) refusal, never a success inferred from an exit code.
  Values travel as `--flag=value`, and the path follows `--`, so a text block
  that begins with a dash or a file named `-dash.py` still reaches keeper.
- **The capability probe** (§6) runs `keeper file replace-lines --help` once
  per invocation and requires `--expected-hash`, `--expected-content` and
  `--dry-run`. A missing flag is `KeeperCapabilityMissing`, exit 3, naming the
  flag; an absent keeper is `KeeperUnavailable`, exit 3. Both messages carry
  the install line, and that line is the only place the 0.1.8 floor appears —
  the catch that runs is the flag probe, because a build that keeps the
  version and drops a guard has to be caught.
- **The pre-pass** (§3) is `plans.run_preconditions`, extracted from
  `plan-validate` in this leg so both commands run one sequence: plan hash,
  schema, requests, paths, file bytes, file hash and newline declaration,
  replacement text, node re-derivation, line/byte cross-check, region hashes,
  overlap and ordering. `plan-apply` calls it with no reporter and raises the
  first refusal as the taxonomy's own error; `plan-validate` calls it with a
  reporter and prints the same verdicts. The two commands cannot disagree
  about what stale means, because there is only one place that decides.
- **The apply loop** (§3) walks `plans.ordered_edits` — files ascending, start
  lines descending — and makes one keeper call per edit:
  `replace-lines --start/--end/--replacement`, `delete-lines --start/--end`,
  or `insert-lines --line … --position before` (`--position end` for an
  insertion after the last line). Each carries `--expected-hash` of the hash
  observed immediately before it — the file's own hash before the first edit,
  then the `after_hash` keeper returned — and `--expected-content` with the
  stored region text (terminator included, which is what keeper compares)
  whenever the plan carries `expected_old_text`. Replacement blocks are
  translated from the plan's LF to the file's declared convention.
- **The report** (§4): every file with `applied`, `would_apply`, `failed` or
  `not_attempted`; per edit its line range, its `operation_id` and, on
  failure, keeper's code and message; plus `plan_id`, `identity.verified`
  (`structural`, with `file_hash`/`decl_hash`/`region_hash`) and `partial`.
  Text and `--json`.
- **Recovery** (§4): `operations` holds the ids in application order and
  `undo` the same list as `keeper history undo <id>` commands, last first. No
  undo command ships — the list is the deliverable — so `apply.recover()` is
  the reference implementation those lines name: it undoes in reverse, stops
  at the first refusal and reports it with keeper's code, never retrying.
- **The receipt** (§4) is
  `<project-root>/.code-indexer/receipts/<plan-hash>-<utc-timestamp>.json`,
  written whenever something was applied, and its path is printed. Every
  keeper call also carries `--task-id <plan_id>`, so keeper's own journal
  finds the whole application without our cooperation.
- **Post-apply** (§7): every modified file is reparsed and reported `parsed`,
  `parse_error` with the offending lines, or `parse_unavailable`. The verdict
  is `ts_chunker.parse_tree`'s — the parser's `has_error` and `is_missing` —
  never the chunker's fallback, which cannot fail. The reparse writes nothing;
  `--refresh` runs one incremental pass afterwards and the report says which
  happened (`refresh: ran` / `refresh: skipped` / `refresh: failed (…)`).
- **Exit mapping** (§1, §6): a pre-pass refusal is exit 5 `PlanStale` with
  zero mutations and is not a partial apply; a mid-loop failure is exit 1
  `PartialApply` with the report on stdout once anything was written, exit 1
  `KeeperFailure` when nothing was; keeper 5 maps to 5 with the report still
  printed; keeper 12 is a recovery refusal reported by `recover()` and never
  retried. No exit number was added to ADR-0003's five.

## Two places where this leg chose the narrower reading, stated plainly

- **The pre-pass reads the files the way `plan-validate` does, not through
  `keeper file hash`.** ADR-0005 §3 offers either ("`keeper file hash` … or
  the equivalent read the validate path already uses"); a second hash source
  would be a second opinion about what stale means, which is exactly what
  sharing the code is meant to prevent. keeper's hash guard still runs on
  every mutation, so the observation is not lost — it is made by the tool that
  writes.
- **No `plan-apply --undo`.** The ADR grants no undo command, so none was
  added; the ordered list plus `keeper history undo` is the recovery surface,
  and `apply.recover()` exists as the reference the tests and the report's
  recovery lines point at.

## The suite, as it ran

```
$ uv run pytest tests/ -q -W error
811 passed, 2 deselected in 101.65s (0:01:41)

$ uvx ruff check src tests scripts
All checks passed!
```

The two new files are `tests/test_plan_apply.py` and
`tests/test_partial_apply.py`; `tests/test_keeper_adapter.py` covers the
boundary itself. The cases ADR-0005's Consequences names are all there:
pre-pass refusal with zero mutations; a mid-loop failure with the report and
the untouched later files (file 3 of 5 fails, 1 and 2 applied, 4 and 5
untouched); reapplication of a stale plan; recovery in reverse order; a
refused undo reported rather than retried; keeper absent; and a keeper whose
`--expected-hash` is missing from its help text.

**Why a stub keeper.** CI installs no filekeeper, and every apply test must
run in a tmp project rather than this checkout, so `tests/keeper_stub.py`
installs a small faithful `keeper` on `PATH`: it edits line ranges, honours
`--expected-hash` and `--expected-content`, answers `--dry-run`, journals and
undoes, and fails or refuses an undo on demand. It is a stub of the interface
this project depends on, not a stub of the behaviour under test — the whole
apply loop, the report, the mapping and the recovery path are this project's
code. The real keeper was exercised separately, below.

## Acceptance, run against the real keeper 0.1.8

The capture is `runs/round4-apply-path/capture.txt`, produced by
`capture.py` in a throwaway registered project under a temporary directory.
Host paths are masked as they are printed. Quoted here with no output line
dropped (the capture's own `#` comment lines are left out):

```
$ code-indexer add-project <project-root> --name captureproj
registered <project-root> (slug captureproj)
note: vectors=off — rows, symbols, references and chunks are indexed with no service; run reindex-project --vectors on later to embed them
exit 0

$ code-indexer plan-replace-body alpha --project captureproj --file sample.py --text '    return 41'
plan sha256:28a65477da347338b3ea528e6a45d8f804c2fa83604146274ca78a6c6fb4272d
path <project-root>/.code-indexer/plans/28a65477da347338b3ea528e6a45d8f804c2fa83604146274ca78a6c6fb4272d.json
replace_body sample.py:2-2, 1 line(s)
exit 0

$ code-indexer plan-apply sha256:28a65477da347338b3ea528e6a45d8f804c2fa83604146274ca78a6c6fb4272d --project captureproj
plan sha256:28a65477da347338b3ea528e6a45d8f804c2fa83604146274ca78a6c6fb4272d
identity structural (file_hash, decl_hash, region_hash)
- sample.py: applied
    replace_body lines 2-2: op_67e5999e4f9846c8
1 of 1 file(s) applied
partial: false
recovery (undo in reverse order):
  keeper history undo op_67e5999e4f9846c8
receipt <project-root>/.code-indexer/receipts/28a65477da347338b3ea528e6a45d8f804c2fa83604146274ca78a6c6fb4272d-20261107T000643Z.json
post-apply:
  sample.py parsed
parse verdicts only -- a file the parser accepts is not a file that compiles or type-checks, and a file whose language has no grammar here reports parse_unavailable, which is a failed claim rather than a pass
refresh: skipped
exit 0
```

keeper's own journal finds the application by the plan id carried as
`--task-id` — one operation, actor `code-indexer`, `before_hash`
`sha256:854d5c95…` and `after_hash` `sha256:4711ac4b…`, which is the file's
hash after the run.

A changed file, then the same plan again:

```
$ code-indexer plan-apply sha256:28a65477… --project captureproj
PlanStale: sample.py: the file's hash is sha256:afeac8146915d40d41892a026b965713f6de1eac0425beb76ed4f5bae096aaa4 but the plan was prepared against sha256:854d5c95c53c01074ee1a2b97babdc9cb23dd6d03098f6758a6667a27bdee848; the plan is stale and is refused whole
exit 5

the file is byte-identical after the refusal: True
keeper history list --task-id <plan id>: count 1 — the refusal recorded none
```

No keeper on `PATH`. The console script is addressed by its own absolute path
because `/usr/bin` does not hold it; what the run proves is that a missing
keeper is a refusal:

```
$ PATH=/usr/bin:/bin <repo-root>/.venv/bin/code-indexer plan-apply sha256:28a65477… --project captureproj
KeeperUnavailable: keeper is not on PATH, and this command applies a plan through filekeeper only: install filekeeper 0.1.8 or newer -- `uv tool install filekeeper-cli` (or `pipx install filekeeper-cli`) -- and put `keeper` on PATH
exit 3
```

The edited file never enters this checkout: the capture's project is a
throwaway under a temporary directory, and no test here targets the working
tree.

## Vocabulary

No output, docstring, help text or CHANGELOG line of this feature says
"atomic", "transaction" or "all-or-nothing", and
`tests/test_plan_apply.py::test_no_surface_claims_a_cross_file_guarantee`
asserts it against the run's output, the command's help text and the module
docstrings. The guarantee is stated as the four observables or not at all.

## What is left for the next leg

- The MCP surface for `plan-apply` is not decided here (ADR-0005 §"What this
  record does not decide"), and no tool was added.
- `apply-patch` remains unused and unguarded; nothing in this leg touched it.
- A refusal that lands on the *second* edit of one file after the first one
  was written is reported as `PartialApply` and undoable in reverse; it is not
  tested end to end, because a plan format that refuses overlapping edits
  leaves no fixture that reaches it without a keeper that fails mid-file on
  command. The order and the hash chain are tested.
