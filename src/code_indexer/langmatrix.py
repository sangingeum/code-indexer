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

import os

from . import ts_chunker
from .graph_extract import LANGUAGES_WITH_AST, resolve_imports

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
}

# tree-sitter pack name -> representative extension.
_LANG_EXT: dict[str, str] = {
    "python": "py", "javascript": "js", "typescript": "ts", "rust": "rs",
    "go": "go", "java": "java", "c": "c", "cpp": "cpp", "csharp": "cs",
    "ruby": "rb", "php": "php", "kotlin": "kt", "bash": "sh", "lua": "lua",
    "swift": "swift",
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
    """EXT_LANG languages WITHOUT AST references (reported in index-status)."""
    return sorted(set(EXT_LANG_NAMES) - LANGUAGES_WITH_AST)


EXT_LANG_NAMES = sorted(set(ts_chunker.EXT_LANG.values()))


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