# ADR-0010: the parse verdict describes the parser

- Status: accepted
- Date: 2026-11-08
- Deciders: architecture review (ruling), owner ratification 2026-11-08
- Supersedes / Superseded by: supersedes ADR-0004 section 7's closing example and its "in the index as well" sentence; not the section's rule

## Decision in one paragraph

The parse verdict reports what the parser did, and only the absence of an AST is `parse_unavailable`. A file the parser read and found no declarations in is `parsed`, and the single fallback chunk it carries for lexical search is a search artifact, not a parse claim; the empty-grammar, no-grammar-for-the-extension and parser-exception paths keep `parse_unavailable` and keep blaming the missing reader.

## Context

1. **A false fact was stored.** A file whose AST declares nothing -- an import-only module is the shape -- parsed cleanly and yielded no chunk, and the verdict was taken from the chunk yield. The manifest then held `parse_unavailable` beside `status=ok` and `chunk_count=1` for the same file: two columns of one row contradicting each other, and the column a reader is told to trust for coverage saying the opposite of what the reader did.
2. **The consequence was a coverage count nobody could act on.** `index-status` reported "1 of 2 files parsed" with `parse_error 0` and `parse_error_files` empty, so the count named a file the indexer had read as one it had failed to read, and no reader could learn which file it was or why. Every absence claim in such a project then carried a caveat about a reader that exists.
3. **The measurement, before and after.** The round's verification recorded the file stored `parse_unavailable` before the fix and `parsed` after it, with the project's `coverage.complete` moving from false to true (`runs/reverify-r2`, the read coverage and parse-verdict scratches). On this build the same input is reproducible without an index: `parse_file_with_language("only_imports.py", "import os\nimport sys\n")` -> `verdict=parsed chunks=1 lang=python`, the single chunk carrying `source=regex` and no symbol.
4. **ADR-0004 section 7's rule is not what this record changes.** That section exists to say the verdict is the parser's own result and never the chunker's, that the syntax verdict reads `has_error` and `is_missing` directly, and that `parse_unavailable` is a failed claim rather than a pass. All of that holds, and the narrowing below is what makes it true rather than what weakens it. The sibling clause in ADR-0005 (`0005-filekeeper-apply-and-recovery.md:94`: `parse_unavailable` when the file's language has no tree-sitter grammar) is accurate as written and is not touched.
5. **The disagreement this record records.** Section 7's closing example -- "a file that went through the regex fallback reports `parse_unavailable`, never `parsed`" -- and the "In the index as well" sentence -- "`index-status` ... never reports a file as parsed on the strength of the chunker's fallback" -- were both written against a chunker whose verdict WAS the chunk yield. They described that chunker correctly and describe the parser wrongly: the fallback chunk is produced for a file the parser read successfully, so the two facts are not in conflict and neither is a claim about the parse. A record that kept the sentences would force the index to misreport a file it read, which is the class of defect section 7 exists to prevent.

## Decision

1. **The three verdicts mean what they name.** `parsed` -- the parser produced a tree and the tree carries neither `has_error` nor a missing node. `parse_error` -- it produced a tree that carries a defect. `parse_unavailable` -- there is no tree at all: no grammar pack, no grammar for the extension, or a parser exception.
2. **An empty AST is `parsed`.** A tree that declares no unit is a successful parse of a file with nothing to declare. The file stores its verdict as `parsed`.
3. **The fallback chunk is a search artifact.** Such a file still carries exactly one chunk -- the regex chunker's text window -- so its text stays searchable by lexical and semantic search. That chunk is not evidence about the parse and no verdict is read from it. Chunk `source` records `regex` for it, so a reader can tell it apart from an AST chunk.
4. **Coverage and absence claims read the verdict, not the chunk yield.** `index-status` counts the three verdicts; `find-*`'s coverage block and its absence sentences read those counts. A project whose files all parsed may say a name is absent; a project with an unparsed file may not.
5. **The two sentences named in the header are superseded; the section's rule is not.** This record is the answer to the question section 7's example raised, and ADR-0004 remains the record for what a plan's validation rung may claim.

## Requirements this record resolves

- The requirement that a file the reader read, with nothing in it to declare, stops being reported as a file the tool could not read, and the follow-up record that requirement falsified. The falsified sentences are named in the header; the rule they were attached to survives.
- The JSON contract for the counts: `docs/json-schema.md` states beside the `parse_*` counts that `parsed` also covers a file whose AST declares nothing, and that such a file carries one fallback chunk for lexical search.

## Alternatives considered, and why rejected

- **Amend ADR-0004 section 7 in place.** The set's convention decides this: after acceptance, a correction that changes what a record decides is a superseding record, and typos and citation corrections are the only edits made in place (`docs/adr/README.md`, Amendment). ADR-0004 is accepted and owner-ratified, the code cites section 7 by name, and this narrows what `parsed` promises -- not a typo. Annotating the section in place would leave two answers to one question with no ratified one.
- **Keep the sentences and make an empty-AST file report `parse_unavailable`.** That is the false fact this record removes: it tells a reader a reader is missing when the only thing missing is a declaration, and it turns a complete index's absence claim into a caveated one for a reason the user cannot see.
- **Report an empty-AST file as `parse_error`.** Worse than the above: the tree carries no defect, so the verdict would assert one.
- **Drop the fallback chunk so the chunk yield and the verdict agree.** The agreement would be bought by making the file unsearchable, which trades a reporting defect for a recall loss.

## Consequences

- A reader can no longer take `parse_unavailable` to mean "this tool produced no chunk for the file", and does not need to: the chunk a file carries is a search artifact and is labelled by `source`.
- `files_not_parsed`, `parse_error_files` and every absence sentence describe the parse, so a project of import-only modules reports complete coverage and may say a name is absent.
- A file with no grammar (proto, csx, prose formats) still reports `parse_unavailable` when the language has no AST reader, and the absence sentence still names `language-without-ast` rather than a parse that ran.
- Tests: an empty-AST file is `parsed` with one regex chunk, and this is pinned in `tests/test_parse_verdict.py` (`test_a_file_with_no_declarations_is_parsed_not_unavailable`).

## What this record does not decide

- The chunker's chunk yield: how many chunks a file produces, the chunk count, and the `chunk_count` column's semantics are unchanged. A file may carry a chunk and a verdict independently.
- The `empty_reason` vocabulary (`docs/json-schema.md:388-394`), including `language-without-ast` and `files-not-parsed`. That vocabulary is unchanged by this record; it is the surface on which the corrected verdict is read.
- Which languages have a grammar, and what a plan's syntax rung may claim -- ADR-0004 section 7's rule and ADR-0005's apply contract stand, and this record narrows only the example and the "in the index as well" sentence named in the header.
