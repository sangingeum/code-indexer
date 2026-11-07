# Round 5, leg 1 — the refactoring provider interface, a stub provider, the contract suite

Card id withheld (an authored record is hygiene-governed; the dispatch brief carries it). Base `1177458`. Tree `<project-root>`.
No language server was installed, spawned or probed: the stub is the only provider in this leg, and every test here runs without one (`command -v` finds none of `clangd`, `pyright-langserver`, `pylsp`, `roslyn-language-server`, `OmniSharp`, `csharp-ls`, `gopls`, `typescript-language-server`).

## Commits

| SHA | slice |
|---|---|
| `db2b5f8` | the interface, typed refusals, provenance, the position module, and the plan format's reserved rename now refusing as `UnsupportedOperation` |
| `467175b` | the five fixed position-conversion cases |
| `9af08f0` | provider edits to line-aligned plan edits |
| `becef85` | the stub provider and the contract suite (the registration test) — **red until the follow-up below**: the working tree that passed there carried an uncommitted closed-session check, so that commit is a declared deviation, not a green one |
| `9b92cea` | `--json` on the four pass commands, `json-schema.md`, the skill's command block |
| `2239b67` | the CHANGELOG entry and this record's first form |
| the commit that carries this line | the slice-4 follow-up (the closed-session capability check and the test's unclosed file handle, together with this correction) — green |

Every commit is signed (`git log -1 --format='%h %G?'` → `G`). The tree is
green from the follow-up commit on; the full `-W error` run passed at the tip
(`869 passed, 2 deselected`), against 822 passed at the base. The one red
intermediate commit above is named here rather than claimed green (the
missing-dependency class the commit discipline names: a test whose passing
depended on a fix left in the dirty tree). The installed skill copy is
byte-identical to `SKILL-CLI.md`.

## What was built

`src/code_indexer/refactoring_provider.py` is the interface;
`src/code_indexer/provider_positions.py` is the only position conversion
site; `tests/provider_stub.py` and `tests/fixtures/provider/` answer from
fixture files; `tests/test_provider_contract.py` is the registration test.

Five operations, with documented results or typed refusals:

| Operation | Result |
|---|---|
| `capabilities` | per operation `supported` / `unsupported` / `unknown`, plus `provider: {name, version, transport}` |
| `resolve_symbol` | path+position or qualified name → candidate declarations with plan ranges; two or more candidates → `AmbiguousSymbolError` (exit 2) listing every candidate |
| `find_references` | one declaration → locations in plan coordinates; a provider refusal → `ProviderRefusal` (exit 1), never a text approximation |
| `prepare_rename` | declaration + new name → provider edits in UTF-16 positions, before normalization |
| `materialize` | provider edits → line-aligned candidate plan edits |

`materialize` is adapter-side; the other three are delegated to the
provider. `validate_plan` stays in `plans.py` and is not offered to a
provider, per the record's disagreement with the proposal.

## Capability honesty

Three states, and `unknown` is a third value on purpose. A provider that did
not answer a capability question has not denied the operation, so an
`unknown` operation is attempted and the provider's own answer decides; an
`unsupported` operation is refused **before** the provider is asked, with
`UnsupportedOperation` (exit 2) and no edit produced. An absent provider is
`ProviderUnavailable` (exit 3) with one line naming the install route; one
that never becomes ready, or that times out, is `ProviderTimeout` (exit 3,
a `ProviderUnavailable` subclass) — the difference between "a working
provider says it cannot" and "nothing answered".

The contract suite's capability report, as produced from the fixture stub:

```json
{"provider": {"name": "stub-fixture", "version": "1.0.0", "transport": "in-process"},
 "operations": {"resolve_symbol": "supported", "find_references": "supported", "prepare_rename": "unsupported"}}
```

`prepare_rename: unsupported` is the risk the plan names as acceptable, and
the suite asserts it as data rather than reading it out of prose. The same
path is proved end to end under a profile that reports rename supported:
provider edits → `materialize` → `def hello(name):` on the first whole line,
with the region hash over the whole old line.

## Lifecycle

- **Lazy start.** Constructing a session spawns nothing (asserted:
  `starts == 0`, `_instance is None`); the first semantic request starts the
  provider, and the capability query is the first thing asked. Every
  syntax-only command is untouched.
- **Bounded reuse.** `session_for(project_root)` returns one session per
  project per invocation; the suite asserts the second call is the same
  object and a different root is a different session. `close()` terminates
the provider and the executor; `atexit` covers process exit. No daemon.
- **Deadlines.** Requests run on a one-worker executor and are awaited with
  the session's deadline. An expiry calls the provider's `cancel()` and
  raises `ProviderTimeout`, which the suite asserts with a provider that
  sleeps past a 0.05 s deadline: the call returns in under 0.5 s, the stub
  records `cancelled`, and the error is a timeout rather than an empty
  answer. A start budget that expires is the same exit 3 with a "become
  ready" message.
- **Provenance.** `resolve_symbol`, `find_references` and `prepare_rename`
  answers each carry `provider: {name, version, transport}` and the suite
  compares all three blocks.
- **No provider in the index.** The module imports neither the store nor the
  manifest, and the suite scans its source for `manifest_for`, `INSERT INTO`,
  `UPDATE ` and the store/manifest imports.

## Positions, converted once

`provider_positions.position_to_offset` (provider → plan) and
`offset_to_position` (plan → provider) are the only two conversion
functions; both apply the one boundary test — the offset must start a
decoded character and decode to exactly the prefix the provider named — and
a position that cannot be placed exactly is `PositionConversionFailed`,
never rounded. Offsets are into the file body, so a byte-order mark lies
before offset 0 and is preserved.

The five cases fixed by the record are in
`tests/test_provider_positions.py`: an astral-plane line (2 UTF-16 units, 4
UTF-8 bytes), a CRLF file (offsets skip the terminator, a terminator offset
is refused), a file with a byte-order mark, an empty last line, and an
offset inside a surrogate pair (refused). Two more prove the reverse
direction: an offset inside a multi-byte character is refused, and every
character boundary round-trips through both functions.

## Provider edits to plan edits

- Each edit expands to the whole line it touches by splicing **bytes** into
the old line's bytes — a character slice would be wrong once the line holds
an astral character, and the astral test caught exactly that.
- Two edits on one line merge into one line edit in descending byte order.
- Each record's `expected_old_text_hash` covers the whole old line, and
  `expected_old_text` is carried under the 8 KiB cap, above it omitted.
- A cross-line edit is `NonLineAlignedProviderEdit` (exit 1), never
  truncated or widened. Two overlapping intra-line edits are
  `OverlappingEdits` (exit 2), the format's own refusal.
- The candidate edit's kind is `rename_symbol`, the operation the plan
  format reserves. `plans._check_schema` now raises `UnsupportedOperation`
  (exit 2) for that operation instead of `PlanFormatError`, so the refusal
  names the same taxonomy leaf the provider path uses; the plan-format test
  asserts exit 2 and that the source file is byte-identical after
  validation. No plan document carrying the operation can be sealed, and no
  `rename-symbol` command exists (`--help` counts 0).

## The four pass commands' `--json`

`add-project`, `index-more`, `remove-project` and `reindex-project` answer
one object: `schema: 1`, `command`, the path and slug, `vectors` where it
applies, `scope_include` for `index-more`, `collections` / `alias` /
`alias_dropped` for `remove-project`, the pass outcome as
`index: {slug, state, result | detail | error}`, and `notes`. No staleness
block: a pass reports its own work. A failure carries `ErrorType` and the
taxonomy's exit code. `docs/json-schema.md` documents all four and the
shared pass shape; the text renderer's lines are unchanged and one test
pins that.

## Gates, as run

| Gate | Result |
|---|---|
| `uv run pytest tests/test_provider_contract.py -q` | `20 passed in 0.15s` |
| `uv run pytest tests/test_plan_format.py -q` | `66 passed in 1.33s` |
| `code-indexer --help \| rg -c rename-symbol \|\| echo 0` | `0` |
| `uv run pytest tests/test_provider_positions.py -q` | `9 passed` |
| `uv run pytest tests/test_provider_materialize.py -q` | `11 passed` |
| `uv run pytest tests/test_pass_json.py -q` | `7 passed` |
| `uv run pytest tests/ -q -W error` | `869 passed, 2 deselected in 101.38s` |
| `uvx ruff check src tests scripts` | `All checks passed!` |
| `uv run python scripts/publishing_hygiene_lint.py` | `violations=0` |

## What this leg does not do

- It installs no language server and chooses none: the install is an owner
  decision, and the record fixes the order (Python first) and the acceptance
  test, not the install.
- It does not enable `rename_symbol` in the plan format, does not bump the
  plan schema, and does not expose a rename command.
- It does not expose the provider surface over MCP, and it adds no CLI
  command: the roster stays at 33, so nothing in the CLI can advertise a
  refactoring because a language has a server.

The next leg is the agent surface (the skill split, the README rewrite and
the installed-copy drift checks).
