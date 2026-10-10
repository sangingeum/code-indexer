"""Graph queries: find-callers / find-callees / deps (graph work item).

Walks the manifest's single reference store (``refs``) and the imports table
breadth-first with depth and node caps. Cycle-safe: visited set keyed on
(file, symbol). Output is an indented compact tree; JSON carries the edges.

A node's ``receiver`` is the receiver expression of the reference AS WRITTEN
(a plain identifier on a member access, else NULL). It is taken from the
stored row — never inferred from the query — and is reported only on the
DIRECT hits of the subject (the level-0 fan-in/fan-out edges); transitive
nodes carry no receiver, because the store holds none for them.
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


def _manifest_for(core, slug: str) -> Manifest:
    return core.manifest_for(slug)


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
                 max_nodes: int = 40) -> tuple[list[GraphNode], list[str]]:
    """BFS over fan-in edges for the subject ``name``.

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

    Returns ``(nodes, notes)``; ``notes`` are plain-language lines for the
    caller to print.
    """
    m = _manifest_for(core, slug)
    notes: list[str] = []
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
            return seeds, notes
        seen: set[tuple[str, str | None]] = {
            (n.file, n.symbol) for n in roots}
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
                    key = (file, frm)
                    if key in seen or count >= max_nodes:
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
        return result, notes
    finally:
        m.close()


def find_callees(core, slug: str, name: str, depth: int = 2,
                 max_nodes: int = 40) -> list[GraphNode]:
    """BFS over fan-out edges rooted at every definition of `name`."""
    m = _manifest_for(core, slug)
    try:
        roots = [GraphNode(file=r.file, symbol=r.name, line=r.start_line,
                           relationship="definition", confidence="ast")
                 for r in m.find_symbols(name, substring=False)]
        seen: set[tuple[str, str | None]] = {(n.file, n.symbol)
                                             for n in roots}
        frontier = list(roots)
        result: list[GraphNode] = list(roots)
        count = len(result)
        for level in range(max(0, depth)):
            next_frontier: list[GraphNode] = []
            for node in frontier:
                for (file, line, to, rel, conf, recv) in _expand_callees(
                        core, m, node.file, node.symbol):
                    key = (file, to)
                    if key in seen or count >= max_nodes:
                        continue
                    seen.add(key)
                    count += 1
                    child = GraphNode(file=file, symbol=to, line=line,
                                      relationship=rel, confidence=conf,
                                      receiver=recv if level == 0 else None)
                    node.children.append(child)
                    next_frontier.append(child)
            frontier = next_frontier
        return result
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


def deps_for_file(core, slug: str, path: str, direction: str = "both",
                  depth: int = 2, max_nodes: int = 60) -> dict:
    """Import graph around one file: `in` (importers), `out` (imports),
    or both. Returns {'in': [...], 'out': [...]} of file names per level."""
    m = _manifest_for(core, slug)
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

        def walk(start: str, edge_fn, levels: int) -> dict[int, list[str]]:
            levels_out: dict[int, list[str]] = {}
            frontier = [start]
            seen: set[str] = {start}
            for lvl in range(1, max(0, levels) + 1):
                nxt: list[str] = []
                for f in frontier:
                    for t in edge_fn(f):
                        if t not in seen and len(seen) < max_nodes:
                            seen.add(t)
                            nxt.append(t)
                if not nxt:
                    break
                levels_out[lvl] = sorted(nxt)
                frontier = nxt
            return levels_out

        return {
            "out": walk(path, out_edges, depth) if want_out else {},
            "in": walk(path, in_edges, depth) if want_in else {},
        }
    finally:
        m.close()
