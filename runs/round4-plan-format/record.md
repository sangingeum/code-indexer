# Round 4, leg 1 — the plan format: content-addressed plans, locators, preconditions, preview

Card T-000061. Base `fb932ff`. Tree `/home/keum/dev/athena/code-indexer`.
No apply path in this leg: nothing here writes a source file.

## Commits

| SHA | slice |
|---|---|
| `5614118` | the document type, canonical bytes, `plan_id`, the locator, the coordinate rules and the refusal matrix (slices 1 and 2 — one module, one test file, both green here) |
| `ece1302` | the three prepare commands and the reserved rename verb |
| `f434a27` | `plan-validate` |
| `0b85e9b` | `plan-show` and `--diff` |
| `d506352` | the insertion coordinate fix the refusal capture found, and the closed command roster |
| the docs commit | SKILL-CLI.md, json-schema.md, CHANGELOG.md, `.gitignore`, this record |

Every commit is signed (`git log -1 --format='%h %G?'` → `G`) and every
intermediate tree is green: the tests for a slice land in its commit, and the
full `-W error` run passed at `d506352` (770 passed).

## What was built

`src/code_indexer/plans.py` owns the format; the six commands are thin
typer seams in `src/code_indexer/cli.py`.

- **The document** (ADR-0004 §1). Canonical bytes are UTF-8, sorted keys, no
  insignificant whitespace, no trailing newline, `\n` never literal.
  `plan_id = sha256:<hex>` over those bytes with the member absent, then
  written in; the file is `<plan-dir>/<hex>.json` with the default plan dir
  `<project>/.code-indexer/plans/`. No database and no registry: a `<ref>` is
  a path or `sha256:<hex>`, and a path whose content hash differs from the
  requested hash is refused (`PlanHashMismatch`). `--print` emits the
  canonical document alone and writes nothing at all — not the file, not the
  directory. `plan-show` prints the document as read, unknown members
  included, and never rewrites it.
- **The locator** (§2). `path`, `language`, `qualified_name`, `symbol_type`,
  `decl_hash` plus `range{start_line, end_line, start_byte, end_byte}` as
  evidence; no `symbol_id` anywhere. `files[]` carries `{path,
  expected_hash, newline, language}`. All four invalidation cases are
  detectable and each has a capture below, including the two-overloads
  collision, which refuses with both candidates listed.
- **`plan-replace-body`** replaces the body node's whole line range (the node
  the chunker's own signature split locates). A body whose first token does
  not begin a line is refused, not widened.
- **`plan-insert-symbol`** inserts before or after a declaration, or at the
  end of the file (`position: end`, no line number). A contiguous comment
  block immediately above the declaration moves the "before" point to the top
  of the block, and the move is recorded in the edit's `note`.
- **`plan-delete-declaration`** deletes the declaration's whole line range.
- **`plan-rename-symbol`** is reserved and refused (exit 2) with the reason.
- **`plan-validate`** prints per-check verdicts and writes nothing. Check
  order: plan hash, schema, requests, paths, file bytes, file hash and
  newline declaration, replacement text, node re-derivation, line and byte
  cross-check, region hashes, overlap and ordering, syntax verdicts.
- **`plan-show [--diff]`** prints the plan, and with `--diff` every affected
  file plus a unified diff computed from the plan plus the current bytes.
  The file preconditions run first, so a stale plan is refused rather than
drawn against bytes it never saw.

## Coordinates, encoding and ordering, as rules with tests

- Bytes are counted after a BOM; offset 0 is the first byte after it. The BOM
  is preserved verbatim and lies inside no range.
- Lines are 1-based and inclusive, and a terminator is not part of its line.
- `newline` is declared per file (`lf` / `crlf`) and must match what is
  observed; mixed terminators are refused rather than guessed.
- Replacement text is always `\n`. A plan that encodes a carriage return is
  refused. `--body-file` input is translated at the input boundary (a CRLF
  body file yields an LF block), which is documented and tested.
- A final line without a terminator is legal and its `end_byte` is the end of
  the body; insertion after it is `position: end`.
- No columns exist. Lines and byte offsets are both recorded and
  cross-checked, so a corrupted offset fails arithmetic instead of being
  trusted (test: `start_byte += 1` is refused with the line's real start).
- Order is a property of the format, in `plans.order_key`: within a file
  descending `start_line`, across files ascending path, a range edit before an
  insertion at the same start line. Adjacency is legal; overlap and two
  insertions at one point are refused.

## The refusal matrix, as it actually printed

Captured by `runs/round4-plan-format/refusals.py` into `refusals.txt`; each
case is a real CLI invocation in a throwaway registered project.

<!-- matrix -->

```
| case | exit | line it printed |
|---|---|---|
| PathEscapesProject (absolute) | 2 | PathEscapesProject: plan path '/etc/passwd' is absolute; a plan path is project-relative with '/' separators |
| PathEscapesProject (..) | 2 | PathEscapesProject: plan path '../outside.py' contains '..' |
| NotUtf8 | 1 | NotUtf8: bad.py: byte 5 of the body is not UTF-8 (invalid continuation byte); a plan never lossy-decodes |
| MixedNewlines | 1 | MixedNewlines: mixed.py mixes line terminators (b'\n', b'\r\n'); a file with mixed terminators is refused rather than guessed at |
| CarriageReturnInPlan | 2 | CarriageReturnInPlan: replacement text carries a carriage return; a plan speaks LF only and the adapter translates to the file's convention |
| SubLineEditUnsupported | 2 | SubLineEditUnsupported: inline.py:1: the body begins mid-line, and a plan edit is line-granular |
| rename_symbol reserved | 2 | ArgumentError: rename_symbol is reserved in the plan format and refused in phase 1: references are textual, so nothing here could verify a rename; no plan is written |
| Plan not found | 1 | NotFoundError: no plan sha256:9999... in .../.code-indexer/plans: a plan is its file, so a missing file is a missing plan |
| NewlineMismatch | 1 | NewlineMismatch: mod.py: the file uses lf terminators but the plan declares crlf |
| OverlappingEdits | 2 | OverlappingEdits: mod.py: lines 2-2 and 1-2 overlap; an overlap is refused, never ordered |
| AmbiguousOrder | 2 | AmbiguousOrder: mod.py: two insertions at the boundary before line 4 -- no rule can honour both, so the order is refused rather than invented |
| SemanticProviderUnavailable | 3 | SemanticProviderUnavailable: the plan sets validation.semantic_required, and no semantic provider exists in phase 1: a plan may not buy a stronger promise than the tool can keep |
| LocatorMismatch | 5 | LocatorMismatch: mod.py: 'alpha' (function) is present but its declaration hash differs: planned sha256:1111..., observed sha256:12fe... |
| AmbiguousNode | 2 | AmbiguousNode: dup.py:dup (function) matches 2 nodes with the same name, kind and declaration hash, so the locator cannot say which one - the plan is refused rather than resolved: dup.py:1-2 (decl_hash=...); dup.py:5-6 (decl_hash=...) |
| PlanStale (a stale file) | 5 | PlanStale: mod.py: the file's hash is sha256:afea... but the plan was prepared against sha256:854d...; the plan is stale and is refused whole |
```

`refusals.txt` holds the untruncated lines. Exit codes reuse ADR-0003's
taxonomy; no number was added.

## The acceptance lines, run

```
code-indexer plan-replace-body alpha --project <fixture> --file sample.py \
  --text '    return 41' --print | jq -r .plan_id
  → sha256:5a7d03156b252147ce85c15c9697756dc32c2e5e5209f26bbee263520260f9a6
sha256 of the same document's bytes with plan_id removed
  → sha256:5a7d03156b252147ce85c15c9697756dc32c2e5e5209f26bbee263520260f9a6
MATCH; bytes on stdout 1032; plan dir written by --print: no

code-indexer plan-validate <ref> --project <fixture>   → passed=true, three times
code-indexer plan-show <ref> --diff          → exit 0, two times

stale file (one body changed after the plan was written):
  code-indexer plan-validate <ref> → exit 5
  PlanStale: sample.py: the file's hash is sha256:7ff5... but the plan was
  prepared against sha256:854d...; the plan is stale and is refused whole
```

## The write-free proof

`runs/round4-plan-format/proof.sh` → `proof.txt`. The standing watcher (pid
355623) was frozen with SIGSTOP for the measurement and SIGCONT-ed on exit;
it is running.

- Fixture project: sha256 of every file in the plan dir before and after 3
  `plan-validate` and 2 `plan-show --diff` calls — identical; the touched
  source file's hash unchanged.
- Real checkout (`--project code-indexer`, plan on
  `src/code_indexer/identity.py`): sha256 of the plan dir, the touched file
  and all three manifest files (`manifest.db`, `-wal`, `-shm`) before and
after 3 validates and 1 show — **identical**:

```
759448cc...  /home/keum/.code-indexer/code-indexer/manifest.db
93b3c026...  .code-indexer/plans/4c79c546...json
b0b6fba8...  src/code_indexer/identity.py
e3b0c442...  /home/keum/.code-indexer/code-indexer/manifest.db-wal
fd4c9fda...  /home/keum/.code-indexer/code-indexer/manifest.db-shm
UNCHANGED: plan dir, the touched file and every manifest file
```

One real plan (`sha256:4c79c546...`) is left under `.code-indexer/plans/` as
evidence; the directory is now gitignored and is never committed.

## Validation honesty

No output of any plan command says "valid", "compiles" or "type-checks"
except the caveat that denies them ("structural checks only … nothing here
claims the result compiles or type-checks"). The syntax verdict is
`ts_chunker.parse_file`'s own answer — `parsed` / `parse_error` /
`parse_unavailable` — and `parse_unavailable` is reported as a failed claim,
not a pass; a plan whose file no longer parses cannot have its locator
re-derived and is refused. Tests assert the banned vocabulary and the verdict
source.

## Gates

```
uv run pytest tests/test_plan_format.py -q          → 63 passed
uv run pytest tests/ -q -W error                    → 770 passed, 2 deselected
uvx ruff check src tests scripts                    → All checks passed!
uv run python scripts/publishing_hygiene_lint.py    → scanned=683 violations=0
```

## What is left for the apply leg

- **`plan-apply <ref> [--dry-run]`** and its receipt, per ADR-0005. The format
  already carries what that leg consumes: `files[].expected_hash`, per-edit
  `expected_old_text_hash` and `expected_old_text` (omitted above 8 KiB),
  ordered edits, and `plan_id` as the journal task id. `plans.ordered_edits`
  and `plans.check_edit_order` are the order and the overlap rule, already
  known to both prepare and validate, so the apply leg calls them rather than
  restating them.
- **The syntax rung after apply** is the same `parse_file` verdict, printed
  next to the caveat; the counts already exist in `index-status`.
- **No partial honour**: a precondition that fails at apply is
  `PlanStale`/exit 5 and the plan is refused whole. `validate` is evidence,
  not a promise; the apply leg must re-run the same precondition code.
- **Not built, deliberately**: `plan-apply`, the keeper mutation path, the
  receipt, `rename_symbol`, multi-file semantic refactorings, and MCP
  exposure of any plan command.

## Deviations and notes

- Slices 1 and 2 landed as one commit (`5614118`): the format and its locator
  are one module and one test file, and the slice-by-slice bar is "each
  commit self-consistent and green", which it is. Slices 3–6 follow
  individually.
- The refusal capture found a real defect (an insertion has no `end_line`, and
  the cross-check read it unconditionally, so a real `AmbiguousOrder` came out
  as `InternalError`). Fixed in `d506352` with two regression tests.
- `plan-validate` and `plan-show` accept the inert `--skip-stale-check` and no
  `--refresh`: they answer from a plan file and the live sources, so there is
  no index answer behind them to refresh. SKILL-CLI.md says so and
  `tests/test_read_contract.py` names the exemption.
- The `runs/round4-plan-format/fixture-refusals/` directory is a throwaway
  project left behind by the capture; its registration is removed at the end
  of the script.
