# Language coverage matrix

Generated from the code's own capability probe (do not edit by hand:
regenerate with `python -m code_indexer.langmatrix`). What each
supported language gets from the pipeline:

| language | ext | AST chunking | symbols | signatures | visibility | references | imports resolution | notes |
|---|---|---|---|---|---|---|---|---|
| bash | sh | yes | no | no | no | heuristic | unresolved (no resolver) | grammar present but symbol extraction is weak |
| c | c | yes | yes | yes | yes | ast | quoted includes relative to the file; angled unresolved | a header spelled .h is cpp, not this row |
| cpp | cpp | yes | yes | yes | yes | ast | quoted includes relative to the file; angled unresolved | .h headers map here too; cpp parses a C header |
| csharp | cs | yes | yes | yes | yes | ast | unresolved (no resolver) | — |
| csproj | csproj | no | no | no | no | none | unresolved (no resolver) | regex chunker fallback |
| csx | csx | no | no | no | no | heuristic | unresolved (no resolver) | regex chunker fallback |
| gitignore | gitignore | no | no | no | no | none | unresolved (no resolver) | regex chunker fallback |
| go | go | yes | yes | yes | yes | ast | go.mod module prefix, then package dir | — |
| java | java | yes | yes | yes | yes | heuristic | unresolved (no resolver) | — |
| javascript | js | yes | yes | yes | yes | ast | relative + index augmentation; packages unresolved | — |
| kotlin | kt | yes | yes | yes | yes | heuristic | unresolved (no resolver) | — |
| log | log | no | no | no | no | none | unresolved (no resolver) | regex chunker fallback |
| lua | lua | yes | yes | yes | yes | heuristic | unresolved (no resolver) | — |
| php | php | yes | yes | yes | yes | heuristic | unresolved (no resolver) | — |
| props | props | no | no | no | no | none | unresolved (no resolver) | regex chunker fallback |
| proto | proto | no | no | no | no | heuristic | unresolved (no resolver) | regex chunker fallback |
| python | py | yes | yes | yes | yes | ast | relative + absolute (root/src, dir or __init__.py) | — |
| ruby | rb | no | no | no | no | heuristic | unresolved (no resolver) | regex chunker fallback despite grammar mapping |
| rust | rs | yes | yes | yes | yes | heuristic | unresolved (no resolver) | — |
| sln | sln | no | no | no | no | none | unresolved (no resolver) | regex chunker fallback |
| swift | swift | yes | yes | yes | yes | heuristic | unresolved (no resolver) | — |
| text | txt | no | no | no | no | none | unresolved (no resolver) | regex chunker fallback |
| typescript | ts | yes | yes | yes | yes | ast | relative + index augmentation; packages unresolved | — |

Legend:

- AST chunking: tree-sitter unit chunking (statement-boundary split
  for oversize units); `no` means the window/regex chunker handles
  the file (chunks still embed and search fine).
- references `ast`: tree-sitter queries (queries/<lang>/references.scm),
  comments/strings excluded. `heuristic` is the textual scan the other
  indexed languages take (comments/strings count). `none` means the
  format stores no reference rows at all — the prose and configuration
  formats below. The cell is what the indexer's one reference writer
  actually does for that language.
- symbols: ONLY a declaration parsed from source with an AST enters
  the symbol table. A text window's first line is not a declaration,
  so a language whose chunker is the regex fallback (ruby) yields no
  symbol rows, and neither does an unparsed language such as proto.
  Every symbol row is therefore `evidence=ast`: `evidence=unknown` no
  longer describes any symbol row.
- prose and configuration (codeindexignore, csproj, css, gitignore, html, json, log, markdown, props, sln, text, toml, yaml)
  are indexed for SEARCH ONLY: chunks for semantic search and a
  file row, with no symbols and no references. What the class tests
  is no SYMBOL reader and no reference the index RESOLVES -- the
  formats carry reference-shaped text (a json `$ref`, an html href,
  a css `var(--x)`), and none of it names a declaration this index
  holds, so a stored row would be a guess the symbol table cannot
  honour. `find-definition`
  cannot answer for anything in them, and says so through the empty-
  answer reason rather than returning prose as a definition.
- an unparsed language is one with no symbol reader, NOT one the index
  refuses: proto and csx files are indexed and searchable, and answer
  nothing for `find-definition`. `index-status` lists every such
  language. Semantic search over the file's chunks is what remains —
  the text is indexed and reachable — and `find-definition` answers
  empty with the files-not-parsed (or language-without-ast) reason
  rather than as a negative.
- visibility is real only where rules exist (python underscore,
  rust pub, csharp modifiers); C/C++/Java default public.
- Unsupported languages fall back cleanly to the window chunker and
  still index/search; index-status reports them via
  languages_without_ast().

