"""C# references through the tree-sitter query (schema v6).

The C# corpus was entirely on the textual fallback: a property/field READ
through a member access (``a.OughtRoleBindings``) produced no reference at
all, and every row that did exist was confidence=heuristic. The query in
``queries/csharp/references.scm`` fixes that:

- a property read via member access yields a reference row with
  relationship=member and confidence=ast (NOT heuristic), carrying the
  receiver expression as written when it is a plain identifier;
- a plain qualified static call yields a caller row (relationship=call,
  receiver recorded);
- a name appearing only in a comment or a string does NOT match.

Offline: real chunker + query, stub embedder/store, no network.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer import graph_extract  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.indexer import Indexer  # noqa: E402
from code_indexer.manifest import Manifest  # noqa: E402

# Line numbers matter to the false-positive assertion below:
#   11,12  the comment and the string mention OughtRoleBindings
#   14     the real property read through a member access
CS_SOURCE = """\
using System;
using Estate.Sim.Psychology;

namespace Estate.Sim
{
    public class LegitimacySurface : BaseSurface
    {
        public double OughtRoleBindings { get; set; }

        public void Apply(Pawn pawn)
        {
            // OughtRoleBindings in a comment is NOT a reference
            string note = "OughtRoleBindings in a string is NOT a reference";
            var read = pawn.OughtRoleBindings;
            var classified = ArmorResolution.Classify(pawn);
            PlainCall();
        }
    }
}
"""


class StubEmbedder:
    def dimension(self):
        return 4

    def embed(self, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class StubStore:
    def collection_exists(self, name):
        return True

    def create_collection(self, name, dim):
        pass

    def upsert_points(self, name, points):
        pass

    def purge_file_points(self, *a, **kw):
        pass


@pytest.fixture()
def cs_manifest(tmp_path):
    """Index one C# fixture file through the real chunker + query."""
    project = tmp_path / "proj"
    src = project / "Vivarium.Sim"
    src.mkdir(parents=True)
    (tmp_path / "state").mkdir()
    (src / "LegitimacyDynamics.cs").write_text(CS_SOURCE)
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    manifest = Manifest(os.path.join(cfg.index_root, "m.db"))
    Indexer(cfg, StubEmbedder(), StubStore()).index_project(
        str(project), "cs", manifest)
    yield manifest
    manifest.close()


def test_csharp_language_is_ast_backed():
    assert "csharp" in graph_extract.LANGUAGES_WITH_AST


def test_csharp_member_read_is_an_ast_reference(cs_manifest):
    """Issue #5: a property read via member access is a real reference."""
    rows = cs_manifest.find_refs("OughtRoleBindings")
    assert rows, "the C# property read produced no reference row"
    row = rows[0]
    assert row.confidence == "ast", "still on the textual fallback"
    assert row.relationship == "member"
    assert row.receiver == "pawn"        # the receiver expression as written
    assert row.from_symbol == "Apply"


def test_csharp_qualified_static_call_yields_a_caller(cs_manifest):
    rows = cs_manifest.find_refs("Classify")
    assert rows, "the qualified static call produced no reference row"
    row = rows[0]
    assert row.confidence == "ast"
    assert row.relationship == "call"
    assert row.receiver == "ArmorResolution"


def test_csharp_plain_call_and_base_class(cs_manifest):
    assert any(r.relationship == "call"
               for r in cs_manifest.find_refs("PlainCall"))
    assert any(r.relationship == "base_class"
               for r in cs_manifest.find_refs("BaseSurface"))


def test_csharp_comment_and_string_mentions_do_not_match(cs_manifest):
    """The false-positive contract, applied to the C# grammar."""
    rows = cs_manifest.find_refs("OughtRoleBindings")
    assert [r.line for r in rows] == [14], rows


# ---------------------------------------------------------------------------
# item D: member-access precision
# ---------------------------------------------------------------------------

def test_csharp_intermediate_segment_is_not_a_reference():
    """Item D(a): only the OUTERMOST access of a chain is a reference."""
    src = (
        "class A\n"
        "{\n"
        "    void M(object x)\n"
        "    {\n"
        "        System.Console.WriteLine(x);\n"
        "        var v = System.Numerics.Vector3.Zero;\n"
        "    }\n"
        "}\n"
    )
    _imports, refs = graph_extract.extract_graph("A.cs", src, "csharp")
    names = {r[2] for r in refs}
    assert "WriteLine" in names
    assert "Zero" in names
    for intermediate in ("Console", "Numerics", "Vector3", "System"):
        assert intermediate not in names, (intermediate, sorted(names))
    # A deep chain's surviving receiver is NULL (the receiver expression is
    # itself a member access, not a plain identifier) — the stated trade-off.
    write_line = [r for r in refs if r[2] == "WriteLine"][0]
    assert write_line[3] == "call" and write_line[4] is None


def test_csharp_same_line_double_read_collapses_to_one_row():
    """Item D(b): the documented collapse — one row per (line, name, rel).

    Two same-name member reads on ONE line keep only the first receiver,
    because neither the in-memory dedupe key nor the table primary key
    includes ``receiver``. Two reads on separate lines are both kept.
    """
    one_line = (
        "class A\n"
        "{\n"
        "    void M()\n"
        "    {\n"
        "        var x = left.Foo; var y = right.Foo;\n"
        "    }\n"
        "}\n"
    )
    _imports, refs = graph_extract.extract_graph("A.cs", one_line, "csharp")
    foo = [r for r in refs if r[2] == "Foo"]
    assert len(foo) == 1, foo
    assert foo[0][3] == "member" and foo[0][4] == "left"

    two_lines = one_line.replace(
        "        var x = left.Foo; var y = right.Foo;\n",
        "        var x = left.Foo;\n"
        "        var y = right.Foo;\n")
    _imports2, refs2 = graph_extract.extract_graph("A.cs", two_lines, "csharp")
    foo2 = sorted((r[4] for r in refs2 if r[2] == "Foo"))
    assert foo2 == ["left", "right"], foo2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
