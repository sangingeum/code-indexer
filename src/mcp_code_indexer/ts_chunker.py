"""Tree-sitter AST chunker (M4): semantic chunks per function/class.

Feasibility verified on this box: py3.11, tree-sitter 0.26.0,
tree-sitter-language-pack 1.18.0, numpy 1.26.4 (<2 satisfied), pure wheels,
python grammar parses. Falls back to the regex chunker on any failure.
"""

from __future__ import annotations

import hashlib
import logging

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
_UNIT_TYPES = {
    "function_definition", "function_declaration", "method_definition",
    "class_definition", "class_declaration", "struct_item", "impl_item",
    "export_statement", "decorated_definition",
}
_CHAR_CAP = 1000


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
        tree = parser.parse(text.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("tree-sitter parse failed for %s (%s): %s", path, lang, exc)
        return fallback_chunk(text)

    chunks: list[Chunk] = []
    root = tree.root_node
    if root.has_error:
        logger.debug("tree-sitter error-tolerant parse for %s — using AST anyway", path)
    _walk(root, text, chunks)
    if not chunks:
        return fallback_chunk(text)
    for i, c in enumerate(chunks):
        c.chunk_index = i
    return chunks


def _walk(node, text: str, out: list[Chunk]) -> None:
    """Collect declaration units; recurse into non-unit containers."""
    for child in node.children:
        if child.type in _UNIT_TYPES:
            start = child.start_point[0] + 1
            end = child.end_point[0] + 1  # end_point row is 0-based
            node_text = text[child.start_byte:child.end_byte]
            symbol = _symbol_from_node(child, text)
            if len(node_text) > _CHAR_CAP:
                # Oversized unit: split via fallback on its own text,
                # adjusting line numbers and preserving symbol on the first.
                subs = fallback_chunk(node_text)
                offset = start
                for s in subs:
                    out.append(Chunk(
                        text=s.text, chunk_hash=s.chunk_hash,
                        symbol=symbol if s.start_line == 1 else s.symbol,
                        start_line=offset + s.start_line - 1,
                        end_line=offset + s.end_line - 1,
                        chunk_index=0,
                    ))
                    offset += s.end_line - s.start_line + 1
            else:
                h = hashlib.sha256(node_text.encode()).hexdigest()
                out.append(Chunk(
                    text=node_text, chunk_hash="sha256:" + h, symbol=symbol,
                    start_line=start, end_line=end, chunk_index=0,
                ))
        elif child.child_count:
            _walk(child, text, out)


def _symbol_from_node(node, text: str) -> str | None:
    # First named child of type identifier/name is the declaration name.
    for ch in node.children:
        if ch.type in ("identifier", "name", "property_identifier", "type_identifier"):
            return text[ch.start_byte:ch.end_byte]
    # Fallback: regex on the first line.
    first_line = text[node.start_byte:node.end_byte].splitlines()[0] \
        if node.end_byte > node.start_byte else ""
    return _guess_symbol(first_line)