"""Reference/caller honesty against the SINGLE reference store (schema v6).

The index kept two reference tables with different writers and row shapes
(``symbol_refs`` and ``refs``); v6 collapses them into one ``refs`` table with
one writer (``graph_extract``), one honest relationship and confidence per
row, and a ``receiver`` column holding the member-access receiver as written.
The contract pinned here:

- ``find-references`` reports every reference to a name from that one store,
  labelled with the confidence the row actually carries; a bare
  ``no references`` appears only when the store has none;
- ``--relationship`` spans the documented vocabulary (calls|includes|
  inherits|references) mapped onto the stored relationships;
- ``find-callers`` accepts a dotted ``Type.Member`` subject, reports the
  subject once on the ``note:`` lines, and stamps a receiver on a direct hit
  only when the store actually holds one — never on transitive nodes;
- with no definition row, the BFS seeds from the reference rows and says so.

Offline and manifest-only: no Ollama, no Qdrant, no index pass.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer import graph  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.manifest import SymbolRow  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()

_RECEIVER_NOTE = ("receiver is shown on a direct hit only when the store "
                  "holds one")


@pytest.fixture()
def rig(tmp_path):
    """A registered project whose manifest the test writes by hand."""
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def ping():\n    return 1\n")
    core = Core(cfg, skip_stale_check=True)
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="refproj")
    core.registry = reg
    m = core.manifest_for(entry.slug)
    try:
        yield core, entry, m
    finally:
        m.close()
        reg.close()


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n.children)


# ---------------------------------------------------------------------------
# find-references: the single store
# ---------------------------------------------------------------------------

def test_references_reads_the_single_store(rig):
    """A member read that is never a call target is still reported."""
    core, entry, m = rig
    m.replace_file_refs(
        "Vivarium.Sim/Psychology/SelfDiscrepancy.cs",
        [(42, "Apply", "OughtRoleBindings", "member", "pawn")], "ast")
    rows = core.find_references(entry, "OughtRoleBindings")
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["confidence"] == "ast"
    assert row["relationship"] == "member"
    assert row["receiver"] == "pawn"
    assert (row["file"], row["line"], row["from_symbol"]) == (
        "Vivarium.Sim/Psychology/SelfDiscrepancy.cs", 42, "Apply")


def test_references_labelled_with_the_confidence_they_carry(rig):
    core, entry, m = rig
    m.replace_file_refs("a.py", [(3, "run", "ping", "call", None)], "ast")
    m.replace_file_refs("b.rs", [(5, None, "ping", "identifier", None)],
                        "heuristic")
    rows = core.find_references(entry, "ping")
    assert {r["confidence"] for r in rows} == {"ast", "heuristic"}, rows
    assert {r["relationship"] for r in rows} == {"call", "identifier"}


def test_relationship_filter_spans_the_vocabulary(rig):
    core, entry, m = rig
    m.replace_file_refs("a.py", [
        (3, "run", "ping", "call", None),
        (4, "run", "Widget", "base_class", None),
        (5, "run", "Thing", "type", None),
        (6, "run", "prop", "member", None)], "ast")
    calls = core.find_references(entry, "ping", relationship="calls")
    assert [r["relationship"] for r in calls] == ["call"]
    inherits = core.find_references(entry, "Widget", relationship="inherits")
    assert [r["relationship"] for r in inherits] == ["base_class"]
    refs_type = core.find_references(entry, "Thing", relationship="references")
    assert [r["relationship"] for r in refs_type] == ["type"]
    refs_member = core.find_references(entry, "prop",
                                       relationship="references")
    assert [r["relationship"] for r in refs_member] == ["member"]
    with pytest.raises(ValueError, match="unknown relationship"):
        core.find_references(entry, "ping", relationship="bogus")


def test_filtered_empty_report_names_the_unfiltered_count(rig, monkeypatch):
    """A `calls` filter with no call rows must not read as absent."""
    core, entry, m = rig
    m.replace_file_refs("a.py", [
        (1, None, "widget", "identifier", None),
        (2, None, "widget", "type", None),
    ], "heuristic")
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    res = runner.invoke(app, ["find-references", "widget", "--project",
                              entry.path, "--relationship", "calls"])
    assert res.exit_code == 0, res.output
    assert "no references to 'widget'" not in res.output
    assert "no calls references to 'widget'" in res.output
    assert "2 references exist unfiltered" in res.output
    assert "identifier" in res.output and "type" in res.output
    # A genuinely absent name still gets the bare line.
    miss = runner.invoke(app, ["find-references", "absent", "--project",
                               entry.path, "--relationship", "calls"])
    assert miss.exit_code == 0, miss.output
    assert "no references to 'absent'" in miss.output


def test_filtered_nonempty_report_is_unchanged(rig, monkeypatch):
    """The honesty branch only fires when the filtered result is EMPTY."""
    core, entry, m = rig
    m.replace_file_refs("a.py", [(3, "run", "ping", "call", None)], "ast")
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    res = runner.invoke(app, ["find-references", "ping", "--project",
                              entry.path, "--relationship", "calls"])
    assert res.exit_code == 0, res.output
    assert "references to 'ping'" in res.output
    assert "unfiltered" not in res.output


def test_cli_prints_no_references_only_when_the_store_is_empty(rig,
                                                               monkeypatch):
    core, entry, m = rig
    m.replace_file_refs("a.py", [(1, None, "widget", "identifier", None)],
                        "heuristic")
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    hit = runner.invoke(app, ["find-references", "widget",
                              "--project", entry.path])
    assert hit.exit_code == 0, hit.output
    assert "no references" not in hit.output
    assert "confidence=heuristic" in hit.output
    miss = runner.invoke(app, ["find-references", "nothing_here",
                               "--project", entry.path])
    assert miss.exit_code == 0, miss.output
    assert "no references to 'nothing_here'" in miss.output


# ---------------------------------------------------------------------------
# find-callers: a dotted subject, and the receiver honesty rule
# ---------------------------------------------------------------------------

def test_find_callers_dotted_subject_reports_the_store_receiver(rig):
    core, entry, m = rig
    m.replace_file_symbols(
        "Vivarium.Sim/Psychology/LegitimacyDynamics.cs",
        [SymbolRow("Vivarium.Sim/Psychology/LegitimacyDynamics.cs",
                   "LegitimacySurface", "class", 153, 174, "ast")])
    # The store holds a receiver for this direct member read.
    m.replace_file_refs(
        "Vivarium.Sim/Psychology/SelfDiscrepancy.cs",
        [(42, "Apply", "OughtRoleBindings", "member", "surface")], "ast")
    res = graph.find_callers(
        core, entry.slug, "LegitimacySurface.OughtRoleBindings")
    nodes, notes = res.nodes, res.notes
    assert nodes, "a dotted subject must resolve, not fail closed"
    hits = [n for n in _walk(nodes) if n.relationship != "definition"]
    assert hits, [n.label() for n in _walk(nodes)]
    # The receiver comes from the STORE (the row's own receiver), never from
    # the query's qualifier.
    assert all(n.receiver == "surface" for n in hits)
    assert any("qualifier 'LegitimacySurface' resolved" in s for s in notes)


def test_find_callers_never_stamps_a_receiver_the_store_lacks(rig):
    core, entry, m = rig
    m.replace_file_symbols("a.py", [SymbolRow("a.py", "Surface", "class",
                                              1, 9, "ast")])
    # The row carries no receiver: nothing may be asserted for it.
    m.replace_file_refs("b.py", [(4, "run", "Classify", "call", None)], "ast")
    res = graph.find_callers(core, entry.slug, "Surface.Classify")
    nodes, notes = res.nodes, res.notes
    hits = [n for n in _walk(nodes) if n.relationship != "definition"]
    assert hits, [n.label() for n in _walk(nodes)]
    assert all(n.receiver is None for n in hits)
    assert any(_RECEIVER_NOTE in s for s in notes)


def test_find_callers_seeds_from_reference_rows_without_a_definition(rig):
    core, entry, m = rig
    m.replace_file_refs(
        "Vivarium.Sim/Psychology/SelfDiscrepancy.cs",
        [(42, "Apply", "OughtRoleBindings", "member", "pawn")], "ast")
    assert m.find_symbols("OughtRoleBindings", substring=False) == []
    res = graph.find_callers(core, entry.slug, "OughtRoleBindings")
    nodes, notes = res.nodes, res.notes
    assert nodes, "must not print a bare 'no results' when rows exist"
    assert nodes[0].file == "Vivarium.Sim/Psychology/SelfDiscrepancy.cs"
    assert nodes[0].receiver == "pawn"
    assert notes and any("holds no definition" in s for s in notes)


def test_find_callers_explains_a_truly_unknown_name(rig):
    core, entry, m = rig
    res = graph.find_callers(core, entry.slug, "never_mentioned")
    nodes, notes = res.nodes, res.notes
    assert nodes == []
    assert any("nothing in this index mentions it" in s for s in notes)


def test_find_callers_cli_prints_the_note_not_a_bare_no_results(rig,
                                                               monkeypatch):
    core, entry, m = rig
    m.replace_file_refs("a.py", [(7, None, "ghost", "identifier", None)],
                        "heuristic")
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["find-callers", "ghost",
                                 "--project", entry.path])
    assert result.exit_code == 0, result.output
    assert "a.py:7" in result.output
    assert "note:" in result.output
    assert "no definition" in result.output


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
