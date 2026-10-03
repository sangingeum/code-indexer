"""AST imports/references extraction (graph work item).

Per-language tree-sitter queries live in ``queries/<lang>/references.scm``.
Languages with a solid grammar + query get ``confidence='ast'`` rows:
python, typescript/tsx, javascript, c, cpp, go (LANGUAGES_WITH_AST).
Everything else falls back to a textual scan stamped
``confidence='heuristic'`` — comments/strings DO count there; the
false-positive test pins that AST extraction does NOT match names that only
appear in comments/strings.

Import resolution is best-effort, documented per language:
- python: relative imports resolved against the file's package; absolute
  dotted names tried as dir.py / dir/__init__.py from the root and src/.
- typescript/javascript: relative paths with index-file augmentation;
  package names unresolved.
- c/cpp: quoted includes relative to the file's directory; angled (system)
  includes unresolved.
- go: go.mod module path prefix stripped, then package-dir mapping.
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
    {"python", "typescript", "tsx", "javascript", "c", "cpp", "go"})

# Bumped when queries/resolution change which rows are produced.
EXTRACTION_VERSION = 1

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
                             list[tuple[int, str | None, str, str]]]:
    """Extract (imports, refs) rows for one file (AST path).

    imports: (raw_target, resolved_file=None, kind, line) — resolution needs
    project context and happens in resolve_imports.
    refs: (line, from_symbol, to_name, relationship).
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
    refs: list[tuple[int, str | None, str, str]] = []

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
        elif capture == "ref.call":
            refs.append((row + 1, frm, text_val, "call"))
        elif capture == "ref.type":
            refs.append((row + 1, frm, text_val, "type"))
        elif capture == "ref.base_class":
            refs.append((row + 1, frm, text_val, "base_class"))
    return _dedupe_imports(imports), _dedupe_refs(refs)


def extract_graph_textual(path: str, text: str
                          ) -> tuple[list[tuple[str, str | None, str, int]],
                                     list[tuple[int, str | None, str, str]]]:
    """Fallback for languages without AST queries: import-line pattern plus
    an identifier scan outside obvious comment leads. Comments/strings DO
    count here — the documented heuristic limitation."""
    imports: list[tuple[str, str | None, str, int]] = []
    refs: list[tuple[int, str | None, str, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if _COMMENT_LINE_RE.match(line):
            continue
        m = re.match(r"\s*(?:import|require|use)\s+(.+)", line)
        if m:
            imports.append((m.group(1).strip().strip("'\""), None,
                            "import", i))
            continue
        for ident in _IDENT_RE.findall(line):
            refs.append((i, None, ident, "identifier"))
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