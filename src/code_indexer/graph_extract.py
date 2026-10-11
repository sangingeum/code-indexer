"""AST imports/references extraction (graph work item).

Per-language tree-sitter queries live in ``queries/<lang>/references.scm``.
Languages with a solid grammar + query get ``confidence='ast'`` rows:
python, typescript/tsx, javascript, c, cpp, go, csharp (LANGUAGES_WITH_AST).
Everything else falls back to a textual scan stamped
``confidence='heuristic'`` — comments/strings DO count there; the
false-positive test pins that AST extraction does NOT match names that only
appear in comments/strings.

One reference shape, one writer
-------------------------------
A reference row is ``(line, from_symbol, to_name, relationship, receiver)``
and lands in the single ``refs`` manifest table. The relationship
vocabulary is fixed here and documented once:

- ``call``       — an invocation (AST), or a tight ``name(`` occurrence (no
                   whitespace between the name and the paren) in the textual
                   scan. On a textual line one occurrence yields ONE row: a
                   tight ``name(`` is a ``call``, anything else is an
                   ``identifier`` — so a call site is never double-reported.
- ``type``       — a name used in a type position (AST).
- ``base_class`` — a base class / interface in an inheritance list (AST).
- ``member``     — a member access read, e.g. C# ``a.OughtRoleBindings`` (AST).
- ``includes``   — an include/import directive. AST: a python import, a TS/JS
                   import specifier or ``require(...)`` source, a Go import
                   path, a C# ``using`` target, a C/C++ quoted include. A
                   require is a call, so the TS/JS query scopes it to the
                   ``require`` function name — an ordinary string-argument
                   call (``alert("hi")``) is NOT an include (issue #12).
                   Textual: an import/include line.
- ``identifier`` — any identifier in the textual scan.

``receiver`` is the receiver expression of a member access AS WRITTEN, and
only when it is a plain identifier (``ArmorResolution.Classify`` →
``receiver='ArmorResolution'``); it is NULL otherwise. No type resolution is
attempted — the column means exactly "the receiver expression as written".

Only the OUTERMOST member access of a chain is a reference: in
``System.Console.WriteLine(x)`` the row is ``WriteLine`` and there is NO row
for ``Console`` or ``System``. The trade-off is stated rather than hidden:
the surviving row of a chain deeper than one hop carries ``receiver=NULL``,
because its receiver expression is itself a member access, not a plain
identifier.

One row per ``(file, line, to_name, relationship)``
---------------------------------------------------
Both the in-memory dedupe key (``_dedupe_refs``) and the ``refs`` table
primary key EXCLUDE ``receiver``, so when the same name is read twice on one
line with two different receivers (``a.Foo; b.Foo;``) the first row in
document order wins and the second receiver is DROPPED with no trace. That is
the documented resolution of issue #9 item 3b: keeping both rows would mean
changing the table primary key, which needs a full ``refs`` table rebuild in
the manifest migration — measured as unclean against the gain for a rare
shape. Two reads on two separate lines are both kept.

Import resolution is best-effort, documented per language:
- python: relative imports resolved against the file's package; absolute
  dotted names tried as dir.py / dir/__init__.py from the root and src/.
- typescript/javascript: relative paths with index-file augmentation;
  package names unresolved.
- c/cpp: quoted includes relative to the file's directory; angled (system)
  includes unresolved.
- go: go.mod module path prefix stripped, then package-dir mapping.
- csharp: ``using`` directives are recorded as imports but left unresolved
  (namespace-to-file mapping needs project context the indexer does not
  build); documented like the other languages, never guessed.
- java/kotlin etc.: no AST query yet — textual fallback only.

Extraction version: bump EXTRACTION_VERSION when the queries or resolution
change shape; the CI-02 fingerprint mechanism flags existing indexes
needs-reindex so the new tables populate honestly.
"""

from __future__ import annotations

import os
import re

# Languages with AST queries (confidence='ast'). Keys are
# tree-sitter-language-pack names. Everything else is textual fallback.
LANGUAGES_WITH_AST = frozenset(
    {"python", "typescript", "tsx", "javascript", "c", "cpp", "go", "csharp"})

# Bumped when queries/resolution change which rows are produced.
# v3: the textual scan emits a `call` row for a tight `name(` occurrence, and
# the AST queries route their import/include target into the reference store as
# an `includes` row. Every existing index therefore reports needs-reindex and
# must be rebuilt before the new rows exist.
# v4: the TS/TSX/JS require patterns are scoped to the `require` function name
# (issue #12). Previously any identifier call with a string first argument
# (`alert("hi")`) produced BOTH an `imports` row (kind `require`) and an
# `includes` reference row; now only `require(...)` does. `require` coverage is
# unchanged — the false positives are dropped.
EXTRACTION_VERSION = 4

_INDEX_EXTS = (".ts", ".tsx", ".js", ".jsx")

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_COMMENT_LINE_RE = re.compile(r"^\s*(#|//|\*|--)")
_DECL_TYPES = {
    "function_definition", "function_declaration", "method_definition",
    "class_definition", "class_declaration", "function_item",
    "method_declaration", "class_specifier", "struct_specifier",
}

_QUERY_DIR = os.path.join(os.path.dirname(__file__), "queries")


def _node_text(node, data: bytes) -> str:
    return data[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _load_query(lang: str):
    """(compiled_query, parser) or None when the language has no query."""
    if lang not in LANGUAGES_WITH_AST:
        return None
    query_path = os.path.join(_QUERY_DIR, lang, "references.scm")
    if not os.path.isfile(query_path):
        return None
    try:
        import tree_sitter_language_pack as tlp
        from tree_sitter import Query, QueryCursor
        parser = tlp.get_parser(lang)
        with open(query_path, encoding="utf-8") as fh:
            query = Query(parser.language, fh.read())
        return query, QueryCursor(query), parser
    except Exception:  # noqa: BLE001 — grammar/query mismatch: fall back
        return None


def extract_graph(path: str, text: str, lang: str
                  ) -> tuple[list[tuple[str, str | None, str, int]],
                             list[tuple[int, str | None, str, str, str | None]]]:
    """Extract (imports, refs) rows for one file (AST path).

    imports: (raw_target, resolved_file=None, kind, line) — resolution needs
    project context and happens in resolve_imports.
    refs: (line, from_symbol, to_name, relationship, receiver).
    Returns ([], []) when the language has no usable AST query (the caller
    then uses extract_graph_textual).
    """
    loaded = _load_query(lang)
    if loaded is None:
        return [], []
    _query, cursor, parser = loaded
    data = text.encode("utf-8")
    tree = parser.parse(data)

    imports: list[tuple[str, str | None, str, int]] = []
    refs: list[tuple[int, str | None, str, str, str | None]] = []

    # Enclosing declaration names: span list, later spans win on ties.
    decl_spans: list[tuple[int, int, str]] = []

    def _collect_decls(node) -> None:
        if node.type in _DECL_TYPES:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                decl_spans.append((node.start_point[0], node.end_point[0],
                                   _node_text(name_node, data)))
        for child in node.children:
            _collect_decls(child)

    _collect_decls(tree.root_node)

    def _enclosing(row: int) -> str | None:
        best: str | None = None
        for s, e, name in decl_spans:
            if s <= row <= e:
                best = name
        return best

    # QueryCursor.captures: normalize across binding shapes.
    raw = cursor.captures(tree.root_node)
    if isinstance(raw, dict):
        pairs: list[tuple[object, str]] = [
            (node, name) for name, nodes in raw.items() for node in nodes]
    else:
        pairs = [(node, name) for node, name in raw]

    for node, capture in pairs:
        row = node.start_point[0]
        frm = _enclosing(row)
        text_val = _node_text(node, data)
        if capture in ("import.target", "import.name"):
            imports.append((text_val.strip("'\""), None, "import", row + 1))
        elif capture == "import.require-source":
            imports.append((text_val.strip("'\""), None, "require", row + 1))
        elif capture == "ref.call" or capture == "ref.call_plain":
            refs.append((row + 1, frm, text_val, "call", None))
        elif capture == "ref.member":
            member_row = _member_ref(node, data, row, frm)
            if member_row is not None:
                refs.append(member_row)
        elif capture == "ref.type":
            refs.append((row + 1, frm, text_val, "type", None))
        elif capture == "ref.base_class":
            refs.append((row + 1, frm, text_val, "base_class", None))
        elif capture == "ref.include":
            refs.append((row + 1, frm, text_val.strip("'\""), "includes", None))
    return (_dedupe_imports(imports),
            _dedupe_refs([r for r in refs if r[2]]))


def _member_ref(node, data: bytes, row: int, frm: str | None
                ) -> tuple[int, str | None, str, str, str | None] | None:
    """One member-access row: (line, from_symbol, name, relationship, receiver).

    ``node`` is a ``member_access_expression`` (C#). The relationship is
    ``call`` when the access is the function of an invocation
    (``ArmorResolution.Classify(...)``) and ``member`` when it is a plain read
    (``a.OughtRoleBindings``). The receiver is the object expression's text
    only when that expression is a plain identifier, else NULL.

    Returns ``None`` for an INTERMEDIATE segment of a longer chain: in
    ``System.Console.WriteLine(x)`` the inner ``System.Console`` access is the
    ``expression`` child of the outer access, so ``Console`` is a namespace
    segment, not something anyone can call or read. Only the outermost access
    of a chain becomes a row. Consequence: for a chain deeper than one hop the
    surviving row's receiver is NULL (the receiver expression is itself a
    member access, not a plain identifier) — the trade-off for dropping the
    intermediate rows.
    """
    parent = node.parent
    if (parent is not None and parent.type == "member_access_expression"
            and parent.child_by_field_name("expression") == node):
        return None
    name_node = node.child_by_field_name("name")
    member = _node_text(name_node, data) if name_node is not None else ""
    obj = node.child_by_field_name("expression")
    receiver = (_node_text(obj, data)
                if obj is not None and obj.type == "identifier" else None)
    is_call = (parent is not None
               and parent.type == "invocation_expression"
               and parent.child_by_field_name("function") == node)
    return (row + 1, frm, member, "call" if is_call else "member", receiver)


def extract_graph_textual(path: str, text: str
                          ) -> tuple[list[tuple[str, str | None, str, int]],
                                     list[tuple[int, str | None, str, str, str | None]]]:
    """Fallback for languages without AST queries: import-line pattern plus
    an identifier scan outside obvious comment leads. Lines beginning with
    ``#``, ``//``, ``*`` or ``--`` are skipped entirely; trailing comments and
    strings DO count here — the documented heuristic limitation.

    An import/include line yields both an import row and an ``includes``
    reference row, so ``find-references --relationship includes`` still sees
    it without a second writer.

    Call coverage: an identifier followed IMMEDIATELY by ``(`` is stored as a
    ``call`` row, so ``find-references X --relationship calls`` answers on a
    language with no AST query instead of printing a bare negative. The
    whitespace rule is deliberate — measured over the live indexes, a tight
    ``name(`` occurs 20,157 times in textual-scan languages: 13,338 (≈66%) are
    ``.csx`` script call sites, i.e. real code, and the other 6,819 (≈34%) are
    not — markdown 6,468 (≈32%), json 244, shell 78, text 9, toml 2 and a few
    stragglers. Allowing whitespace before the paren would add a further 89,882
    matches that are overwhelmingly prose (79,551 in markdown, e.g.
    ``function (see below)``). What the tight rule does NOT do: it removes that
    spaced-prose flood but does not make markdown prose call-free, because the
    ``word(s)`` shape has no space before the paren — a tight ``commit(s)``,
    ``actor(s)`` or ``project(s)`` is still stored as a ``call`` row. The row
    means exactly "a ``name(`` occurrence is on this line" — it is
    ``confidence='heuristic'``, context is not resolved, and a name mentioned
    only in a string or a trailing comment still matches.
    """
    imports: list[tuple[str, str | None, str, int]] = []
    refs: list[tuple[int, str | None, str, str, str | None]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if _COMMENT_LINE_RE.match(line):
            continue
        m = re.match(r"\s*(?:import|require|use)\s+(.+)", line)
        if m:
            target = m.group(1).strip().strip("'\"")
            imports.append((target, None, "import", i))
            refs.append((i, None, target, "includes", None))
            continue
        for match in _IDENT_RE.finditer(line):
            ident = match.group(0)
            relationship = ("call" if line[match.end():match.end() + 1] == "("
                            else "identifier")
            refs.append((i, None, ident, relationship, None))
    return _dedupe_imports(imports), _dedupe_refs(refs)


def _dedupe_imports(rows):
    seen: set = set()
    out = []
    for r in rows:
        key = (r[0], r[3])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _dedupe_refs(rows):
    """One row per (line, to_name, relationship) — ``receiver`` is NOT part of
    the key, so two same-name member reads on one line collapse to the first
    in document order (documented; see the module docstring)."""
    seen: set = set()
    out = []
    for r in rows:
        key = (r[0], r[2], r[3])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# import resolution (best-effort, per language)
# ---------------------------------------------------------------------------

def resolve_imports(project_root: str, file: str, lang: str,
                    raw_targets: list[str]) -> dict[str, str | None]:
    """Resolve raw import targets to project-relative files (best effort).

    Unresolvable targets (external packages, system headers) map to None.
    """
    out: dict[str, str | None] = {}
    file_dir = os.path.dirname(os.path.join(project_root, file))
    for target in raw_targets:
        if lang == "python":
            out[target] = _resolve_python(project_root, file_dir, target)
        elif lang in ("typescript", "tsx", "javascript"):
            out[target] = _resolve_js(project_root, file_dir, target)
        elif lang in ("c", "cpp"):
            out[target] = _resolve_c(project_root, file_dir, target)
        elif lang == "go":
            out[target] = _resolve_go(project_root, target)
        else:
            out[target] = None
    return out


def _proj_file(project_root: str, rel: str) -> str | None:
    cand = os.path.normpath(os.path.join(project_root, rel))
    if os.path.isfile(cand):
        return os.path.relpath(cand, project_root).replace(os.sep, "/")
    return None


def _resolve_python(project_root: str, file_dir: str,
                    target: str) -> str | None:
    rel = os.path.relpath(file_dir, project_root).replace(os.sep, "/")
    if target.startswith("."):
        depth = len(target) - len(target.lstrip("."))
        base_parts = [] if rel == "." else rel.split("/")
        if depth > 1:
            base_parts = base_parts[: len(base_parts) - (depth - 1)]
        mod = target[depth:].replace(".", "/")
        cand_dir = "/".join(base_parts + ([mod] if mod else []))
        for cand in (f"{cand_dir}.py", f"{cand_dir}/__init__.py"):
            r = _proj_file(project_root, cand)
            if r:
                return r
        return None
    mod = target.replace(".", "/")
    for cand in (f"{mod}.py", f"{mod}/__init__.py",
                 f"src/{mod}.py", f"src/{mod}/__init__.py"):
        r = _proj_file(project_root, cand)
        if r:
            return r
    return None


def _resolve_js(project_root: str, file_dir: str, target: str) -> str | None:
    if not target.startswith("."):
        return None  # package name: unresolved
    base = os.path.relpath(
        os.path.normpath(os.path.join(file_dir, target)),
        project_root).replace(os.sep, "/")
    cands = [base]
    cands += [base + ext for ext in _INDEX_EXTS]
    cands += [f"{base}/index{ext}" for ext in _INDEX_EXTS]
    for cand in cands:
        r = _proj_file(project_root, cand)
        if r:
            return r
    return None


def _resolve_c(project_root: str, file_dir: str, target: str) -> str | None:
    if target.startswith("<"):
        return None  # system header
    target = target.strip("'\"")  # quoted include: strip the quote chars
    cand = os.path.relpath(
        os.path.normpath(os.path.join(file_dir, target)),
        project_root).replace(os.sep, "/")
    return _proj_file(project_root, cand)


def _resolve_go(project_root: str, target: str) -> str | None:
    module = _go_module(project_root)
    rel = target
    if module and target.startswith(module + "/"):
        rel = target[len(module) + 1:]
    cand_dir = os.path.normpath(os.path.join(project_root, rel))
    if os.path.isdir(cand_dir):
        for name in sorted(os.listdir(cand_dir)):
            if name.endswith(".go") and not name.endswith("_test.go"):
                return f"{rel}/{name}" if rel != "." else name
    return _proj_file(project_root, rel + ".go")


def _go_module(project_root: str) -> str | None:
    gomod = os.path.join(project_root, "go.mod")
    if os.path.isfile(gomod):
        try:
            with open(gomod, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("module "):
                        return line.split()[1].strip()
        except OSError:
            pass
    return None
