# ADR-0004: the edit plan and its lifecycle

- Status: accepted (owner ratification 2026-11-07)
- Date: 2026-11-07
- Deciders: owner (ratification), architecture review
- Supersedes / Superseded by: --

## Decision in one paragraph

An edit plan is an immutable JSON document named by the sha256 of its own canonical bytes, so `show`, `validate` and `apply` read one file and need no mutable plan store; it names its target with ADR-0002's line-free locator -- `(path, language, qualified name, kind, declaration hash)` -- plus one whole-file hash per touched file and one region hash per edit, and "revalidate" means reparse the file, re-derive the node, compare the declaration hash: structural identity, reported as `structural` and never claimed as semantic. Coordinates are 1-based inclusive lines and byte offsets into the file's stored bytes; columns, code points and UTF-16 positions do not exist in the phase-1 format, because the only mutation primitive available is line-granular and a coordinate nothing verifies is a defect generator. Overlapping edits are refused, never ordered. The first operation set is three line-aligned structural edits -- replace a body, insert at a declaration boundary, delete a declaration -- and a body that does not begin at a line start is refused rather than approximated. Validation is a ladder -- bytes, parse, compile -- and this record claims only the first two.

## Context

1. None of this exists. The audit's gap matrix records edit-plan generation, preview, structural edits and the provider interface as absent, each with a `rg` over `src/` that returns nothing (audit section 8). So there is no format to extend, only one to choose.
2. The identity question is settled but constraining. ADR-0002 section 1 defines the symbol key and section 2 states that the surrogate `id` is revision-scoped and may not be a plan locator. Whatever this record writes must be expressible in that key.
3. The read side must stay write-free. ADR-0003 section 1 says preview and validation write nothing, so a plan store with mutable ids would put a write inside `show` and a lock inside `validate`. Content addressing is the only plan identity that survives that rule without an exception.
4. The mutation primitive constrains the range model. Measured on this host: `keeper file replace-lines|insert-lines|delete-lines` take 1-based inclusive line numbers, reject out-of-range references instead of clamping, and preserve the file's newline convention and byte-order mark (`keeper file --help`; the tool's `README.md`, "Line numbers" and "Line ranges"; and `keeper file replace-lines --help`, whose option list ends `[--expected-content TEXT] [--expected-hash HASH] [--dry-run]`). A plan whose coordinates cannot be expressed as a line range cannot be applied by the primitive ADR-0005 selects.
5. The proposal leaves the ambiguity to us. Section 6.4 requires that "the implementation must define precise coordinate conventions, encoding, newline handling, and range semantics", shows a range with `start_column`/`end_column` and no unit, and section 6.5 defers the CLI shape. Left as is, every plan is a guess about what line 3 column 7 means.

## Decision

### 1. The plan document

- **Canonical bytes**: UTF-8, object keys sorted, no insignificant whitespace, no trailing newline, and `\n` never literal. These are the bytes the hash covers.
- **Identity**: `plan_id` is `sha256:<hex>` over those canonical bytes computed with the `plan_id` member absent, then written into the document. The file is stored as `<plan-dir>/<hex>.json`, default plan dir `.code-indexer/plans/`. The `sha256:` spelling is filekeeper's own, so one hash vocabulary covers both tools.
- **No store**: there is no database, no registry and no index of plans. A plan reference is a path or a `sha256:<hex>` resolved in the plan dir; a path whose content hash differs from the requested hash is refused. Deleting the file deletes the plan.
- **Preparing a plan is the only write**: `plan-*` prepare commands write one file into the plan dir (a state location, not the sources); `--print` emits the same document on stdout and writes nothing at all.

Required members, and nothing else is required:

| Member | Type | Meaning |
|---|---|---|
| `schema_version` | integer, `1` | the format version; a reader refuses an unknown major |
| `plan_id` | string | `sha256:<hex>` as above |
| `operation` | enum | `replace_body`, `insert_symbol`, `delete_declaration`; `rename_symbol` is reserved and refused in phase 1 |
| `repository` | object | `{project, index_revision}`: the registered project name, and the revision the plan was prepared against as evidence, never as a precondition |
| `created_at` | string | RFC 3339, UTC, second precision |
| `provider` | object | `{name, version}`; phase 1 writes `tree-sitter-structural` and the extraction version |
| `target` | object | the locator of section 2, including the declaration's observed range |
| `files` | array | per touched file: `{path, expected_hash, newline, language}` |
| `edits` | array | the edit records of sections 3 and 5 |
| `validation` | object | `{syntax_required, semantic_required}` |

Optional members: `notes`, `diagnostics[]`, `warnings[]`. A reader ignores unknown members and preserves them on `show`; it never round-trips a plan by rewriting it, because rewriting changes the hash.

What a plan explicitly does not embed: full source files; the declaration's text when it exceeds the region cap of section 4; any absolute path; the index surrogate id; any service or model identifier.

### 2. The locator, and what revalidation proves

A locator is ADR-0002's key plus the file hash -- five members and the hash, and nothing else about the node is identity:

```
path, language, qualified_name, symbol_type, decl_hash, range{start_line, end_line, start_byte, end_byte}
```

The range travels with the locator as evidence: it is what a reader and the region guard compare against, and a node whose range moved while its declaration hash held is still the same symbol. It is not a sixth member of the identity, and the distinction is the one ADR-0002 section 1 draws between the key and the attributes beside it.

What invalidates a locator, and what `validate` therefore checks:

- the file's bytes differ from `files[].expected_hash` -- the file changed;
- the node's `decl_hash` differs -- the declaration's signature changed, which includes a reformat;
- no node matches `(qualified_name, symbol_type)` in that file -- the declaration moved out, was renamed, or was deleted;
- two nodes share that name, kind and `decl_hash` in the file -- the locator cannot say which one, and the answer is a refusal listing both (ADR-0002 section 5). Note that two overloads with identical normalized signatures collide here by construction, and the region hash is what separates the cases a human can see.

"Revalidate at apply" proves three things in order: the file is the file the plan was prepared against; the declaration node still exists at that identity; the bytes the edit replaces are the bytes the plan saw. The result names the basis it used:

```
identity: {verified: "structural", basis: ["file_hash", "decl_hash", "region_hash"]}
```

`verified` is `structural` in every phase-1 result. It is not semantic: it does not prove the symbol is the one a user meant among identically-signatured overloads, that no other file defines the name, that references still bind, or that the result compiles. A later record may add a semantic basis as an additional element of `basis`; it may not relabel a structural check as semantic.

### 3. Coordinates and encoding, as rules

1. **Paths** are repository-relative with `/` separators. A path that is absolute, contains `..`, or resolves outside the project root is refused (`PathEscapesProject`), which is the proposal's section 6.6 item 12 made mechanical.
2. **Bytes** are counted over the file's stored bytes, offset 0 being the first byte after any byte-order mark. A BOM lies inside no range and is preserved verbatim; keeper already preserves it.
3. **Lines** are 1-based and inclusive, and a terminator is not part of its line's text.
4. **Newlines**: every touched file declares `newline` as `lf` or `crlf`; `validate` refuses a file whose observed convention differs from the declaration (`NewlineMismatch`), and a file with mixed terminators is refused (`MixedNewlines`) rather than guessed at.
5. **Replacement text** always uses `\n` and never carries `\r`; the adapter translates it to the file's convention. A plan that encodes a carriage return is refused.
6. **End of file**: a final line without a terminator is a legal last line, and its `end_byte` is the end of the file's bytes as rule 2 counts them. Insertion after the last line uses `position: end` and never a line number, mirroring the primitive.
7. **Encoding** is UTF-8. A file that does not decode as UTF-8 is refused (`NotUtf8`): no lossy decode, no replacement characters, no writing back a lossy round trip.
8. **No columns.** Lines and byte offsets are both always present and `validate` cross-checks them -- line N's start byte must equal the recorded `start_byte`, and its end byte the recorded `end_byte` -- so a corrupted offset is caught by arithmetic rather than trusted. Code points and UTF-16 code units appear nowhere in the format. A provider that speaks UTF-16, which is the LSP default the proposal's section 6.4 flags, converts at its own adapter boundary, and the conversion is named in the plan's `provider` member. The boundary test is not this record's to state: there is exactly one, it belongs to the adapter, and ADR-0006 section 5 states it -- a converted position must land on a byte boundary of the decoded line, and a position in the middle of a line is not a conversion failure at all. Phase 1 never receives one, because its operations are whole-line and its ranges are line-granular; phase 2 accepts it and expands the edit to the whole lines it touches (ADR-0006 section 6).

### 4. Preconditions, and the validate/apply gap

- A file precondition is `files[].expected_hash`; an edit precondition is `edits[].expected_old_text_hash`, optionally accompanied by `expected_old_text` (capped at 8 KiB, omitted above the cap rather than truncated) so the adapter can also pass the primitive's region guard.
- `validate` and `apply` run the same precondition code. A passing `validate` is evidence, not a promise: between the two, any watcher, agent or editor may write the file.
- A plan whose preconditions pass at `validate` and fail at `apply` is refused whole. It is never partially honoured and never auto-repaired: `apply` does not re-derive the plan against the new bytes, because a re-derived plan is a different plan. The refusal is `ErrorType: PlanStale`, exit 5 -- the number ADR-0003 gives a concurrency conflict and keeper gives a stale hash, so one number means one thing across both tools -- and it names the file, the edit, and the expected and observed hashes.

### 5. Overlap, adjacency and ordering

- Within one file, two edits overlap if their line ranges intersect, or if an insertion point falls strictly inside another edit's range. Overlap is refused at `validate` (`OverlappingEdits`, exit 2), not resolved.
- Two edits are adjacent when one ends at line N and the next starts at N+1. Adjacent replacements are legal and independent.
- Two insertions at the same point are refused (`AmbiguousOrder`), because no deterministic rule can honour both without inventing an order the caller did not state.
- Application order is fixed: within a file, descending `start_line`, so an earlier edit never shifts a later edit's coordinates; across files, ascending path. The order is a property of the format, not of an implementation.

### 6. The lifecycle and the CLI surface

| Stage | Command | Writes | Output |
|---|---|---|---|
| DISCOVER, RESOLVE, PREPARE | `plan-replace-body`, `plan-insert-symbol`, `plan-delete-declaration`: symbol query plus the operation's arguments | one plan file, or nothing under `--print` | the canonical plan, its `plan_id`, and the edits it contains |
| VALIDATE | `plan-validate <ref>` | nothing | per-check verdicts: plan hash, schema, paths, file hashes, node re-derivation, region hashes, overlap |
| PREVIEW | `plan-show <ref> --diff` | nothing | every affected file and a unified diff computed from the plan plus current bytes |
| APPLY | `plan-apply <ref>` | the sources, through filekeeper (ADR-0005) | the per-file report of ADR-0005 |
| POST-VALIDATE, REPORT | part of `plan-apply` | nothing | per-file syntax verdicts, and the honest statement that this is not a compile check |

Every command has `--json`. `<ref>` accepts a path or `sha256:<hex>`. `plan-apply` is itself the explicit act of section 6.5 of the proposal; it also accepts `--dry-run`, which produces the whole report without mutating anything.

The exact command names may change in the implementation leg; they may not change these constraints: flat hyphenated verbs in the style of the twenty-seven existing commands rather than nested subcommands (`src/code_indexer/cli.py`), `--json` on every one of them, ADR-0003's exit-code taxonomy, and no command that applies a plan without a plan reference.

### 7. Validation honesty

| Level | What proves it | What this tool may claim |
|---|---|---|
| bytes | keeper's hash guard and the region guard | the intended bytes were written |
| parse | reparsing each touched file with the language's extractor | the file parses, or it does not, per file: `parsed`, `parse_error`, `parse_unavailable` |
| compile | a compiler or a semantic provider | nothing, in phase 1 |

`syntax_required: true` means the parse rung runs and its verdict is reported. The verdict may not be taken from the chunker, which cannot fail: `chunk_text` falls back to the regex chunker when a language has no grammar, when the parse raises, and when it yields no chunks, and it uses an error-tolerant tree when `root.has_error` is set (`src/code_indexer/ts_chunker.py:122-160`). The syntax verdict therefore reads the parser's result directly -- `has_error`, `is_missing` -- and a file that went through the regex fallback reports `parse_unavailable`, never `parsed`. `parse_unavailable` is a failed claim, not a pass. `semantic_required: true` is refused in phase 1 (`SemanticProviderUnavailable`, exit 3) because no semantic provider exists; a plan may not buy a stronger promise than the tool can keep. No output of this feature says "valid", "compiles" or "type-checks", and the syntax verdict is printed next to the caveat rather than above it.

**In the index as well.** The same three verdicts are what `index-status` counts per project -- files `parsed`, files `parse_error`, files `parse_unavailable` -- so the verdict is reachable from the CLI and not only from a plan report or a test. `index-status` reports the counts and never reports a file as parsed on the strength of the chunker's fallback, which is the defect this section names.

### 8. The first operation set

| Operation | What it needs from the identity model | Range it needs |
|---|---|---|
| replace a function or method body | the declaration node and its body child: `decl_hash` proves which declaration, the body node's range proves which bytes; the body node is the one the chunker's own signature split already locates (`_BODY_NODE_BY_TYPE`, `src/code_indexer/ts_chunker.py:161-175`), so the edit and the identity read the same node | the body's line range, which is whole lines for every language this tool extracts except a one-line Python body; a body whose first token is not at a line start is refused (`SubLineEditUnsupported`) |
| insert before or after a symbol | the declaration's whole range: `qualified_name` finds it, `symbol_type` narrows it, `decl_hash` proves it | one insertion point at the declaration's first line or the line after its last; a contiguous comment block immediately above moves the "before" point to the top of that block, and the move is recorded in the edit's `note` |
| delete a declaration | the same locator and the whole declaration range | the declaration's line range, deleted whole; body-only deletion is not offered |

None of the three uses the surrogate id, and none needs a range sharper than whole lines once the body node is the unit -- which is the point: the identity model of ADR-0002 supplies the node, and the primitive supplies the line. A range that is sharper than a line would be a coordinate nothing can verify.

## Requirements this record resolves

- **Requirement 2** in full: the locator is ADR-0002's key plus the file hash, the surrogate id is excluded, and revalidation reports which identity it verified (section 2). **Requirement 4**'s plan half is section 1's content addressing plus section 6's table; **requirement 5**'s boundary holds because the three P0 operations are syntax-only and wait on no language server.
- **Addition to requirement 2's locator list, stated as such:** the requirement names the declaration hash but not a region hash. Without it, the locator proves which declaration is targeted and says nothing about the bytes about to be replaced, which is precisely the case where a file hash has moved for an unrelated reason. `expected_old_text_hash` is added for that reason.
- **Disagreement with proposal section 6.4:** "Reject overlapping or ambiguous edits unless the plan explicitly defines their ordering" is the wrong rule. An explicit order over overlapping edits is a deterministic way to produce a result nobody can predict from reading the plan; the ordering is asserted by the author and verified by nothing. This record refuses instead (section 5).
- **Disagreement with proposal section 6.4's data model and range object:** `target.symbol_id` is exactly the locator ADR-0002 forbids, `plan_id: generated-id` is a name rather than an identity, and a column pair with no unit would be copied into plans and never checked by a format whose only primitive is line-granular. The replacements are the content hash, the locator, and no columns at all (sections 1, 2 and 3).

## Alternatives considered, and why rejected

- **A mutable plan store with generated ids, as the proposal sketches.** It needs a database, a lock, a garbage collector and a migration story, and it makes `show` a writer -- the one thing ADR-0003 section 1 forbids.
- **Locate the target by the surrogate symbol id.** ADR-0002 section 2 forbids it; it also breaks on the first reindex, which is exactly when a stale plan needs to be refused rather than applied.
- **A plan that is only a unified diff.** No ranges means no overlap detection, no per-edit region guard and no per-edit refusal message, and it hands the primitive keeper's context matcher, whose offset relocation ADR-0005 measures and rejects.
- **LSP-native coordinates everywhere (UTF-16 code units, 0-based lines).** Nothing in phase 1 can verify a column, the primitive accepts only lines, and 0-based lines contradict every other surface this tool ships. Conversion belongs at the provider boundary and is stated there.
- **Keep plans in the manifest, or silently widen a sub-line range to whole lines.** The first couples a read path to a write and makes a plan unshareable; the second is an edit the caller did not write, and refusal is cheaper to explain.

## Consequences

- A new artifact family appears, `.code-indexer/plans/`, holding generated documents that must never be committed; ignoring it is an implementation-leg task, named here so it is not forgotten. `plan-apply` is a write command, and the list that says so is ADR-0003 section 1's, which names the plan writers, `plan-apply` and its receipt. That roster is the only place a command's class is stated; this record does not restate it, because a second statement beside a closed list is what produced the contradiction this fold closes.
- Six commands appear that must carry `--json` from the first commit; the audit section 8 row records thirteen existing commands that do not, and this feature may not join them.
- A plan is executable input, not a human note: a plan file that cannot be validated is a refusal, a plan file edited by hand is a hash mismatch, and since a plan records the tree and not the user's intent, preview is the only place intent is checked -- which is why preview writes nothing and shows every affected file rather than a summary.
- Tests that must exist: coordinate cross-checking, CRLF, BOM, end-of-file insertion, mixed terminators, non-UTF-8 input, overlap and adjacency, stale refusal at apply, and the ambiguity refusal for two identically-signatured nodes.

## What this record does not decide

- How a plan reaches the filesystem: the primitive, the guarantee, compensation and recovery are ADR-0005's.
- Semantic identity, the refactoring provider interface, which language server is validated first, and how a semantic basis is added to `identity.basis`.
- Whether the plan commands are exposed over MCP, the project's name, and whether plans may be committed, shared between machines or archived.
- `rename_symbol`, multi-file semantic refactorings, and the `moved`-versus-`removed` classification in `changed-symbols`.
