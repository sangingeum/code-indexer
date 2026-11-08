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

from .chunker import (Chunk, chunk as fallback_chunk, declaration_hash,
                      _guess_symbol, split_lines)

logger = logging.getLogger("code-indexer.tschunker")

# Version of the chunking behavior (unit types, signature extraction, split
# rules). Recorded in the manifest fingerprint (see fingerprint.py); bump
# whenever a change alters which chunks/symbols a file produces, so existing
# indexes are flagged for reindex instead of silently mixing old and new
# chunks. Cosmetic changes that never alter output do not need a bump.
# v2: an oversized unit's split pieces carry the file lines they came from.
# A split piece's line inside the unit is already 1-based, so its file line is
# `unit_start + piece_start - 1` -- there is no running offset to add. v1
# counted the unit's start twice and drifted by each piece's length, so a
# split declaration reported a range past EOF. Same text, different ranges:
# rows built by v1 describe an index this build would not produce.
# v3: `.h` headers chunk with the cpp grammar (v2 read them as C), so a C++
# header's chunks describe declarations v2 never saw.
# v4: a `.h` header's grammar is decided from the file itself (C first, cpp
# when C cannot read it), so a C-only header's chunks again come from the C
# grammar -- v3 read every `.h` as cpp and lost a K&R definition.
CHUNKER_VERSION = 4

try:
    import tree_sitter_language_pack
    _TS_OK = True
except Exception:  # noqa: BLE001
    _TS_OK = False

# The one header spelling whose language its name cannot settle: C and C++
# projects both ship `.h`. The map keeps the family default (`cpp`) for the
# callers that see only a path, but a file's own bytes decide the grammar in
# `parse_tree_with_language` -- see `_choose_grammar`. `.hpp/.hxx/.hh` are
# unambiguous and stay cpp.
AMBIGUOUS_HEADER_EXTENSIONS = {"h"}

EXT_LANG = {
    # `.h` keeps the cpp default here; the per-file decision (C first, cpp
    # when C's tree carries an error or a missing node) lives in
    # `_choose_grammar`, and the index stores the language that parse used.
    "py": "python", "js": "javascript", "jsx": "javascript", "ts": "typescript",
    "tsx": "typescript", "rs": "rust", "go": "go", "java": "java", "c": "c",
    "cpp": "cpp", "cc": "cpp", "h": "cpp", "hpp": "cpp", "hxx": "cpp",
    "hh": "cpp", "cxx": "cpp", "cs": "csharp",
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
    "method_declaration", "constructor_declaration",
    "class_definition", "class_declaration", "class_specifier",
    "struct_item", "struct_specifier", "impl_item",
    "export_statement", "decorated_definition", "enum_specifier",
    "enum_declaration", "interface_declaration", "struct_declaration",
    "record_declaration", "function_item",
    "preproc_function_def",
}
# Container-ish declarations that still get a symbol row (walk recurses in).
_UNIT_TYPES_EXTRACT_ONLY = {
    "namespace_definition", "namespace_definition_probability",
    "namespace_declaration",
}
_TYPE_BY_NODE = {
    "function_definition": "function", "function_declaration": "function",
    "method_definition": "method", "method_declaration": "method",
    "constructor_declaration": "method", "preproc_function_def": "function",
    "function_item": "function",
    "class_definition": "class", "class_declaration": "class",
    "class_specifier": "class",
    "struct_item": "struct", "struct_specifier": "struct",
    "struct_declaration": "struct",
    "impl_item": "class", "enum_specifier": "enum",
    "enum_declaration": "enum",
    "interface_declaration": "interface",
    "record_declaration": "class",
    "namespace_definition": "namespace", "namespace_declaration": "namespace",
}
_CHAR_CAP = 1000
SIG_CAP = 120

# Unit types that CONTAIN other symbols (classes, structs, interfaces,
# records, impls). _walk recurses into them so nested methods/properties
# land in the symbol table; their chunk text is already covered by the
# container chunk itself.
_CONTAINER_TYPES = {
    "class_definition", "class_declaration", "class_specifier",
    "struct_item", "struct_specifier", "struct_declaration",
    "interface_declaration", "record_declaration", "impl_item",
}

# Grammar-specific body node names (design §3): the child field that starts
# the symbol body. A signature is the declaration text from start_byte up to
# the body node's start; one node-field lookup + one slice.
_BODY_NODE_BY_TYPE = {
    "function_definition": "body",
    "function_declaration": "body",
    "method_definition": "body",
    "method_declaration": "body",
    "constructor_declaration": "body",
    "class_definition": "body",
    "class_declaration": "body",
    "class_specifier": "field_declaration_list",
    "struct_specifier": "field_declaration_list",
    "struct_declaration": "declaration_list",
    "interface_declaration": "declaration_list",
    "record_declaration": "parameter_list",
    "impl_item": "declaration_list",
    "enum_specifier": "enumerator_list",
    "enum_declaration": "enum_member_declaration_list",
    "namespace_definition": "body",
    "namespace_declaration": "declaration_list",
    "preproc_function_def": "value",
}

# Node types whose text names a call target (textual, unbound).
_CALL_NODE_TYPES = {
    "call_expression", "call", "call_function", "function_call",
    "call_function_expression",
}


PARSE_PARSED = "parsed"
PARSE_ERROR = "parse_error"
PARSE_UNAVAILABLE = "parse_unavailable"


def chunk_text(path: str, text: str) -> list[Chunk]:
    """AST chunk via tree-sitter; falls back to regex chunker on failure."""
    return parse_file(path, text)[0]


def parse_tree(path: str, text: str):
    """The parser's tree and its own verdict, or ``(None, unavailable)``."""
    _lang, tree, verdict = parse_tree_with_language(path, text)
    return tree, verdict


def parse_tree_with_language(path: str, text: str):
    """The tree, its verdict, and the grammar that produced them.

    One place decides whether an AST exists at all: no grammar pack, no grammar
    for the extension, or a parser exception yields ``None`` with
    ``parse_unavailable``. Callers that need node ranges read the tree; the
    chunker reads it through ``parse_file`` so both answer from the same parse.

    The grammar is the extension's, except for the ambiguous `.h` header
    spelling, where the file's own contents decide (``_choose_grammar``). The
    chosen language is returned so the index can STORE the language that
    actually parsed the file instead of the one its name suggested.
    """
    if not _TS_OK:
        return None, None, PARSE_UNAVAILABLE
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    lang = EXT_LANG.get(ext)
    if lang is None:
        return None, None, PARSE_UNAVAILABLE
    data = text.encode("utf-8")
    if ext in AMBIGUOUS_HEADER_EXTENSIONS:
        return _choose_grammar(path, data)
    tree, verdict = _parse_with(lang, path, data)
    return lang, tree, verdict


def _parse_with(lang: str, path: str, data: bytes):
    """One grammar's tree and verdict, or ``(None, unavailable)``."""
    try:
        parser = tree_sitter_language_pack.get_parser(lang)
        tree = parser.parse(data)
    except Exception as exc:  # noqa: BLE001
        logger.debug("tree-sitter parse failed for %s (%s): %s", path, lang, exc)
        return None, PARSE_UNAVAILABLE
    return tree, _tree_verdict(tree.root_node)


def _choose_grammar(path: str, data: bytes):
    """The grammar a `.h` header is written in, read from the header itself.

    A `.h` name does not say C or C++: C++ projects spell headers `.h` too,
    and C is not a strict subset of what the cpp grammar reads (a K&R
    definition is legal C and an error in cpp). So both grammar trees are
    built -- this is the one extension where a second parse is spent -- and
    the evidence picks between them, in this order:

    1. a tree with no error or missing node beats one that carries a defect;
    2. between two clean trees, the one that DECLARES MORE wins: the C
       grammar reads a C++ header as a legal-looking function body whose name
       it cannot recover (`namespace geometry { ... }`), so it declares
       nothing from it, while the cpp grammar declares the names the project
       uses;
    3. a tie stays `c`: a header both grammars read into the same named
       declarations is C-compatible, and calling it C is what the C project
       that ships it expects -- no symbol is lost either way.

    Two trees that both carry a defect keep the cpp one: it is the family
    default and recovers the most of a broken header.
    """
    c_tree, c_verdict = _parse_with("c", path, data)
    cpp_tree, cpp_verdict = _parse_with("cpp", path, data)
    if c_tree is None and cpp_tree is None:
        return "c", None, PARSE_UNAVAILABLE
    if cpp_tree is None:
        return "c", c_tree, c_verdict
    if c_tree is None:
        return "cpp", cpp_tree, cpp_verdict
    c_clean = c_verdict == PARSE_PARSED
    cpp_clean = cpp_verdict == PARSE_PARSED
    if c_clean and not cpp_clean:
        return "c", c_tree, c_verdict
    if cpp_clean and not c_clean:
        return "cpp", cpp_tree, cpp_verdict
    if not c_clean:
        return "cpp", cpp_tree, cpp_verdict
    if (_declaration_count(cpp_tree.root_node, data)
            > _declaration_count(c_tree.root_node, data)):
        return "cpp", cpp_tree, cpp_verdict
    return "c", c_tree, c_verdict


def _declaration_count(root, data: bytes) -> int:
    """How many NAMED declaration units a tree holds, at any depth.

    The same node types the chunk walk honours, counted without building
    chunks. The name is part of the count because a misread C++ header parses
    as a nameless function body: a node that declares nothing particular is
    not evidence that this grammar read the file.
    """
    total = 0
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in _UNIT_TYPES or node.type in _UNIT_TYPES_EXTRACT_ONLY:
            if _symbol_from_node(node, data) is not None:
                total += 1
        stack.extend(node.children)
    return total


def parse_file(path: str, text: str) -> tuple[list[Chunk], str]:
    """Chunks plus the file's parse verdict, from ONE parse of the file."""
    chunks, verdict, _lang = parse_file_with_language(path, text)
    return chunks, verdict


def parse_file_with_language(
        path: str, text: str) -> tuple[list[Chunk], str, str | None]:
    """Chunks, the parser's verdict, and the grammar that produced them.

    The verdict is the parser's own result (ADR-0004 section 7), never the
    chunker's: ``parse_error`` when the tree carries an error or a missing
    node, ``parsed`` otherwise, and ``parse_unavailable`` when no AST exists
    at all — no grammar pack, no grammar for the extension, or a parser
    exception. An AST that declares nothing is not one of those: an
    import-only module parses cleanly and yields no chunk, so its text takes
    one fallback chunk for search while the verdict stays ``parsed`` — the
    parser read the file and said so. Only a file with no AST at all reports
    ``parse_unavailable``: a failed claim, not a pass.

    The language is the one the chosen grammar names (``c`` or ``cpp`` for a
    `.h` header), so the caller records what parsed the file rather than what
    its extension suggested.
    """
    lang, tree, verdict = parse_tree_with_language(path, text)
    if tree is None:
        return fallback_chunk(text), PARSE_UNAVAILABLE, lang

    chunks: list[Chunk] = []
    root = tree.root_node
    data = text.encode("utf-8")
    if verdict == PARSE_ERROR:
        logger.debug("tree-sitter error-tolerant parse for %s — using AST anyway", path)
    # tree-sitter byte offsets index the ENCODED buffer, not the str. Slicing
    # the str with byte offsets corrupts any name after multi-byte chars.
    # Decode each slice from the bytes buffer instead.
    _walk(root, data, chunks)
    if not chunks:
        # The parser read the file and its AST declares no unit -- an
        # import-only module is the shape. The text still needs one chunk to
        # be searchable, but the verdict describes the PARSE, not the chunk
        # yield: blaming a missing reader for a file the parser read is the
        # false fact this branch used to store.
        return fallback_chunk(text), verdict, lang
    for i, c in enumerate(chunks):
        c.chunk_index = i
        c.source = "ast"
    return chunks, verdict, lang


def _tree_verdict(root) -> str:
    """``parsed`` or ``parse_error`` for one tree, read from the tree itself.

    ``has_error`` is the parser's own verdict on a recovered tree; the
    explicit missing-node walk is kept beside it because a missing node is a
    defect the caller is promised to hear about, whether or not a grammar
    folds it into ``has_error``.
    """
    if root.has_error:
        return PARSE_ERROR
    cursor = root.walk()
    while True:
        if cursor.node.is_missing:
            return PARSE_ERROR
        if cursor.goto_first_child():
            continue
        while not cursor.goto_next_sibling():
            if not cursor.goto_parent():
                return PARSE_PARSED


def _declaration_signature(node, data: bytes) -> str:
    """Decl text from the node's start up to the body node (design §3).

    First line only, whitespace-collapsed, capped at SIG_CAP. When no body
    node is found, fall back to the first physical line.
    """
    body_name = _BODY_NODE_BY_TYPE.get(node.type)
    if node.type == "decorated_definition":
        # Signature should be the wrapped unit's decl, not the decorator.
        for ch in node.children:
            if ch.type in _UNIT_TYPES:
                return _declaration_signature(ch, data)
    body = None
    if body_name:
        body = node.child_by_field_name(body_name)
    end = body.start_byte if body is not None else None
    if end is None or end <= node.start_byte:
        text = data[node.start_byte:node.end_byte].decode("utf-8", "replace")
        first_line = split_lines(text)[0] if text else ""
    else:
        text = data[node.start_byte:end].decode("utf-8", "replace")
        first_line = split_lines(text)[0] if text else ""
    return " ".join(first_line.split())[:SIG_CAP]


def _visibility_from_name(name: str | None, node_type: str,
                          node_text: str, sym_type: str | None) -> str:
    """Per-language visibility rule (design §2.5, §3). C/C++/Java default
    public (class-member access sections are not tracked — documented
    limitation, do not oversell)."""
    if not name:
        return "public"
    # Rust: `pub` present in decl head → public; otherwise private (design
    # §2.5: pub absent → private, no underscore exception needed).
    if node_type in ("function_item", "struct_item", "impl_item",
                     "enum_item", "type_item"):
        head = " ".join(node_text.split()[:4])
        return "public" if "pub" in head.split() else "private"
    # Python: leading underscore → private.
    if sym_type in ("function", "method", "class") and name.startswith("_"):
        return "private"
    return "public"


def _walk(node, data: bytes, out: list[Chunk],
          container: str | None = None) -> None:
    """Collect declaration units; recurse into non-unit containers.

    ``container`` is the qualified name of the enclosing declaration, threaded
    down the walk: a method inside ``Core`` is emitted with
    ``container="Core"``, which is what makes its qualified name
    ``Core.search`` rather than a bare ``search`` (ADR-0002 section 1).
    """
    text = None  # decoded lazily only when needed
    for child in node.children:
        if child.type in _UNIT_TYPES:
            start = child.start_point[0] + 1
            end = child.end_point[0] + 1  # end_point row is 0-based
            node_text = data[child.start_byte:child.end_byte].decode("utf-8", "replace")
            symbol = _symbol_from_node(child, data)
            sym_type = _TYPE_BY_NODE.get(child.type)
            signature = _declaration_signature(child, data)
            # The qualified name this unit's children would carry.
            nested = _join_qualified(container, symbol)
            if len(node_text) > _CHAR_CAP:
                # Oversized unit: split via fallback on its own text,
                # adjusting line numbers and preserving symbol on the first.
                # source stays 'ast': the unit boundary is AST-derived; only
                # the split is mechanical (design round-1 decision).
                subs = fallback_chunk(node_text)
                # A piece's line numbers are relative to ``node_text``, whose
                # first line is file line ``start``. Map each piece straight
                # onto the file. A running offset cannot be used: it would
                # count ``start`` a second time (the piece's 1-based line
                # already contains the same shift) and the pieces OVERLAP by
                # OVERLAP_LINES, so adding lengths would drift on every
                # window too -- the reported range would name text the file
                # does not have.
                for s in subs:
                    # Only the FIRST sub-piece is the declaration head and
                    # carries the container's symbol/type. Later pieces are
                    # anonymous body fragments — they must NOT inherit the
                    # container's symbol_type, or the regex-guessed symbol
                    # gets registered as a bogus class/method row (seen on
                    # CrimeSurface.cs: comment-leading fragments surfaced
                    # as 'FeelsRemorse class' / 'if class').
                    out.append(Chunk(
                        text=s.text, chunk_hash=s.chunk_hash,
                        symbol=symbol if s.start_line == 1 else None,
                        start_line=start + s.start_line - 1,
                        end_line=start + s.end_line - 1,
                        chunk_index=0, source="ast",
                        symbol_type=sym_type if s.start_line == 1 else None,
                        signature=signature if s.start_line == 1 else None,
                        node_type=child.type if s.start_line == 1 else None,
                        container=container if s.start_line == 1 else None,
                    ))
                # The split pieces hide any nested units (methods inside a
                # >1000-char C# class, etc.) from the symbol table — walk the
                # unit's own body so inner symbols are still extracted.
                _walk(child, data, out,
                      nested if child.type in _CONTAINER_TYPES else container)
            else:
                h = hashlib.sha256(node_text.encode()).hexdigest()
                out.append(Chunk(
                    text=node_text, chunk_hash="sha256:" + h, symbol=symbol,
                    start_line=start, end_line=end, chunk_index=0,
                    source="ast", symbol_type=sym_type,
                    signature=signature, node_type=child.type,
                    container=container,
                ))
                # Container types carry nested symbols (methods in a class,
                # namespaces in a namespace, etc.): recurse so they land in
                # the symbol table as well. Their chunks are covered by the
                # container chunk above; this walk is extract-only there.
                if child.type in _CONTAINER_TYPES:
                    _walk(child, data, out, nested)
        elif child.type in _UNIT_TYPES_EXTRACT_ONLY:
            # Namespace: emit a symbol row via the chunk that _make_chunk
            # would produce ONLY when the body is small; always recurse.
            symbol = _symbol_from_node(child, data)
            if symbol:
                start = child.start_point[0] + 1
                end = child.end_point[0] + 1
                node_text = data[child.start_byte:child.end_byte].decode("utf-8", "replace")
                if len(node_text) <= _CHAR_CAP:
                    h = hashlib.sha256(node_text.encode()).hexdigest()
                    out.append(Chunk(
                        text=node_text, chunk_hash="sha256:" + h, symbol=symbol,
                        start_line=start, end_line=end, chunk_index=0,
                        source="ast", symbol_type="namespace",
                        signature=_declaration_signature(child, data),
                        node_type=child.type, container=container,
                    ))
            _walk(child, data, out, _join_qualified(container, symbol))
        elif child.child_count:
            _walk(child, data, out, container)
    del text


def _join_qualified(container: str | None, name: str | None) -> str | None:
    """``Core.search`` for a nested declaration; the bare name at top level."""
    if not name:
        return container
    return f"{container}.{name}" if container else name


def _symbol_from_node(node, data: bytes) -> str | None:
    # Exact name-field pass FIRST: grammars that carry a `name` FIELD
    # (C#-family) must never fall through to the first-identifier heuristic
    # below — that heuristic can grab a return-type identifier instead of
    # the declared name (e.g. `public static HostileActVerdict Evaluate()`
    # → first identifier is the return type). Field lookup returns None on
    # grammars without name fields, so Python/TS are unaffected.
    if node.child_by_field_name("name") is not None:
        name_node = node.child_by_field_name("name")
        if name_node.type == "qualified_name":
            # C# `namespace Estate.Sim {}` names the field with a
            # qualified_name node. Only the LEAF is ever stored — this
            # returns the last dotted part, and no dotted symbol name
            # reaches the store — so `find_symbol` and every other caller
            # match the leaf, and only the leaf.
            full = data[name_node.start_byte:name_node.end_byte].decode(
                "utf-8", "replace")
            return full.split(".")[-1] if "." in full else full
        if name_node.type in ("identifier", "type_identifier",
                              "namespace_identifier", "token_identifier"):
            return data[name_node.start_byte:name_node.end_byte].decode(
                "utf-8", "replace")
    # First named child of a common identifier type is the declaration name
    # (fallback for grammars without name fields).
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
    first_line = split_lines(
        data[node.start_byte:node.end_byte].decode("utf-8", "replace"))[0] \
        if node.end_byte > node.start_byte else ""
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

    Returns [{'name', 'qualified_name', 'container', 'symbol_type',
    'start_line', 'end_line', 'source', 'signature', 'visibility',
    'decl_hash'}]. ONLY chunks the AST walk produced ('source' == 'ast')
    become rows: the regex fallback is a text window, and a window's first
    line is not a declaration (see the loop's comment).

    ``qualified_name`` is the container chain the chunk carries joined to the
    name (``Core.search``); ``container`` is that chain alone, NULL at top
    level. ``decl_hash`` is the sha256 of the normalized signature, falling
    back to the declaration's first line when a chunk has none, so the
    identity key is total for every row (ADR-0002 section 1).
    """
    rows: dict[tuple, dict] = {}
    for c in chunks:
        if not c.symbol:
            continue
        if c.source != "ast":
            # The symbol table is an authority other agents act on, so a row
            # here means "a declaration parsed from source". A guess from a
            # text window (the regex fallback, which runs for prose formats
            # and for any language without a readable AST) is not that: a
            # markdown heading and a sentence with a name in parentheses both
            # matched the declaration patterns, and both became definitions.
            #
            # THIS is the table's one guard. The indexer clears a prose
            # chunk's symbol metadata before its payload is written; that
            # clearing is about what a chunk echoes, not about what enters
            # the table, and it is not a second rule.
            continue
        stype = c.symbol_type or _guess_type_regex(c.text) or "function"
        qualified = _join_qualified(c.container, c.symbol)
        signature = c.signature
        visibility = _visibility_from_name(
            c.symbol, c.node_type or "", c.text, stype)
        first_line = split_lines(c.text)[0] if c.text else ""
        decl = declaration_hash(signature, first_line)
        # The identity key is line-free, so ONE declaration observed twice (a
        # decorated definition's outer and inner node, a chunk split) is ONE
        # row: the first observed range is kept and the duplicate is dropped.
        # Two declarations that differ only by their signature stay two rows
        # (the overload case); two byte-identical declarations in one file are
        # the same symbol by this model.
        key = (qualified, stype, decl)
        if key in rows:
            continue
        rows[key] = {
            "name": c.symbol, "qualified_name": qualified,
            "container": c.container,
            "symbol_type": stype,
            "start_line": c.start_line, "end_line": c.end_line,
            "source": c.source,
            "signature": signature,
            "visibility": visibility,
            "decl_hash": decl,
        }
    return list(rows.values())


# NOTE: the regex reference extractor that used to live here (calls/includes
# rows written to the retired `symbol_refs` table) is gone. References have ONE
# writer — `graph_extract` — and land in the single `refs` manifest table
# (schema v6). `ts_chunker` extracts chunks and symbols only.


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
