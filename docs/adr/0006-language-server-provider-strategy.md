# ADR-0006: language-server provider strategy

- Status: proposed (awaiting owner ratification)
- Date: 2026-11-07
- Deciders: owner (ratification), architecture review
- Supersedes / Superseded by: --

## Decision in one paragraph

The refactoring provider interface -- capabilities, symbol resolution, reference lookup, prepare-rename and plan material -- lands before any real language server, against a stub provider and repository fixtures, so the abstraction is shaped by the contract instead of by one server's quirks, and a real provider earns its registration by passing the same contract suite. The first real provider is Python, chosen from measured workload rather than preference, with the install itself left to the owner as a decision and not a milestone step; C# is the second candidate and C++ is deferred with the reason recorded. Capabilities are queried per provider and never assumed, positions are converted from LSP's UTF-16 code units at one named adapter boundary into ADR-0004's bytes and 1-based lines, every semantic answer carries its provider's name and version, and a semantic operation that fails fails closed -- it never degrades into text replacement.

## Context

1. Nothing of this exists. The audit records the provider interface and every semantic refactoring as absent, each with a `rg` over `src/` that returns nothing (audit section 8). There is no format to extend and no half-built adapter to rescue.
2. No server is installed. `command -v` finds none of `clangd`, `pyright-langserver`, `pylsp`, `roslyn-language-server`, `OmniSharp`, `csharp-ls`, `gopls` (audit section 9); adding `typescript-language-server` to the same list prints nothing and exits 1. Every option is an install decision for the owner.
3. What the tool is actually pointed at, counted per registered project from each manifest's `files` rows (`SELECT language, COUNT(*) FROM files GROUP BY language`) and, for the development-tree column, by extension under each checkout (`fd -e cs -e py -e ts -e cpp . <root>`):

| Language | Registered projects containing it | Indexed source files | Files in the development tree, raw |
|---|---|---|---|
| C# | 2 of 12 | 820 | 7,444 |
| Python | 10 of 12 | 407 | 13,640 |
| TypeScript | 0 of 12 | 0 (8 JavaScript files in one project) | 283 |
| C++ | 0 of 12 | 0 | 348 |

   The development-tree column counts every checkout, including several worktree clones of the same projects, so it corroborates rather than measures; the registered-project column is the workload this tool would serve. The C++ figure is further inflated by generated protobuf sources under CMake build directories.
4. What installing each would take on this host (same artifact): `clangd` has an apt candidate (`1:18.0-59~exp2`, noble/universe), so a C++ server is one system package. `dotnet` 8.0.133 is present with one global tool installed, and it is not a language server (`dotnet tool list -g` reports `dotnet-script 2.0.1`), so a Roslyn server is still one `dotnet tool install`. `node` 26.7.0 and `npm` 11.19.0 are present, so a Node-distributed Python or TypeScript server is one package install. Global `pip` is blocked (PEP 668) but `uv` 0.12.5 is present, so a Python server installs as a uv-managed tool. `jedi` is not importable, and it is a library rather than a server: it would give an in-process rename through the same interface without a subprocess lifecycle.
5. Only rename genuinely needs a server. Structural edits (replace a body, insert at a declaration boundary, delete a declaration) are syntax-only and are already scheduled on ADR-0004's plan with no semantic provider; requirement 5 states this and ADR-0004 section 7 refuses `semantic_required` plans while no provider exists.
6. The plan format cannot express an intra-line edit. ADR-0004 section 3 states there are no columns, and section 5 refuses sub-line ranges; the only mutation primitive that carries a hash guard takes whole lines (ADR-0005 section 2). A rename edits identifiers inside a line, so a normalization rule is needed before a rename plan can exist at all.
7. The proposal's own boundaries apply: no Serena dependency, no second implementation of the same semantic operation, and no provider reported as supported before integration tests verify it (proposal sections 7.1 to 7.3).

## Decision

### 1. The interface, and what lands first

| Operation | Input | Output |
|---|---|---|
| `capabilities` | -- | per operation: `supported`, `unsupported`, `unknown`, plus provider name and version |
| `resolve_symbol` | path, position or qualified name | candidate declarations with their ranges, or a typed ambiguity |
| `find_references` | one declaration | reference locations, or a typed refusal |
| `prepare_rename` | one declaration, a new name | provider edits, in UTF-16 positions, before normalization |
| `materialize` | provider edits | a candidate plan body of line-aligned edits for ADR-0004 validation |

- The interface ships with a **stub provider** in the test tree that answers from fixture files, and a contract suite that any provider must pass: each operation returns either its documented result or a typed refusal; an unsupported operation returns `unsupported` and never raises something that reads as a crash; a provider edit that crosses a line boundary is normalized or refused according to section 6, never truncated; every result carries provenance.
- No real server is required to land the interface, and no milestone may depend on one being installed.
- The contract suite is the registration test. A provider that has been written but not passed is not registered and cannot be selected by configuration.

### 2. Which language first

**Python**, on breadth: ten of twelve registered projects contain Python source, against two for C#, and the tool's own repository, fixtures and test corpus are Python, so the provider is exercised by the work the household already does rather than by a demonstration repository. The install route needs no system package and no compiler configuration: a uv-managed Python server, or `jedi` in-process.

The tension is stated rather than hidden: C# has twice the indexed file count (820 against 407), but it is two projects, one a snapshot of the other, both game-simulation code whose project model a Roslyn server would need to load before it could answer anything.

- **Reversal rule:** the first real server is accepted only if the contract suite passes against it. If the Python server cannot express rename, Python is not the answer; the next candidate is C#, and the refused report is recorded rather than the decision being re-litigated from preference.
- **C++ is deferred.** The development tree holds one C++ project (226 sources excluding its build directories, with a CMake compilation database), but no registered project contains a single C++ source file, so a C++ provider would serve a codebase the tool does not index and whose build configuration it has never read. Deferring costs nothing that is being used and avoids starting with the provider that needs the most provisioning.
- **TypeScript is deferred:** one project holds eight JavaScript files and no TypeScript, and a server would have to be installed for a language nothing is written in.
- Installing a server, choosing its version and provisioning its project model are **owner decisions**. This record fixes the order and the acceptance test, not the install.

### 3. Capability honesty

- Every capability answer is per provider and per operation, and `unknown` is a distinct value from `unsupported`: a server that did not answer a capability query has not thereby denied it.
- A request for an operation the selected provider does not support is refused with `ErrorType: UnsupportedOperation` and exit 2 -- it is a usage-class refusal, because the caller asked the tool for something the tool does not offer. It is never answered by a weaker local approximation.
- **No provider installed is a different situation with a different number**: exit 3, `ErrorType: ProviderUnavailable`, one line naming the install route -- the number ADR-0003 section 5 gives an unavailable backend or extension. The two are not interchangeable: 2 says a working provider answered that it cannot do this, and 3 says nothing answered because nothing is there. A caller's next move follows from the difference -- another operation, or an install. A provider that is installed but never becomes ready, or times out during the handshake, is also 3, because it answered nothing.
- Nothing in the CLI, the skill or the documentation may say a refactoring is available because a language has a server; availability is a property of `(provider, operation)` as measured by the contract suite.

### 4. Lifecycle

- **Lazy start.** No command spawns a server unless it needs a semantic answer. The syntax-only commands -- `overview`, `skeleton`, `outline`, `find-*`, `deps`, `changed-symbols`, the three structural plan operations -- are unaffected by a server being absent, slow or broken.
- **Bounded reuse.** One server process per project per invocation, reused across the operations of that invocation and terminated at exit. A long-lived daemon is refused for phase 1: it would add a supervision problem to a tool whose current failure mode is a stuck read.
- **Timeouts and cancellation.** Every request carries a deadline; an expired request is cancelled with `$/cancelRequest` and reported as a provider timeout, not as an empty semantic answer. A server that fails to become ready within its start budget is reported as unavailable, and the command's answer says so.
- **Provenance.** Every semantic answer carries `provider: {name, version, transport}`, and a plan built from one carries the same object in ADR-0004's `provider` member. A result with no provenance is a defect, not a default.
- **The index stores no server identifier.** A provider's symbol ids do not survive a project reload, a server upgrade or a workspace change, and ADR-0002 section 2 already says a persisted id is meaningless across revisions. Semantic answers are computed on demand and are never written into symbol rows.

### 5. Position encoding, converted exactly once

- LSP positions are 0-based lines with UTF-16 code-unit characters. ADR-0004 counts 1-based inclusive lines and bytes over the file's stored bytes. These are three different units for the same place.
- Conversion lives in **one module** in the provider adapter, as two named functions, and nowhere else. A provider answer enters the core through it; nothing downstream sees a UTF-16 position.
- The conversion is proved per file's `newline` and byte-order-mark state, and its test set is fixed here: a line containing astral-plane characters, a CRLF file, a file with a byte-order mark, an empty last line, and a position whose UTF-16 offset falls inside a surrogate pair (refused, not rounded).
- **The one boundary test, applied by the adapter to every converted position**: it must land on a byte boundary of the decoded line. A UTF-16 offset that falls inside a surrogate pair, or anywhere the line's decoding cannot place exactly, is refused (`PositionConversionFailed`), because a silently rounded offset is the same defect class as a plan column nobody checks.
- **A mid-line position is not a failure of that test, and each phase says what it does with one -- this clause is the only place either answer is stated.** Phase 1 has no use for a mid-line position: every structural operation is whole-line, its ranges are line-granular (ADR-0004 section 3) and validation refuses a range that is not whole lines, so one never reaches a phase-1 operation. Phase 2 takes it as the normal case, because a rename edits identifiers inside a line, and expands the edit to the whole lines it touches (section 6). Reading these as two tests -- or as ADR-0004 refusing what this section accepts -- is the misreading this clause exists to end.
- The conversion's version is recorded in the plan's `provider` member, so a plan written under one conversion is not silently reinterpreted under another.

### 6. From provider edits to a plan

A provider returns edits in its own coordinates. The adapter normalizes them into ADR-0004's line-granular world without weakening either side:

- Each provider edit is expanded to the **whole lines it touches**. The line's new text is computed by applying the intra-line replacement to that line's old text; bytes outside the provider's range are preserved exactly.
- Two edits on the same line merge into one line edit, in descending byte order so an earlier edit never shifts a later one.
- Each edit's `expected_old_text_hash` covers the whole old line, so ADR-0005's region guard proves the bytes about to be replaced.
- A provider edit whose range crosses a line terminator is refused (`NonLineAlignedProviderEdit`) in phase 1 rather than approximated, matching ADR-0004's refusal of a body that does not begin at a line start.
- Enabling this on the plan format is a **schema bump to version 2** with `operation: rename_symbol`, which ADR-0004 section 1 reserves and refuses. This record fixes the normalization rule so that record can be written; it does not enable the operation.

### 7. No fallback

A semantic rename that the provider refuses, cannot resolve, times out or answers ambiguously returns a refusal with the provider's reason. It is never retried as a text replacement, a regex rename, an identifier match or an AST guess. A fallback that is right most of the time would make a wrong answer indistinguishable from a right one at exactly the moment a user relies on the tool -- the same reason the proposal's fail-closed rule exists.

## Requirements this record resolves

- **Requirement 5** in full. The interface-first addition is section 1; the single language chosen by workload is section 2; the install being an owner decision is stated there and in the plan; and the note that structural edits wait on no server is section 4's first rule, consistent with ADR-0004.
- **Agreement with ADR-0004 section 3**, stated so the two records cannot drift: UTF-16 conversion belongs at the provider boundary and the plan records it. Section 6 adds the mechanism that record left open -- how a sub-line edit becomes a line edit.
- **Disagreement with proposal section 6.2's interface list:** `validate_plan(plan)` belongs to the provider there. It must not. Validation is ADR-0004's ladder over our own document, and a provider asked to validate our plan will answer about its own view of the tree -- a second, weaker verdict that could contradict the first. The provider supplies facts; the plan's validation stays in one place.
- **Disagreement with proposal section 7.2's framing of a provider as a per-language plugin:** the unit of support is `(provider, operation)`, not language. A server that gives definition and references but no rename is worth registering for the two it can do, and the contract suite is what makes that safe.

## Alternatives considered, and why rejected

- **Install a server first and shape the interface around it.** The abstraction would encode one server's lifecycle, one server's capability gaps and one server's position quirks; the second provider would then be a rewrite of the first. The cheap order is contract, stub, then server.
- **One provider per language immediately.** Four providers, four install stories and four contract runs, for languages where two of the four have zero registered projects. Requirement 5 exists to prevent exactly this.
- **UTF-16 inside the plan, or 0-based lines everywhere.** The primitive is line-granular and the rest of the tool is 1-based; either choice spreads a unit mix through every surface to save one conversion.
- **An in-process `jedi` provider as the whole semantic story.** It is the cheapest Python route and it has no project model worth the name; it cannot answer for C# or C++, so it would be an interface with one implementation and no pressure to generalize. It remains a legitimate second Python provider once the interface exists.
- **Keep a text-replacement fallback for names the server cannot resolve.** See section 7.
- **Put the provider methods on `Core`.** The core already carries five eagerly built collaborators (audit section 2); a semantic provider with a subprocess lifecycle belongs behind an adapter, not inside the facade.

## Consequences

- The contract suite runs in CI with no server installed, so the interface is testable on a machine that has none -- which is every machine in this household today.
- A rename plan will need plan schema 2 and a record that enables `rename_symbol`; the structural operations of ADR-0004 are unaffected and ship first.
- Python first means C# rename waits behind an install and a second contract run; the order is written down so the wait is a decision rather than a drift.
- The tool will say `unsupported` more often than a user expects, in exactly the places where a competing tool would guess. That is the honest state until providers cover the languages.
- Tests that must exist: conversion against astral-plane text, CRLF, byte-order mark and an empty last line; a stub provider answering `unsupported`; a provider whose edit crosses a line terminator refused; two edits on one line merged; a rename plan refused while schema 1 is current; a timed-out request reported as a timeout rather than as an empty result.

## What this record does not decide

- Which refactorings ship in which round -- the milestone plan's, and this record deliberately does not pre-empt it.
- Whether a server is installed at all, which one, at which version, or how its project model is provisioned. Those are owner decisions.
- Whether the provider surface is exposed over MCP, and what a semantic refusal looks like through the relay.
- The plan schema 2 format, which is a successor record's; only the normalization rule is fixed here.
- Whether a semantic identifier is ever persisted on a symbol row. This record says not in phase 1, and a later record may overturn that with its own argument.
