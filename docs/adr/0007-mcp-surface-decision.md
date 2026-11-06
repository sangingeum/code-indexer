# ADR-0007: the MCP surface

- Status: proposed (awaiting owner ratification)
- Date: 2026-11-07
- Deciders: owner (ratification), architecture review
- Supersedes / Superseded by: --

## Decision in one paragraph

The MCP server is kept, but only as an optional extra carrying a narrowed read-only surface, and it is kept **on probation with a measurement it does not yet have**: the server gains a one-line-per-invocation usage record, and the next decision -- keep or remove -- is taken on those lines rather than on this paragraph. The measured cost is 18 tools, 17,660 to 22,491 bytes of schema and 284 ms on every CLI start, plus 42 tests; the measured value is a thin relay with no store access that duplicates the CLI, whose own household skill routes every workflow through the CLI and mentions MCP only as "this command has no MCP tool". The relay is fixed in the same round so that a client sees the CLI's exit code and its two streams, because ADR-0003's read-only contract is worthless across a boundary that reports every failure as success.

## Context

1. **Cost, measured.** 18 registered tools; 22,491 bytes of `{name, description, inputSchema}` serialized with an indent, 17,660 compact, which is roughly 5,622 and 4,415 tokens respectively (audit section 6; gate bar 8 reproduces both byte counts exactly). `semantic_search` alone carries a 1,514-byte description and 13 parameters. The server has 42 tests: 40 in `test_mcp_server`, 2 in `test_mcp_stdio_handshake` (audit section 5).
2. **Cost on every CLI start.** `mcp` is imported by `code_indexer/__init__.py:8` through `server.py`, so every invocation of every command pays 284 ms for a server it is not running (audit section 4; gate bar 10). ADR-0001 removes this by making `mcp` an extra and by keeping `__init__` free of submodule imports; that part of the cost is therefore already scheduled, and this record does not need to re-argue it.
3. **Value, measured.** `server.py` is 567 lines of argv building and relaying. It reads no manifest, no registry and no store, and `test_server_is_thread_free_and_core_free` enforces that isolation (audit section 6). The adapter discipline the proposal asks for in section 8.3 already holds.
4. **Value, measured, and weaker than it looks.** The 18 tools are not a superset of the CLI: `overview` -- the first command in the skill's decision tree -- has no MCP tool, and neither do `index-more`, `sweep-orphans`, `doctor`, `watch`, `unwatch`, `eval`, `eval-compare`, `map` or `file-outline` (audit section 6). A client that speaks only MCP cannot orient itself the way the documented workflow does.
5. **The relay misreports.** `server.py:105-118` concatenates stdout and stderr, strips them, and substitutes a synthesized message for a nonzero exit with empty output. The exit code is discarded. A failed search and an empty one look identical to the client, which is the same defect ADR-0003 section 5 fixes inside the tool (audit section 6, section 10 item 10).
6. **The reads are not read-only yet.** `semantic_search` carries `readOnlyHint=False` with the reason stated in the source: the staleness probe writes (ADR-0003 context item 5). Until ADR-0003 lands, no annotation on this surface can be true of what a client observes.
7. **Adoption evidence does not exist.** There are no tool-call logs for this server, on this host or anywhere the audit could reach. Neither adoption nor non-adoption is established, and this record does not invent a number for either. What exists is documentary: the installed household skill is byte-identical to `SKILL-CLI.md`, whose four MCP mentions are all "this command has no MCP tool" and whose decision tree is entirely CLI (audit section 6), and `README.md` mentions MCP 19 times including a parity table and a client configuration block.
8. **The proposal's own bar.** Section 8.3 says to keep MCP only if usage evidence justifies it, to keep it thin if kept, and to avoid a huge tool catalog that burdens agent context. Section 8.3 also says not to delete it automatically.

## Decision

### 1. Status: an optional extra, with a narrowed surface

- `mcp` becomes the `code-indexer[mcp]` extra, imported only by the `code-indexer-mcp` entry point (ADR-0001 section 1). A core install ships no tool schemas and no server code.
- The surface is narrowed to read-only query tools, one tool per query shape, with no tool that duplicates another:

| Kept | Why |
|---|---|
| `list_projects` | a client has no working directory, so project selection needs an answer |
| `overview` | the first step of the documented workflow, absent today |
| `skeleton` | orientation over a repository or subtree |
| `find_symbol` | structural lookup |
| `find_references` | the read surface a rename preview will need |
| `find_callers` | the one graph direction worth a tool |
| `get_code_context` | reading exact lines by range or symbol |
| `semantic_search` | registered only when the semantic extra is importable |

- Removed: the three lifecycle tools (`add_project`, `reindex_project`, `remove_project`, and the implicit write path behind them), `index_status`, `lookup_project`, `outline`, `deps`, `find_callees`, `find_symbols`, `changed_symbols`. Writes are exactly what an MCP client should not be able to trigger by a tool call that carries no preview, and every removed read duplicates a kept tool's shape or the CLI.
- Eight tools with one schema each is a starting point the implementation leg may trim further; it may not add a tool that the rule above excludes without a superseding record.

### 2. The relay must stop lying

The `code-indexer-mcp` process keeps the CLI's two streams separate and returns the CLI's exit code in the tool result:

- a successful call returns the CLI's stdout as its content;
- a failing call returns the CLI's stderr as its content and `isError: true`, with `ErrorType` and the numeric code preserved in the structured result;
- no synthesized message replaces a nonzero exit;
- the merged-stream behaviour and the 14-versus-18 stale docstring are deleted rather than corrected.

Only after ADR-0003 lands **and** this relay holds may `readOnlyHint` be published as true for a read tool, and `idempotentHint` with it. The annotations are a claim about what a client sees; until both hold, the claim is false and the surface is not released.

### 3. The measurement this decision actually needs

- Each invocation appends one line to a usage record under the index root: tool name, outcome, exit code, response bytes, duration, and a timestamp. Nothing else, and no arguments -- a query string may be source code.
- The record is written by the MCP process, not by a CLI read command, so ADR-0003's read-only contract is untouched; the path is stated in the README so nobody has to guess what the tool writes about itself.
- A failed write to that record must not fail the call.

### 4. The re-decision, dated by evidence rather than by a calendar guess

The next decision is taken once the semantic extra has shipped and one release cycle has passed with the record in place:

- **zero invocations** in that window: the server is removed in the next minor release, with the exit path in section 5;
- **any invocations**: the surface stays and the review reads the record -- which tools were called, how often, and whether the narrowed set was the right one;
- the record itself is the deliverable that makes the next decision cheap, and a second "we have no evidence" verdict is not an acceptable outcome of this record.

### 5. What a caller experiences when a tool goes away

- The removed tools do not disappear silently: `README.md`'s MCP section, the parity table and the client configuration block are rewritten in the same commit; `code-indexer-mcp --help` lists the registered tools and says what was removed; `CHANGELOG.md` carries the removal under a named release with the migration line ("use the CLI subcommand of the same name").
- A client call to a removed tool gets the protocol's own unknown-tool error, which is the honest answer; the README is where the caller learns why.
- The published client configuration keeps working if the entry point keeps its name; only the install line changes to include the extra: `uvx --from "code-indexer[mcp]" code-indexer-mcp`.
- The 42 tests do not survive as 42: the 10 removed tools' tests are deleted with them, `test_server_is_thread_free_and_core_free` and the stdio handshake test stay, and a new test asserts that the registered tool set equals the documented list -- the defect class that produced "all 14 tools" is closed by a test, not by proofreading.

### 6. What is being accepted, stated plainly

This record does not claim the MCP surface has earned its cost. It claims that the cost is measurable, that the value is thin but real (a structured interface for clients that have no shell), that the evidence needed to end the argument is cheap to collect, and that removing a working adapter before it is instrumented would destroy the ability to decide. That is a probation with a termination clause, not an endorsement.

## Requirements this record resolves

- **Requirement 4's relay consequence**, named by ADR-0003 and handed here: the exit code and the stream split are restored before any annotation is published as true.
- **Proposal section 8.3**: the thin-adapter condition already holds and is preserved; the "only if usage evidence justifies it" condition is answered by making evidence exist rather than by assuming it; the "avoid a huge tool catalog" condition is section 1's narrowing.
- **Disagreement with the proposal's default ("make MCP optional and secondary")**: optional, yes; secondary is the wrong metaphor. A second surface that answers one query shape differently from the CLI is a second product, and the narrow-survival rule (one tool per query shape, read-only) is what keeps it a view of the first.
- **Disagreement with the audit's framing** (section 10 item 7: "Milestone 7 already has enough evidence"): the cost side is measured and the value side is not. Presenting a cost measurement as a decision is how a working surface gets deleted by a reviewer who never saw a client use it, and it is how a useless one survives because nobody could prove otherwise.

## Alternatives considered, and why rejected

- **Remove now.** The cheapest option today and the most expensive to reverse: no adoption record exists, so removal would be justified by absence of evidence rather than by evidence of absence, and the SDK integration, the isolation test and the handshake test would have to be rebuilt if a client appeared.
- **Deprecate with a dated removal.** A date chosen without data is a guess wearing a deadline. Section 4's evidence gate is the same decision with a real trigger.
- **Keep all 18 tools as an optional extra.** The import cost is gone once it is an extra, but the context cost is not: a client still loads 22,491 bytes of schema, including a 1,514-byte description, to discover eight tools, one of them conditional. The catalog is the product for an MCP client, so narrowing it is the decision, not a tidy-up.
- **One generic passthrough tool that takes CLI arguments.** Smallest possible schema and permanent parity. Rejected: it is a shell wearing a JSON hat -- no typed arguments, no per-operation validation, no honest annotation, and an agent that can call a shell already has one. The structured interface is the only reason for this server to exist.
- **Leave the relay alone and publish no annotations.** Then the surface tells a client nothing it can branch on, which is the current state with fewer words.

## Consequences

- A core install loses 284 ms of startup and 22,491 bytes of schema; an MCP user gains an install flag and eight tools instead of eighteen.
- Ten tool schemas and their tests disappear. The parity table in `README.md` becomes a short list, and the "no MCP tool" notes in the skill can be deleted rather than maintained.
- A client that used `deps`, `outline`, `changed_symbols` or a lifecycle tool breaks on upgrade. That is stated in the release note; the CLI has had every one of them all along.
- MCP becomes observable for the first time, which is also the first time it can be judged.
- The server's own help and docstrings become generated from the registry, so the "all 14 tools" drift cannot recur.

## What this record does not decide

- Whether a different MCP-shaped surface (a single passthrough, a resource-based interface, a remote transport) ever ships. Both are superseding records if wanted.
- The exact annotation values once ADR-0003 holds; this record says only that they may then be true, and that ADR-0003 defines what true means.
- Whether the provider surface of ADR-0006 is exposed over MCP. It is not exposed in phase 1, and enabling it is that record's successor.
- The removal date itself, which section 4 makes a function of measured use rather than a date chosen now.
