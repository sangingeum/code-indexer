# Language coverage matrix

Generated from the code's own capability probe (do not edit by hand:
regenerate with `python -m code_indexer.langmatrix`). What each
supported language gets from the pipeline:

| language | ext | AST chunking | symbols | signatures | visibility | references | imports resolution | notes |
|---|---|---|---|---|---|---|---|---|
| bash | sh | yes | no | no | no | none | unresolved (no resolver) | grammar present but symbol extraction is weak |
| c | c | yes | yes | yes | yes | ast | quoted includes relative to the file; angled unresolved | — |
| cpp | cpp | yes | yes | yes | yes | ast | quoted includes relative to the file; angled unresolved | — |
| csharp | cs | yes | yes | yes | yes | none | unresolved (no resolver) | — |
| go | go | yes | yes | yes | yes | ast | go.mod module prefix, then package dir | — |
| java | java | yes | yes | yes | yes | none | unresolved (no resolver) | — |
| javascript | js | yes | yes | yes | yes | ast | relative + index augmentation; packages unresolved | — |
| kotlin | kt | yes | yes | yes | yes | none | unresolved (no resolver) | — |
| lua | lua | yes | yes | yes | yes | none | unresolved (no resolver) | — |
| php | php | yes | yes | yes | yes | none | unresolved (no resolver) | — |
| python | py | yes | yes | yes | yes | ast | relative + absolute (root/src, dir or __init__.py) | — |
| ruby | rb | no | yes | yes | yes | none | unresolved (no resolver) | regex chunker fallback despite grammar mapping |
| rust | rs | yes | yes | yes | yes | none | unresolved (no resolver) | — |
| swift | swift | yes | yes | yes | yes | none | unresolved (no resolver) | — |
| typescript | ts | yes | yes | yes | yes | ast | relative + index augmentation; packages unresolved | — |

Legend:

- AST chunking: tree-sitter unit chunking (statement-boundary split
  for oversize units); `no` means the window/regex chunker handles
  the file (chunks still embed and search fine).
- references `ast`: tree-sitter queries (queries/<lang>/references.scm),
  comments/strings excluded. Anything else that extracts references
  does it textually (heuristic; comments/strings count).
- visibility is real only where rules exist (python underscore,
  rust pub, csharp modifiers); C/C++/Java default public.
- Unsupported languages fall back cleanly to the window chunker and
  still index/search; index-status reports them via
  languages_without_ast().
