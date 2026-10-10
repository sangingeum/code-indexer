"""Reference/caller honesty: the two reference stores and the caller subject.

The index keeps TWO reference tables with different writers and consumers —
`symbol_refs` (invocation targets: calls/includes, every language) and `refs`
(AST call/type/base_class rows plus the textual `identifier` fallback). The
regressions pinned here:

- `find-references` must read BOTH stores, so a name that is referenced but
  never invoked as `name(` is not reported as "no references";
- a bare "no references" appears only when both stores are empty;
- `find-callers` accepts a dotted `Type.Member` subject and reports the
  receiver, and seeds from `refs` (with a plain explanation) when the index
  holds no definition row for the name.

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
from code_indexer.manifest import RefRow, SymbolRow  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()


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
# find-references: the union of both stores
# ---------------------------------------------------------------------------

def test_references_reads_the_refs_table_too(rig):
    """A name only in `refs` (never a call target) is still reported."""
    core, entry, m = rig
    m.replace_file_refs_heuristic(
        "Vivarium.Sim/Psychology/SelfDiscrepancy.cs",
        [(42, "Apply", "OughtRoleBindings", "identifier")])
    # The other store genuinely has nothing for this name: that is exactly
    # the old under-report (find-references read symbol_refs alone).
    assert m.find_refs("OughtRoleBindings") == []
    rows = core.find_references(entry, "OughtRoleBindings")
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["store"] == "refs"
    assert row["confidence"] == "heuristic"
    assert row["relationship"] == "identifier"
    assert (row["file"], row["line"], row["from_symbol"]) == (
        "Vivarium.Sim/Psychology/SelfDiscrepancy.cs", 42, "Apply")


def test_references_unions_both_stores_with_labels(rig):
    core, entry, m = rig
    # refs: an AST call row (python is in LANGUAGES_WITH_AST).
    m.replace_file_refs("a.py", [(3, "run", "ping", "call")])
    # symbol_refs: the regex invocation-target row for the same call.
    m.replace_file_symbols("a.py", [], [RefRow("a.py", 3, None, "calls", "ping")])
    rows = core.find_references(entry, "ping")
    by_store = {r["store"]: r for r in rows}
    assert set(by_store) == {"refs", "symbol_refs"}, rows
    assert by_store["refs"]["relationship"] == "call"
    assert by_store["refs"]["confidence"] == "ast"
    assert by_store["symbol_refs"]["relationship"] == "calls"
    assert by_store["symbol_refs"]["confidence"] == "heuristic"


def test_relationship_filter_spans_both_vocabularies(rig):
    core, entry, m = rig
    m.replace_file_refs("a.py", [(3, "run", "ping", "call")])
    m.replace_file_symbols("a.py", [], [RefRow("a.py", 3, None, "calls", "ping")])
    calls = core.find_references(entry, "ping", relationship="calls")
    assert {r["store"] for r in calls} == {"refs", "symbol_refs"}, calls
    # 'references' selects the non-call rows (type/identifier); only call rows
    # exist here, so it is legitimately empty.
    assert core.find_references(entry, "ping", relationship="references") == []
    with pytest.raises(ValueError, match="unknown relationship"):
        core.find_references(entry, "ping", relationship="bogus")


def test_cli_prints_no_references_only_when_both_stores_are_empty(rig,
                                                                 monkeypatch):
    core, entry, m = rig
    m.replace_file_refs_heuristic("a.py", [(1, None, "widget", "identifier")])
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    hit = runner.invoke(app, ["find-references", "widget",
                              "--project", entry.path])
    assert hit.exit_code == 0, hit.output
    assert "no references" not in hit.output
    assert "store=refs" in hit.output
    miss = runner.invoke(app, ["find-references", "nothing_here",
                               "--project", entry.path])
    assert miss.exit_code == 0, miss.output
    assert "no references to 'nothing_here'" in miss.output


# ---------------------------------------------------------------------------
# find-callers: a dotted subject, and the no-definition case
# ---------------------------------------------------------------------------

def test_find_callers_resolves_a_dotted_subject(rig):
    core, entry, m = rig
    m.replace_file_symbols(
        "Vivarium.Sim/Psychology/LegitimacyDynamics.cs",
        [SymbolRow("Vivarium.Sim/Psychology/LegitimacyDynamics.cs",
                   "LegitimacySurface", "class", 153, 174, "ast")], [])
    m.replace_file_refs_heuristic(
        "Vivarium.Sim/Psychology/SelfDiscrepancy.cs",
        [(42, "Apply", "OughtRoleBindings", "identifier")])
    nodes, notes = graph.find_callers(
        core, entry.slug, "LegitimacySurface.OughtRoleBindings")
    assert nodes, "a dotted subject must resolve, not fail closed"
    # The root is the qualifier's definition; every fan-in hit carries the
    # receiver so a shared member name is not misread as another type's.
    hits = [n for n in _walk(nodes) if n.relationship != "definition"]
    assert hits, [n.label() for n in _walk(nodes)]
    assert all(n.receiver == "LegitimacySurface" for n in hits)
    assert any("qualifier 'LegitimacySurface' resolved" in s for s in notes)


def test_find_callers_seeds_from_refs_when_there_is_no_definition(rig):
    core, entry, m = rig
    m.replace_file_refs_heuristic(
        "Vivarium.Sim/Psychology/SelfDiscrepancy.cs",
        [(42, "Apply", "OughtRoleBindings", "identifier")])
    assert m.find_symbols("OughtRoleBindings", substring=False) == []
    nodes, notes = graph.find_callers(core, entry.slug, "OughtRoleBindings")
    assert nodes, "must not print a bare 'no results' when refs has rows"
    assert nodes[0].file == "Vivarium.Sim/Psychology/SelfDiscrepancy.cs"
    assert notes and any("holds no definition" in s for s in notes)


def test_find_callers_explains_a_truly_unknown_name(rig):
    core, entry, m = rig
    nodes, notes = graph.find_callers(core, entry.slug, "never_mentioned")
    assert nodes == []
    assert any("nothing in this index mentions it" in s for s in notes)


def test_find_callers_cli_prints_the_note_not_a_bare_no_results(rig,
                                                                monkeypatch):
    core, entry, m = rig
    m.replace_file_refs_heuristic("a.py", [(7, None, "ghost", "identifier")])
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["find-callers", "ghost",
                                 "--project", entry.path])
    assert result.exit_code == 0, result.output
    assert "a.py:7" in result.output
    assert "note:" in result.output
    assert "no definition" in result.output


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
