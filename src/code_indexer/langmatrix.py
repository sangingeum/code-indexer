"""Per-language capability matrix (language-coverage work item).

Single source of truth for what each supported language actually gets:
AST chunking, symbol extraction, signatures/visibility, references
(ast/heuristic), import resolution. ``capabilities()`` PROBES each language
with tiny fixtures at runtime (no hardcoded claims that rot); docs/languages.md
and the snapshot tests render the same table.

Empirical facts baked into the fixtures (audit §6):
- bash: maps to a grammar but symbol extraction is weak (no symbol rows).
- ruby: maps to a grammar but chunking falls back to regex.
- visibility is real only where the grammar+rules support it
  (python underscore rule, rust pub, csharp access modifiers; C/C++/Java
  default public).
- references/imports: AST via queries/<lang>/references.scm for
  LANGUAGES_WITH_AST (graph_extract); textual fallback (heuristic) otherwise.
"""

from __future__ import annotations


from . import ts_chunker
from .graph_extract import LANGUAGES_WITH_AST

# Formats the index takes for SEARCH ONLY. Their content is prose or
# configuration: there is no declaration in them to parse and no name
# reference to resolve, so they contribute chunks (semantic search) and a
# file row, and nothing to the symbol or reference tables. This is the
# accuracy ruling of 2026-11-08: the symbol table is an authority other
# agents act on, and a markdown heading lifted into it is a phantom
# definition that find-definition answers for as though it were code.
PROSE_OR_CONFIG_LANGUAGES = frozenset(
    {"markdown", "text", "log", "json", "yaml", "toml", "html", "css"})


def is_prose_or_config(language: str | None) -> bool:
    """True when ``language`` is prose or configuration, not source.

    Such a file is indexed for search and answers no symbol or reference
    question. An unknown or absent language is NOT prose: nothing is claimed
    about it either way, and the AST-only symbol rule already keeps it out of
    the symbol table.
    """
    return bool(language) and language.lower() in PROSE_OR_CONFIG_LANGUAGES

# Fixture snippets per EXT_LANG language name (tree-sitter pack names).
_FIXTURES: dict[str, str] = {
    "python": "def greet(name):\n    return name\n",
    "javascript": "function greet(name) { return name; }\n",
    "typescript": "export function greet(name: string): string {\n"
                  "  return name;\n}\n",
    "rust": "pub fn greet(name: &str) -> &str {\n    name\n}\n",
    "go": "func Greet(name string) string {\n    return name\n}\n",
    "java": "public class Greeter {\n    public String greet() {\n"
            "        return \"hi\";\n    }\n}\n",
    "c": "int add(int a, int b) {\n    return a + b;\n}\n",
    "cpp": "class Greeter {\npublic:\n    int greet() { return 1; }\n};\n",
    "csharp": "public class Greeter {\n    public int Greet() { return 1; }\n}\n",
    "ruby": "def greet(name)\n  name\nend\n",
    "php": "<?php\nfunction greet($name) {\n    return $name;\n}\n",
    "kotlin": "fun greet(name: String): String {\n    return name\n}\n",
    "bash": "greet() {\n    echo \"$1\"\n}\n",
    "lua": "function greet(name)\n    return name\nend\n",
    "swift": "func greet(name: String) -> String {\n    return name\n}\n",
    # Formats the index takes for search only. There is no symbol reader to
    # probe, so the probe's answer (no symbols, no AST chunks) is the honest
    # one: the fixtures exist so the matrix states it rather than omitting
    # the row.
    "proto": 'syntax = "proto3";\nmessage Greeting {\n  string name = 1;\n}\n',
    "text": "a plain text note (about a function)\n",
    "log": "2026-11-08 registry: started\n",
}

# tree-sitter pack name -> representative extension.
_LANG_EXT: dict[str, str] = {
    "python": "py", "javascript": "js", "typescript": "ts", "rust": "rs",
    "go": "go", "java": "java", "c": "c", "cpp": "cpp", "csharp": "cs",
    "ruby": "rb", "php": "php", "kotlin": "kt", "bash": "sh", "lua": "lua",
    "swift": "swift", "proto": "proto", "text": "txt", "log": "log",
}


def capabilities(lang: str) -> dict:
    """Probe one language: what the pipeline actually produces for it.

    Returns {'language', 'ext', 'ast_chunking', 'symbols', 'signatures',
    'visibility', 'references', 'imports_resolution', 'notes'}.
    """
    text = _FIXTURES.get(lang)
    ext = _LANG_EXT.get(lang)
    caps = {
        "language": lang, "ext": ext, "ast_chunking": False,
        "symbols": False, "signatures": False, "visibility": False,
        "references": "heuristic" if lang in LANGUAGES_WITH_AST else "none",
        "imports_resolution": _resolution_note(lang),
        "notes": "",
    }
    if lang in LANGUAGES_WITH_AST:
        caps["references"] = "ast"
    if text is None or ext is None:
        caps["notes"] = "unsupported: window chunker fallback only"
        return caps
    path = f"fixture.{ext}"
    try:
        chunks = ts_chunker.chunk_text(path, text)
    except Exception:  # noqa: BLE001
        chunks = []
    ast_chunks = [c for c in chunks if c.source == "ast"]
    if ast_chunks:
        caps["ast_chunking"] = True
    syms = ts_chunker.extract_symbols(path, text, chunks) if chunks else []
    if syms:
        caps["symbols"] = True
        if any(s["signature"] for s in syms):
            caps["signatures"] = True
        if any(s["visibility"] for s in syms):
            caps["visibility"] = True
    # Empirical notes (audit §6): grammar present but extraction limited.
    if lang == "bash" and not syms:
        caps["notes"] = "grammar present but symbol extraction is weak"
    elif lang == "ruby" and not ast_chunks:
        caps["notes"] = "regex chunker fallback despite grammar mapping"
    elif chunks and not ast_chunks and not caps["notes"]:
        caps["notes"] = "regex chunker fallback"
    return caps


def _resolution_note(lang: str) -> str:
    notes = {
        "python": "relative + absolute (root/src, dir or __init__.py)",
        "typescript": "relative + index augmentation; packages unresolved",
        "javascript": "relative + index augmentation; packages unresolved",
        "tsx": "relative + index augmentation; packages unresolved",
        "c": 'quoted includes relative to the file; angled unresolved',
        "cpp": 'quoted includes relative to the file; angled unresolved',
        "go": "go.mod module prefix, then package dir",
    }
    return notes.get(lang, "unresolved (no resolver)")


def matrix() -> list[dict]:
    """The full table (sorted by language), ready for docs rendering."""
    langs = sorted(set(_LANG_EXT) | set(_FIXTURES))
    return [capabilities(lang) for lang in langs]


def languages_without_ast() -> list[str]:
    """Languages the registry knows that yield no AST references.

    Read from the same table ``matrix()`` renders, so the answer and the
    documented matrix cannot drift: every language the matrix probes or maps,
    minus the ones with AST reference queries. ``proto`` is here because a
    .proto file has a grammar in the pack yet no symbol or reference reader,
    so ``find-definition`` cannot answer inside one.
    """
    known = ({row["language"] for row in matrix()}
             | set(EXT_LANG_NAMES)
             | set(DECLARED_WITHOUT_READER))
    return sorted(known - LANGUAGES_WITH_AST)


EXT_LANG_NAMES = sorted(set(ts_chunker.EXT_LANG.values()))

# Languages the index takes with no AST reader of any kind. Declared rather
# than probed, because there is nothing to probe: no symbol reader exists, so
# the honest statement is the declaration itself. Kept beside the matrix so a
# reader of the registry learns why a .proto file answers no definition.
DECLARED_WITHOUT_READER: dict[str, str] = {
    "proto": "indexed for search; no symbol or reference reader, so "
             "find-definition cannot answer inside a .proto file",
}


def render_markdown() -> str:
    """docs/languages.md body from the live table."""
    rows = matrix()
    lines = [
        "# Language coverage matrix",
        "",
        "Generated from the code's own capability probe (do not edit by hand:",
        "regenerate with `python -m code_indexer.langmatrix`). What each",
        "supported language gets from the pipeline:",
        "",
        "| language | ext | AST chunking | symbols | signatures | visibility"
        " | references | imports resolution | notes |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        yes = lambda b: "yes" if b else ("no" if b is False else "—")
        lines.append(
            f"| {r['language']} | {r['ext'] or '—'} | {yes(r['ast_chunking'])}"
            f" | {yes(r['symbols'])} | {yes(r['signatures'])} "
            f"| {yes(r['visibility'])} | {r['references']} "
            f"| {r['imports_resolution']} | {r['notes'] or '—'} |")
    lines += [
        "",
        "Legend:",
        "",
        "- AST chunking: tree-sitter unit chunking (statement-boundary split",
        "  for oversize units); `no` means the window/regex chunker handles",
        "  the file (chunks still embed and search fine).",
        "- references `ast`: tree-sitter queries (queries/<lang>/references.scm),",
        "  comments/strings excluded. Anything else that extracts references",
        "  does it textually (heuristic; comments/strings count).",
        "- symbols: ONLY a declaration parsed from source with an AST enters",
        "  the symbol table. A text window's first line is not a declaration,",
        "  so a language whose chunker is the regex fallback (ruby) yields no",
        "  symbol rows, and neither does an unparsed language such as proto.",
        "- prose and configuration (markdown, text, log, json, yaml, toml, html,",
        "  css) are indexed for SEARCH ONLY: chunks for semantic search and a",
        "  file row, with no symbols and no references. `find-definition`",
        "  cannot answer for anything in them, and says so through the empty-",
        "  answer reason rather than returning prose as a definition.",
        "- an unparsed language is one with no symbol reader, NOT one the index",
        "  refuses: proto files are indexed and searchable, and answer nothing",
        "  for `find-definition`. `index-status` lists every such language.",
        "- visibility is real only where rules exist (python underscore,",
        "  rust pub, csharp modifiers); C/C++/Java default public.",
        "- Unsupported languages fall back cleanly to the window chunker and",
        "  still index/search; index-status reports them via",
        "  languages_without_ast().",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    print(render_markdown())