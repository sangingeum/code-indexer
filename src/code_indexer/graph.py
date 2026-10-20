"""Graph queries: find-callers / find-callees / deps (graph work item).

Walks the manifest's single reference store (``refs``) and the imports table
breadth-first with depth and node caps.

Node identity — what makes two rows the same node, and therefore what the
cycle-safe visited set is keyed on:

* a definition root is ``(file, symbol)``;
* a caller node is the CALL SITE, ``(file, line, from_symbol,
  relationship)`` — not the enclosing symbol, so two calls to the same target
  from ONE function are two nodes, each carrying its own line and its own
  receiver (issue #21: enclosing-symbol identity collapsed the second into the
  first, silently);
* a callee node is the TARGET, ``(file, to_name)`` — `find-callees` answers
  "what does this symbol reach", so one target reached twice is one callee.
  The asymmetry with `find-callers` is deliberate.

Caps are honest. An edge this walk examined and refused because the node cap
was reached is counted in ``GraphResult.omitted`` and the answer is marked
``truncated``; a depth cap that stops a frontier which still has unvisited
edges adds a "deeper references exist" line. An incomplete answer always says
so — it is never indistinguishable from a complete one.

Output is an indented compact tree; JSON carries the edges plus the two
top-level truncation fields (``truncated``, ``omitted``).

A node's ``receiver`` is the receiver expression of the reference AS WRITTEN
(a plain identifier on a member access, else NULL). It is taken from the
stored row — never inferred from the query — and is reported only on the
DIRECT hits of the subject (the level-0 fan-in/fan-out edges); transitive
nodes carry no receiver.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .manifest import Manifest


@dataclass
class GraphNode:
    file: str
    symbol: str | None
    line: int | None
    relationship: str
    confidence: str
    children: list["GraphNode"] = field(default_factory=list)
    # Receiver of a member-access reference as written, when the store holds
    # one for THIS edge; NULL on definition roots and transitive nodes.
    receiver: str | None = None

    def label(self) -> str:
        loc = f"{self.file}:{self.line}" if self.line else self.file
        sym = f" {self.symbol}" if self.symbol else ""
        recv = f" (receiver={self.receiver})" if self.receiver else ""
        return f"{loc}{sym} [{self.relationship}/{self.confidence}]{recv}"


@dataclass
class GraphResult:
    """One graph walk: the nodes, what explains them, and whether the answer
    is incomplete.

    ``notes`` are plain-language lines for the caller to print. ``truncated``
    is true when this answer is incomplete — the node cap refused an edge, or
    the depth cap stopped a frontier that still had edges. ``omitted`` is the
    number of edges the walk examined and refused because the node cap was
    reached (0 when nothing was refused); it is a count of what the walk saw,
    never a claim about the whole population.
    """

    nodes: list[GraphNode] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    truncated: bool = False
    omitted: int = 0


def _manifest_for(core, slug: str) -> Manifest:
    return core.manifest_for(slug)


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _cap_note(cap: int, omitted: int, what: str, scope: str = "") -> str:
    """The truncation line for a node cap. It says the answer is incomplete,
    names the cap and the refused count, and never presents that count as the
    whole population (it is what this walk examined, not what the graph
    holds)."""
    noun = _plural(omitted, "edge", "edges")
    return (f"node cap {cap} reached{scope} — {omitted} more {what} {noun} "
            "not shown (a lower bound: a refused edge is counted but never "
            "expanded, so its own edges are not walked, and a depth cap on "
            "the same answer can cut more); raise --max-nodes to see more")


def _depth_note(depth: int, what: str) -> str:
    return (f"depth cap {depth} reached — deeper {what} exist beyond depth "
            f"{depth}; raise --depth to see them")


def _site_key(file: str, line: int | None, frm: str | None,
              relationship: str) -> tuple:
    """Call-site identity: the SITE, not the enclosing symbol."""
    return ("site", file, line if line is not None else -1, frm or "",
            relationship)


def _root_key(file: str, symbol: str | None) -> tuple:
    return ("definition", file, symbol or "")


def _callee_key(file: str, to_name: str | None) -> tuple:
    """Callee identity: the TARGET set (see the module docstring)."""
    return ("callee", file, to_name or "")


def _expand_callers(core, m: Manifest, name: str
                    ) -> list[tuple[str, int | None, str | None, str, str,
                                   str | None]]:
    """(file, line, from_symbol, relationship, confidence, receiver) — the
    edges INTO `name` (by target symbol name, matching either a direct ref or
    an import of the file that defines it)."""
    edges = []
    for r in m.callers_of(name):
        edges.append((r["file"], r["line"], r["from_symbol"],
                      r["relationship"], r["confidence"], r["receiver"]))
    # Import-based fan-in: files importing the file whose symbols include
    # `name` (resolved edges are project-relative files). An import edge has
    # no receiver expression.
    for imp in m.import_rows():
        if imp["resolved_file"] and _file_defines(m, imp["resolved_file"],
                                                  name):
            edges.append((imp["file"], imp["line"], None,
                          f"import:{imp['kind']}", "ast", None))
    return edges


def _file_defines(m: Manifest, file: str, name: str) -> bool:
    return any(s.name == name for s in m.symbols_for_file(file))


def _expand_callees(core, m: Manifest, file: str, symbol: str | None
                    ) -> list[tuple[str, int | None, str | None, str, str,
                                   str | None]]:
    """Edges OUT of one symbol: the names it references, resolved to the
    defining file when unambiguous."""
    out = []
    for r in m.callees_of_file_symbol(file, symbol):
        target_file = _definer_of(m, r["to_name"])
        out.append((target_file or file, r["line"], r["to_name"],
                    r["relationship"], r["confidence"], r["receiver"]))
    return out


def _definer_of(m: Manifest, name: str) -> str | None:
    rows = m.find_symbols(name, substring=False)
    files = sorted({r.file for r in rows})
    return files[0] if len(files) == 1 else None


def find_callers(core, slug: str, name: str, depth: int = 2,
                 max_nodes: int = 40) -> GraphResult:
    """BFS over fan-in edges for the subject ``name``.

    A caller node is a CALL SITE, keyed ``(file, line, from_symbol,
    relationship)``: two calls to the same target inside ONE function of the
    same file are two nodes, each with its own line and its own receiver.
    (Enclosing-symbol identity silently collapsed the second — issue #21.)
    Definition roots keep ``(file, symbol)`` identity.

    The subject may be dotted (``Type.Member``): the qualifier is resolved
    against the symbol rows and the fan-in is matched on the trailing member
    name. The subject (and its resolved qualifier, if any) is reported ONCE on
    the ``notes`` lines — not stamped on the tree, where it cannot describe
    the rows it would label.

    A direct hit carries ``receiver`` only when the store holds one for that
    row (a member access ``Type.Member`` records the receiver expression as
    written); transitive nodes never carry a receiver.

    Symbol extraction covers method|class|function|enum|interface|struct
    only — properties and fields have NO definition row. When the subject has
    no definition, the BFS is seeded from the reference rows instead of
    returning an empty tree, and ``notes`` says so: an empty caller list must
    never read as "nothing calls this".

    Returns a GraphResult; ``truncated``/``omitted`` report a node-cap or
    depth-cap refusal instead of dropping edges silently.
    """
    m = _manifest_for(core, slug)
    notes: list[str] = []
    omitted = 0
    depth_capped = False
    try:
        qualifier: str | None = None
        member = name
        if "." in name:
            qualifier, member = name.rsplit(".", 1)
        if qualifier:
            q_rows = m.find_symbols(qualifier, substring=False)
            if q_rows:
                defs = ", ".join(f"{r.file}:{r.start_line}"
                                 for r in q_rows[:3])
                notes.append(
                    f"subject {name!r}: qualifier {qualifier!r} resolved "
                    f"({defs}); fan-in matched on member {member!r}; a "
                    "receiver is shown on a direct hit only when the store "
                    "holds one for that row")
                target = member
            else:
                # Unknown qualifier: fall back to the whole dotted name.
                notes.append(
                    f"subject {name!r}: qualifier {qualifier!r} has no symbol "
                    "row; treating the whole name as the subject")
                qualifier = None
                target = name
        else:
            target = member
        roots = [GraphNode(file=r.file, symbol=r.name, line=r.start_line,
                           relationship="definition", confidence="ast")
                 for r in m.find_symbols(target, substring=False)]
        if not roots:
            # No definition row: seed from the reference rows this command
            # reads, and say plainly that the index holds no definition.
            ref_rows = m.callers_of(target)
            notes.append(
                f"the index holds no definition for {target!r} (symbols cover "
                "method|class|function|enum|interface|struct; properties and "
                "fields are not extracted); seeding from reference rows "
                "instead of reporting none")
            seeds = [GraphNode(file=r["file"], symbol=r["from_symbol"],
                               line=r["line"], relationship=r["relationship"],
                               confidence=r["confidence"],
                               receiver=r.get("receiver"))
                     for r in ref_rows]
            if not seeds:
                notes.append(f"no reference rows for {target!r} either — "
                             "nothing in this index mentions it")
            return GraphResult(nodes=seeds, notes=notes)
        seen: set[tuple] = {_root_key(n.file, n.symbol) for n in roots}
        frontier = list(roots)
        result: list[GraphNode] = list(roots)
        count = len(result)
        for level in range(max(0, depth)):
            next_frontier: list[GraphNode] = []
            for node in frontier:
                # Level 0 targets the subject itself (or the member of a
                # dotted subject); deeper levels target the node's own symbol,
                # because a caller references the symbol NAME.
                tgt = target if level == 0 else (
                    node.symbol or os.path.basename(node.file))
                for (file, line, frm, rel, conf, recv) in _expand_callers(
                        core, m, tgt):
                    key = _site_key(file, line, frm, rel)
                    if key in seen:
                        continue
                    if count >= max_nodes:
                        # Refused, not dropped: counted, and remembered so a
                        # later level cannot recount the same edge.
                        seen.add(key)
                        omitted += 1
                        continue
                    seen.add(key)
                    count += 1
                    child = GraphNode(file=file, symbol=frm, line=line,
                                      relationship=rel, confidence=conf,
                                      # Receiver only on the direct hits.
                                      receiver=recv if level == 0 else None)
                    node.children.append(child)
                    next_frontier.append(child)
            frontier = next_frontier
        # The depth cap stopped a frontier: does it still hold unvisited
        # edges? Answer that from the store rather than assuming it does.
        stopped_level = max(0, depth)
        for node in frontier:
            tgt = target if stopped_level == 0 else (
                node.symbol or os.path.basename(node.file))
            for (file, line, frm, rel, _conf, _recv) in _expand_callers(
                    core, m, tgt):
                if _site_key(file, line, frm, rel) not in seen:
                    depth_capped = True
                    break
            if depth_capped:
                break
        if omitted:
            notes.append(_cap_note(max_nodes, omitted, "reference"))
        if depth_capped:
            notes.append(_depth_note(stopped_level, "references"))
        return GraphResult(nodes=result, notes=notes,
                           truncated=bool(omitted or depth_capped),
                           omitted=omitted)
    finally:
        m.close()


def find_callees(core, slug: str, name: str, depth: int = 2,
                 max_nodes: int = 40) -> GraphResult:
    """BFS over fan-out edges rooted at every definition of `name`.

    Identity is the TARGET, keyed ``(file, to_name)`` — deliberately NOT the
    call site, which is the asymmetry with `find_callers`: this walk answers
    "what does this symbol reach", so a caller calling one target twice is one
    callee node. The line reported is the first row that reached it.

    Returns a GraphResult; ``truncated``/``omitted`` report a node-cap or
    depth-cap refusal instead of dropping edges silently.
    """
    m = _manifest_for(core, slug)
    notes: list[str] = []
    omitted = 0
    depth_capped = False
    try:
        roots = [GraphNode(file=r.file, symbol=r.name, line=r.start_line,
                           relationship="definition", confidence="ast")
                 for r in m.find_symbols(name, substring=False)]
        seen: set[tuple] = {_root_key(n.file, n.symbol) for n in roots}
        frontier = list(roots)
        result: list[GraphNode] = list(roots)
        count = len(result)
        for level in range(max(0, depth)):
            next_frontier: list[GraphNode] = []
            for node in frontier:
                for (file, line, to, rel, conf, recv) in _expand_callees(
                        core, m, node.file, node.symbol):
                    key = _callee_key(file, to)
                    if key in seen:
                        continue
                    if count >= max_nodes:
                        seen.add(key)
                        omitted += 1
                        continue
                    seen.add(key)
                    count += 1
                    child = GraphNode(file=file, symbol=to, line=line,
                                      relationship=rel, confidence=conf,
                                      receiver=recv if level == 0 else None)
                    node.children.append(child)
                    next_frontier.append(child)
            frontier = next_frontier
        stopped_level = max(0, depth)
        for node in frontier:
            for (file, _line, to, _rel, _conf, _recv) in _expand_callees(
                    core, m, node.file, node.symbol):
                if _callee_key(file, to) not in seen:
                    depth_capped = True
                    break
            if depth_capped:
                break
        if omitted:
            notes.append(_cap_note(max_nodes, omitted, "callee"))
        if depth_capped:
            notes.append(_depth_note(stopped_level, "callees"))
        return GraphResult(nodes=result, notes=notes,
                           truncated=bool(omitted or depth_capped),
                           omitted=omitted)
    finally:
        m.close()


def render_tree(nodes: list[GraphNode]) -> str:
    lines: list[str] = []

    def walk(n: GraphNode, prefix: str) -> None:
        lines.append(prefix + n.label())
        for child in n.children:
            walk(child, prefix + "  ")

    for n in nodes:
        walk(n, "")
    return "\n".join(lines) if lines else "no results"


def graph_to_json(nodes: list[GraphNode]) -> dict:
    def node_dict(n: GraphNode) -> dict:
        return {
            "file": n.file, "symbol": n.symbol, "line": n.line,
            "relationship": n.relationship, "confidence": n.confidence,
            "receiver": n.receiver,
            "children": [node_dict(c) for c in n.children],
        }

    return {"roots": [node_dict(n) for n in nodes]}


def result_to_json(result: GraphResult) -> dict:
    """The JSON payload for one walk: `roots` unchanged, plus the top-level
    `truncated` / `omitted` siblings (and `notes` when there are any)."""
    payload = graph_to_json(result.nodes)
    payload["truncated"] = result.truncated
    payload["omitted"] = result.omitted
    if result.notes:
        payload["notes"] = list(result.notes)
    return payload


def deps_for_file(core, slug: str, path: str, direction: str = "both",
                  depth: int = 2, max_nodes: int = 60) -> dict:
    """Import graph around one file: `in` (importers), `out` (imports),
    or both. Returns {'in': [...], 'out': [...]} of file names per level,
    plus `truncated` / `omitted` / `notes` describing a refused edge."""
    m = _manifest_for(core, slug)
    notes: list[str] = []
    omitted = 0
    depth_capped = False
    try:
        all_rows = m.import_rows()

        def out_edges(f: str) -> list[str]:
            seen: set[str] = set()
            for r in all_rows:
                if r["file"] == f and r["resolved_file"]:
                    seen.add(r["resolved_file"])
            return sorted(seen)

        def in_edges(f: str) -> list[str]:
            seen: set[str] = set()
            for r in all_rows:
                if r["resolved_file"] == f and r["file"] != f:
                    seen.add(r["file"])
            return sorted(seen)

        want_out = direction in ("out", "both")
        want_in = direction in ("in", "both")

        def walk(start: str, edge_fn, levels: int) -> tuple[dict[int, list[str]], int, bool]:
            levels_out: dict[int, list[str]] = {}
            frontier = [start]
            seen: set[str] = {start}
            refused = 0
            for lvl in range(1, max(0, levels) + 1):
                nxt: list[str] = []
                for f in frontier:
                    for t in edge_fn(f):
                        if t in seen:
                            continue
                        if len(seen) >= max_nodes:
                            # Refused at the cap: counted, and remembered so a
                            # later level cannot recount the same file.
                            seen.add(t)
                            refused += 1
                            continue
                        seen.add(t)
                        nxt.append(t)
                if not nxt:
                    break
                levels_out[lvl] = sorted(nxt)
                frontier = nxt
            else:
                # Ran out of levels, not out of files: ask the store whether
                # the frontier still has an unvisited edge.
                for f in frontier:
                    if any(t not in seen for t in edge_fn(f)):
                        return levels_out, refused, True
            return levels_out, refused, False

        result: dict = {}
        for dir_key, edge_fn, wanted in (("out", out_edges, want_out),
                                         ("in", in_edges, want_in)):
            if not wanted:
                result[dir_key] = {}
                continue
            levels, refused, deeper = walk(path, edge_fn, depth)
            result[dir_key] = levels
            omitted += refused
            if refused:
                notes.append(_cap_note(max_nodes, refused, "import",
                                       scope=f" ({dir_key} direction)"))
            if deeper:
                depth_capped = True
                notes.append(_depth_note(max(0, depth),
                                         f"{dir_key} import edges"))
        result["truncated"] = bool(omitted or depth_capped)
        result["omitted"] = omitted
        if notes:
            result["notes"] = notes
        return result
    finally:
        m.close()
