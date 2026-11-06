# Architecture decision records

Decision records for the code-indexer overhaul. Each one settles a question the baseline audit left open (`docs/overhaul-baseline-audit.md`, section 11) and records the disagreement where the audit, the proposal or a reviewed requirement got it wrong.

| Record | Status | Decision in one line |
|---|---|---|
| [0001 -- core and extension separation](0001-core-and-extension-separation.md) | proposed | Indexing, structural query and lexical search install and run with no service; embedding and vector storage are an optional extension, the manifest stores chunk text so a pass can build a complete index with vectors off, and three version axes carry a policy matrix that never mixes vectors across models. |
| [0002 -- symbol identity and structural search](0002-symbol-identity-and-structural-search.md) | proposed | A symbol is identified by `(file, qualified name, kind, declaration hash)` with a revision-scoped surrogate id that an edit plan may not use as a locator; references keep the observed name and gain a nullable resolved target later; structural filters extend `find-symbol`; ambiguity returns candidates, never a pick. |
| [0003 -- read-only and staleness contract](0003-read-only-and-staleness-contract.md) | proposed | Read commands never write; staleness is reported in text and JSON as fresh, stale or unknown and repaired only on an explicit `--refresh`; a failed query exits non-zero; an unscoped query resolves one project, attempts one embed and stops at a wall budget. |

A successor set covers the edit plan, the filekeeper apply path, the language-server provider, the MCP decision and the naming question. Those records are not written yet and nothing here decides them.

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
