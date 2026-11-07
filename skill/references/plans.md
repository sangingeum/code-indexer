# plans -- changing code through a plan

Governed by: ADR-0004 (the plan and apply lifecycle); ADR-0005 (filekeeper
apply and recovery); ADR-0003 section 1 (the plan commands' classes) and
section 5 (exit codes); ADR-0008 (no rename); ADR-0009 section 2 (this file's
contents and budget). Open this reference when the task is to change code
through a plan.

A change is prepared as a plan: an immutable JSON document named by the
sha256 of its own canonical bytes. There is no plan store to keep in sync --
a plan reference is a path or `sha256:<hex>`, resolved in the plan directory
(`<project>/.code-indexer/plans/` unless `--plan-dir` says otherwise), and
deleting the file deletes the plan. A path reference whose content hash
differs from the requested hash is refused.

A write command changes the manifest, the vector store or the sources; a
read command never does. The three writers are **write** commands whose only
write is one plan document; under `--print` they write nothing at all. `plan-validate` and
`plan-show` are **reads** and write nothing, always. `plan-apply` is a
the only command in the tool that writes a source file.

## Commands

<!-- generated:commands-plans:begin -->
### `plan-replace-body`

- **Class**: write
- **Arguments**: `symbol` (required)
- **Flags**: `--project` <value>, `--file` <value>, `--kind` <value>, `--container` <value>, `--text` <value>, `--body-file` <value>, `--note` <value>, `--plan-dir` <value>, `--print`, `--json`, `--skip-stale-check`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `plan-insert-symbol`

- **Class**: write
- **Arguments**: `symbol` (required)
- **Flags**: `--project` <value>, `--position` <value> (default `after`), `--file` <value>, `--kind` <value>, `--container` <value>, `--text` <value>, `--body-file` <value>, `--note` <value>, `--plan-dir` <value>, `--print`, `--json`, `--skip-stale-check`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `plan-delete-declaration`

- **Class**: write
- **Arguments**: `symbol` (required)
- **Flags**: `--project` <value>, `--file` <value>, `--kind` <value>, `--container` <value>, `--plan-dir` <value>, `--print`, `--json`, `--skip-stale-check`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `plan-validate`

- **Class**: read
- **Arguments**: `ref` (required)
- **Flags**: `--project` <value>, `--plan-dir` <value>, `--json`, `--skip-stale-check`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `plan-show`

- **Class**: read
- **Arguments**: `ref` (required)
- **Flags**: `--project` <value>, `--plan-dir` <value>, `--diff`, `--json`, `--skip-stale-check`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).

### `plan-apply`

- **Class**: write
- **Arguments**: `ref` (required)
- **Flags**: `--project` <value>, `--plan-dir` <value>, `--dry-run`, `--json`, `--skip-stale-check`, `--refresh`
- **JSON**: `--json` accepted; envelope and recipes in [machine-output.md](machine-output.md).
<!-- generated:commands-plans:end -->

## Preparing

```bash
code-indexer plan-replace-body Core.search --project P --file src/core.py --text '    return 41'
code-indexer plan-insert-symbol Core.search --project P --file src/core.py \
    --position after --body-file helper.py
code-indexer plan-delete-declaration Legacy --project P --file src/core.py
```

- **Discovery is the symbol query.** `--file` searches that file's live
  declarations, so preparing never needs an index; without it the manifest's
  symbol index finds the file. A name with two definitions is refused with
  every candidate listed (`ArgumentError`, exit 2) -- never resolved to the
  row that sorted first.
- **One write per prepare**: the plan file. `--print` emits the canonical
  document on stdout and writes nothing at all -- not the file, not the
  directory. `... --print | jq -r .plan_id` equals the sha256 of the
  document's own bytes with `plan_id` removed.
- `--position` is `before`, `after` or `end`: a declaration boundary, or the
  end of the file for `end`.

## The plan document

| member | meaning |
|---|---|
| `plan_id` | `sha256:<hex>` of the canonical bytes with this member absent |
| `schema` | the format version |
| `operation` | `replace_body`, `insert_symbol`, `delete_declaration`; `rename_symbol` is reserved and refused |
| `files[]` | one record per touched file: path, expected hash, newline convention, the edits, and the observed node as evidence |
| `edits[]` | per file: the locator (`path`, `language`, `qualified_name`, `symbol_type`, `decl_hash`), 1-based inclusive `range`, byte offsets, region hash, replacement text |

- **The locator is structural**: there is no `symbol_id` in a plan -- it is
  revision-scoped. Two nodes in one file with the same name, kind and
  declaration hash collide by construction and are refused with both listed
  (`AmbiguousNode`, exit 2).
- **Coordinates** are 1-based inclusive lines plus byte offsets into the
  stored bytes; offset 0 is the first byte after any BOM, and a terminator is
  not part of its line. Validation cross-checks the two, so a corrupted
  offset fails arithmetic instead of being trusted. Columns do not exist.
- **Replacement text always uses `\n`**; the apply adapter translates to the
  file's declared convention. A plan encoding a carriage return is refused;
  `--body-file` input is translated at the input boundary.
- **One path, one record.** A duplicated path in `files[]` is
  `PlanFormatError` (exit 2): the duplicate would be applied twice and
  reported once.
- **Ordering is part of the format**: within a file descending start line,
  across files ascending path. Adjacent edits are legal; overlapping edits
  are `OverlappingEdits` (exit 2), two insertions at one point are
  `AmbiguousOrder` (exit 2), and an insertion after the last line is
  `position: end` with no line number.

## Validating and previewing

```bash
code-indexer plan-validate sha256:<hex> --project P --json
code-indexer plan-show sha256:<hex> --project P --diff
```

`plan-validate` writes nothing and reports, it does not claim: plan hash,
schema, paths, file hashes and newlines, node re-derivation, the line/byte
cross-check, region hashes, overlap and ordering, and the parser's verdict
per file (`parsed` / `parse_error` / `parse_unavailable`). Nothing in its
output says the result is valid, compiles or type-checks.

`plan-show --diff` writes nothing and prints every affected file with a
unified diff computed from the plan plus the current bytes. Without `--diff`
it prints the document as read, unknown members included: reading never
rewrites a plan, because a rewrite changes its hash. Either way the plan's
`plan_id` is recomputed and its schema checked first, so a tampered plan is
refused here exactly as at validate and apply (`PlanHashMismatch` /
`PlanFormatError`, exit 2), never drawn as a diff.

A stale file refuses before anything else: `files[].expected_hash` mismatch
is `PlanStale`, exit 5, naming the file and both hashes.

## Applying

```bash
code-indexer plan-apply sha256:<hex> --project P            # apply, then report
code-indexer plan-apply sha256:<hex> --project P --dry-run  # the same report, nothing written
```

`plan-apply` needs keeper (the filekeeper CLI) on `PATH`; an absent keeper is
`KeeperUnavailable`, exit 3, with the install line, and there is no fallback
path. Once per invocation it reads `keeper file replace-lines --help` and
requires the flags it is about to pass (`--expected-hash`,
`--expected-content`, `--dry-run`); a build that dropped one is
`KeeperCapabilityMissing`, exit 3, naming the flag.

What a caller may assume, and nothing more:

1. **Nothing is written before every precondition passes.** A read-only
   pre-pass -- the same code `plan-validate` runs, so the two cannot disagree
   about what stale means -- checks every file hash, re-derives each targeted
   node and reproduces every edit's region. One failure refuses the whole
   plan (`PlanStale`, exit 5) with zero mutations.
2. **Each file's bytes move from old to new through keeper's hash guard.**
   Each edit is one keeper line-range mutation carrying the hash observed
   immediately before it; a concurrent change is refused by keeper and the
   file keeps its bytes. Files apply in ascending path order and, within a
   file, start lines descending.
3. **The operations that ran can be undone in reverse order.** Every
   operation id is in the report and the receipt, in application order.
4. **No promise across files.** A failure stops the run: no further file is
   attempted and no compensating write is issued. The report says which files
   were `applied`, `would_apply`, `partially_applied` (with the landed edits'
   operation ids), `failed` or `not_attempted`, and names the keeper code and
   message. `partial` is true whenever an operation ran, so a half-written
   file is never reported as an unchanged one.

Exit codes: a pre-pass refusal is 5 `PlanStale` and is not a partial apply; a
mid-loop failure is 1 `PartialApply` with the report on stdout once anything
was written, and 1 `KeeperFailure` when nothing was; keeper 5 maps to 5.
`plan-apply` refuses `--skip-stale-check` (`ArgumentError`, exit 2): a plan's
staleness is not skippable when a source file is about to be written.

**Recovery** is the ordered operation id list, undone in reverse:

```bash
keeper history undo op_...        # last operation first, as the report prints it
keeper history list --task-id sha256:<hex>
```

Keeper refuses an undo with exit 12 when an affected path moved after the
operation; that ends recovery and is reported, never retried. Every call
carries `--task-id <plan_id>`, so `keeper history list --task-id` reconstructs
the whole application from keeper's own journal. A JSON receipt is written to
`<project>/.code-indexer/receipts/<plan-hash>-<timestamp>.json` and its path
printed -- losing the receipt loses convenience, not evidence.

**Post-apply** reparses every modified file and reports the parser's verdict
per file: `parsed`, `parse_error` (with the offending lines) or
`parse_unavailable` when the language has no grammar here. This is a parse
verdict, never a compile verdict, and it is read from the same parser rung
`plan-validate` reports. The reparse writes nothing; `--refresh` runs one
incremental index pass after the report, and the report says which happened
(`refresh: ran` / `refresh: skipped`).
