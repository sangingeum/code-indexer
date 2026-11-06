# Architecture decision records

Decision records for the code-indexer overhaul. Each one settles a question the baseline audit left open (`docs/overhaul-baseline-audit.md`, section 11) and records the disagreement where the audit, the proposal or a reviewed requirement got it wrong.

| Record | Status | Decision in one line |
|---|---|---|
| [0001 -- core and extension separation](0001-core-and-extension-separation.md) | proposed | Indexing, structural query and lexical search install and run with no service; embedding and vector storage are an optional extension, the manifest stores chunk text so a pass can build a complete index with vectors off, and three version axes carry a policy matrix that never mixes vectors across models. |
| [0002 -- symbol identity and structural search](0002-symbol-identity-and-structural-search.md) | proposed | A symbol is identified by `(file, qualified name, kind, declaration hash)` with a revision-scoped surrogate id that an edit plan may not use as a locator; references keep the observed name and gain a nullable resolved target later; structural filters extend `find-symbol`; ambiguity returns candidates, never a pick. |
| [0003 -- read-only and staleness contract](0003-read-only-and-staleness-contract.md) | proposed | Read commands never write; staleness is reported in text and JSON as fresh, stale or unknown and repaired only on an explicit `--refresh`; a failed query exits non-zero; an unscoped query resolves one project, attempts one embed and stops at a wall budget. |
| [0004 -- the edit plan and its lifecycle](0004-edit-plan-and-apply-lifecycle.md) | proposed | A plan is an immutable JSON document named by the hash of its own canonical bytes, so show, validate and apply need no mutable plan store; it targets ADR-0002's line-free locator plus a file hash and a per-edit region hash, reports structural identity and never semantic, carries no columns and no UTF-16 positions, refuses overlap instead of ordering it, and claims only the bytes and parse rungs of validation. |
| [0005 -- applying a plan through filekeeper, and recovery](0005-filekeeper-apply-and-recovery.md) | proposed | A plan is applied as one hash-guarded line-range mutation per edit after a read-only pre-pass, giving refuse-before-first-mutation, per-file atomicity, reverse-order undo and no cross-file atomicity; patch application is rejected because keeper 0.1.8 gives it no hash guard and relocates hunks, a partial failure stops and reports per file rather than auto-undoing, and there is no direct-write fallback. |
| [0006 -- language-server provider strategy](0006-language-server-provider-strategy.md) | proposed | The provider interface and its contract suite land against a stub before any real server, Python is the first language on measured workload with the install left to the owner, capabilities are queried per provider and never assumed, UTF-16 positions are converted once at the adapter boundary into bytes and 1-based lines, and a failed semantic operation fails closed rather than falling back to text replacement. |
| [0007 -- the MCP surface](0007-mcp-surface-decision.md) | proposed | MCP survives only as an optional extra with eight read-only tools, a relay that keeps the CLI's streams and exit code, and a usage record whose first release cycle decides whether it stays; the catalog drops from eighteen tools to eight before it is judged. |
| [0008 -- the product name](0008-product-naming.md) | proposed | The tool keeps the name `code-indexer` for this phase, because the name is not the defect and a rename would land in the same release as three behaviour migrations; the rename cost is recorded as an inventory, and five stated conditions gate any later change. |

The order in which these records land is `docs/overhaul-plan.md`, which is ratified alongside them and decides no behaviour of its own.

## Conventions

- **Numbering** is sequential, four digits: `NNNN-concept-name.md`. A record is never renumbered.
- **Status** is one of `proposed`, `accepted`, `superseded`, `rejected`.
  A record is `proposed` until the owner ratifies it.
- **Sections** are fixed: the one-paragraph decision first, then context, decision, alternatives, consequences, and what the record does not decide. A reader who reads only the first paragraph still learns the answer.
- **Provenance** is cited inline: an audit section, a source path with lines, or a measured artifact. A number without a source does not belong in a record.
- **Measurement**: where a record needed a fact neither the audit nor its verification gate covers, the measurement is a script under `runs/milestone0/` and the record names the artifact and the script.
- **Language** is natural: concepts are named in words, not coordinate labels. `--` rather than em dashes. Paths are repository-relative; no host paths.
- **Disagreement is written down.** Where a record rejects the proposal, the audit or a reviewed requirement, the disagreement, the better rule and the evidence are all in the record. A record that agrees with everything has failed.
- **Amendment**: a decision changes only by a superseding record, which names what it supersedes. Typos and citation corrections are fixed in place.
