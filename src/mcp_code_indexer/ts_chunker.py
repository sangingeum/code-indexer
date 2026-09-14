"""Tree-sitter AST chunker (M4): semantic chunks per function/class.

Feasibility verified on this box: py3.11, tree-sitter 0.26.0,
tree-sitter-language-pack 1.18.0, numpy 1.26.4 (<2 satisfied), pure wheels,
python grammar parses. Falls back to the regex chunker on any failure.

Also extracts the symbol table + reference edges (plan v2 §4/§5) in the
same AST walk. Namespaces are treated as containers (recurse inside, emit a
symbol row without swallowing their children); enums are units.
"""

from __future__ import annotations

import hashlib
import logging
import re

from .chunker import Chunk, chunk as fallback_chunk, _guess_symbol

logger = logging.getLogger("mcp-code-indexer.tschunker")

try:
    import tree_sitter_language_pack
    _TS_OK = True
except Exception:  # noqa: BLE001
    _TS_OK = False

EXT_LANG = {
    "py": "python", "js": "javascript", "jsx": "javascript", "ts": "typescript",
    "tsx": "typescript", "rs": "rust", "go": "go", "java": "java", "c": "c",
    "cpp": "cpp", "cc": "cpp", "h": "c", "hpp": "cpp", "cs": "csharp",
    "rb": "ruby", "php": "php", "sh": "bash", "lua": "lua", "swift": "swift",
    "kt": "kotlin", "md": "markdown", "json": "json", "yaml": "yaml",
    "yml": "yaml", "toml": "toml", "html": "html", "css": "css",
}

# Node types treated as one chunk (top-level declarations).
# NOTE: namespaces are deliberately NOT here — they are containers
# (see _UNIT_TYPES_EXTRACT_ONLY); making them chunks would hide every
# inner symbol from chunking.
_UNIT_TYPES = {
    "function_definition", "function_declaration", "method_definition",
    "class_definition", "class_declaration", "class_specifier",
    "struct_item", "struct_specifier", "impl_item",
    "export_statement", "decorated_definition", "enum_specifier",
    "preproc_function_def",
}
# Container-ish declarations that still get a symbol row (walk recurses in).
_UNIT_TYPES_EXTRACT_ONLY = {
    "namespace_definition", "namespace_definition_probability",
}
_TYPE_BY_NODE = {
    "function_definition": "function", "function_declaration": "function",
    "method_definition": "method", "preproc_function_def": "function",
    "class_definition": "class", "class_declaration": "class",
    "class_specifier": "class",
    "struct_item": "struct", "struct_specifier": "struct",
    "impl_item": "class", "enum_specifier": "enum",
    "namespace_definition": "namespace",
}
_CHAR_CAP = 1000

# Node types whose text names a call target (textual, unbound).
_CALL_NODE_TYPES = {
    "call_expression", "call", "call_function", "function_call",
    "call_function_expression",
}


def chunk_text(path: str, text: str) -> list[Chunk]:
    """AST chunk via tree-sitter; falls back to regex chunker on failure."""
    if not _TS_OK:
        return fallback_chunk(text)
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    lang = EXT_LANG.get(ext)
    if lang is None:
        return fallback_chunk(text)
    try:
        parser = tree_sitter_language_pack.get_parser(lang)
        data = text.encode("utf-8")
        tree = parser.parse(data)
    except Exception as exc:  # noqa: BLE001
        logger.debug("tree-sitter parse failed for %s (%s): %s", path, lang, exc)
        return fallback_chunk(text)

    chunks: list[Chunk] = []
    root = tree.root_node
    if root.has_error:
        logger.debug("tree-sitter error-tolerant parse for %s — using AST anyway", path)
    # tree-sitter byte offsets index the ENCODED buffer, not the str. Slicing
    # the str with byte offsets corrupts any name after multi-byte chars.
    # Decode each slice from the bytes buffer instead.
    _walk(root, data, chunks)
    if not chunks:
        return fallback_chunk(text)
    for i, c in enumerate(chunks):
        c.chunk_index = i
        c.source = "ast"
    return chunks


def _walk(node, data: bytes, out: list[Chunk]) -> None:
    """Collect declaration units; recurse into non-unit containers."""
    text = None  # decoded lazily only when needed
    for child in node.children:
        if child.type in _UNIT_TYPES:
            start = child.start_point[0] + 1
            end = child.end_point[0] + 1  # end_point row is 0-based
            node_text = data[child.start_byte:child.end_byte].decode("utf-8", "replace")
            symbol = _symbol_from_node(child, data)
            sym_type = _TYPE_BY_NODE.get(child.type)
            if len(node_text) > _CHAR_CAP:
                # Oversized unit: split via fallback on its own text,
                # adjusting line numbers and preserving symbol on the first.
                # source stays 'ast': the unit boundary is AST-derived; only
                # the split is mechanical (design round-1 decision).
                subs = fallback_chunk(node_text)
                offset = start
                for s in subs:
                    out.append(Chunk(
                        text=s.text, chunk_hash=s.chunk_hash,
                        symbol=symbol if s.start_line == 1 else s.symbol,
                        start_line=offset + s.start_line - 1,
                        end_line=offset + s.end_line - 1,
                        chunk_index=0, source="ast", symbol_type=sym_type,
                    ))
                    offset += s.end_line - s.start_line + 1
            else:
                h = hashlib.sha256(node_text.encode()).hexdigest()
                out.append(Chunk(
                    text=node_text, chunk_hash="sha256:" + h, symbol=symbol,
                    start_line=start, end_line=end, chunk_index=0,
                    source="ast", symbol_type=sym_type,
                ))
        elif child.type in _UNIT_TYPES_EXTRACT_ONLY:
            # Namespace: emit a symbol row via the chunk that _make_chunk
            # would produce ONLY when the body is small; always recurse.
            if symbol := _symbol_from_node(child, data):
                start = child.start_point[0] + 1
                end = child.end_point[0] + 1
                node_text = data[child.start_byte:child.end_byte].decode("utf-8", "replace")
                if len(node_text) <= _CHAR_CAP:
                    h = hashlib.sha256(node_text.encode()).hexdigest()
                    out.append(Chunk(
                        text=node_text, chunk_hash="sha256:" + h, symbol=symbol,
                        start_line=start, end_line=end, chunk_index=0,
                        source="ast", symbol_type="namespace",
                    ))
            _walk(child, data, out)
        elif child.child_count:
            _walk(child, data, out)
    del text


def _symbol_from_node(node, data: bytes) -> str | None:
    # First named child of type identifier/name is the declaration name.
    for ch in node.children:
        if ch.type in ("identifier", "name", "property_identifier", "type_identifier"):
            return data[ch.start_byte:ch.end_byte].decode("utf-8", "replace")
    # Python wraps classes/functions in decorated_definition when a decorator
    # is present: descend into the wrapped unit for the real name.
    if node.type == "decorated_definition":
        for ch in node.children:
            if ch.type in _UNIT_TYPES:
                return _symbol_from_node(ch, data)
    # C/C++ grammars nest the name: function_definition → function_declarator
    # → qualified_identifier → identifier; class_specifier → type_identifier
    # is a direct child (covered above); namespace → namespace_identifier.
    for ch in node.children:
        if ch.type in ("function_declarator",):
            return _symbol_from_declarator(ch, data)
        if ch.type in ("namespace_identifier",):
            return data[ch.start_byte:ch.end_byte].decode("utf-8", "replace")
    # Fallback: regex on the first line.
    first_line = data[node.start_byte:node.end_byte].decode("utf-8", "replace") \
        .splitlines()[0] if node.end_byte > node.start_byte else ""
    return _guess_symbol(first_line)


def _symbol_from_declarator(node, data: bytes) -> str | None:
    """Name inside a function_declarator: prefer the LAST identifier part of
    a qualified_identifier (Foo::bar → 'bar'), else the plain identifier."""
    for ch in node.children:
        if ch.type == "qualified_identifier":
            parts = [c for c in ch.children
                     if c.type in ("identifier", "namespace_identifier",
                                   "destructor_name", "operator_name")]
            if parts:
                return data[parts[-1].start_byte:parts[-1].end_byte].decode("utf-8", "replace")
        if ch.type in ("identifier", "field_identifier", "destructor_name"):
            return data[ch.start_byte:ch.end_byte].decode("utf-8", "replace")
    return None


# ---------------------------------------------------------------------------
# Symbol table + reference extraction (plan v2 §4/§5). Textual and unbound;
# every consumer labels these heuristic.
# ---------------------------------------------------------------------------

def extract_symbols(path: str, text: str, chunks: list[Chunk]) -> list[dict]:
    """Symbol rows derived from the chunk pass itself.

    Returns [{'name', 'symbol_type', 'start_line', 'end_line', 'source'}].
    Chunks carry the type; regex-chunked files get function/class guesses.
    """
    rows: dict[tuple, dict] = {}
    for c in chunks:
        if not c.symbol:
            continue
        stype = c.symbol_type or _guess_type_regex(c.text) or "function"
        key = (c.symbol, c.start_line)
        if key not in rows:
            rows[key] = {
                "name": c.symbol, "symbol_type": stype,
                "start_line": c.start_line, "end_line": c.end_line,
                "source": c.source,
            }
    return list(rows.values())


_CALL_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_:<>,~]*)\s*\(")
_INCLUDE_RE = re.compile(r"^\s*#\s*include\s+[<\"]([^>\"]+)[>\"]",
                         re.MULTILINE)
_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))",
                        re.MULTILINE)


def extract_refs(path: str, text: str) -> list[dict]:
    """Textual reference edges: calls, includes/imports.

    Unbound by design (no compiler resolution, plan §5): every edge is a
    textual match and consumers label it heuristic. Regex-based so it works
    for all languages without per-grammar node maps.
    """
    lines = text.splitlines()
    refs: list[dict] = []
    for m in _INCLUDE_RE.finditer(text):
        line = text[:m.start()].count("\n") + 1
        refs.append({"line": line, "src_symbol": None,
                     "relationship": "includes", "target": m.group(1)})
    for m in _IMPORT_RE.finditer(text):
        line = text[:m.start()].count("\n") + 1
        target = m.group(1) or m.group(2)
        refs.append({"line": line, "src_symbol": None,
                     "relationship": "includes", "target": target})
    for lineno, ln in enumerate(lines, 1):
        for m in _CALL_RE.finditer(ln):
            name = m.group(1).split("::")[-1].split("->")[-1].split(".")[-1]
            if name and name.isidentifier():
                refs.append({"line": lineno, "src_symbol": None,
                             "relationship": "calls", "target": name})
    return refs


def _guess_type_regex(text: str) -> str | None:
    first = text.lstrip()
    if first.startswith(("class ", "struct ", "interface ")):
        return "class"
    if first.startswith("enum "):
        return "enum"
    if first.startswith(("namespace ",)):
        return "namespace"
    if first.startswith(("def ", "fn ", "function ", "func ")):
        return "function"
    return None
