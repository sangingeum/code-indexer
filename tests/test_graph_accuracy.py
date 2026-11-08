"""Graph accuracy: a type reached through a member access, an include that is
not a call, and a `deps` answer that never reads as a leaf.

The three contracts pinned here:

- a member access through a type (``NeedBand.Critical``) records the TYPE as
  a reference, not only the member row with the type as its receiver, so
  ``find-references`` can see it; a receiver that binds no definition (a
  local variable or a parameter) records NO type row;
- an empty reference answer has one of three honest sentences: rows found,
  no rows but a definition exists, or the name is unknown to this index —
  and the "absent" reading is never printed over a partly parsed tree;
- ``#include`` edges are importers, never callers: they are counted and
  printed in their own labelled section, not in the caller list or its
  count;
- ``deps`` names its depth levels in plain words, and a file whose includes
  did not resolve says so (counted and named) instead of reading as a leaf.

Offline: real chunker + queries, stub embedder and store, no network.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()

# `NeedBand` is a TYPE reached through a plain identifier receiver, and
# `other` / `level` are a parameter and a local — receivers that bind nothing.
CS_SOURCE = """\
namespace Vivarium.Sim
{
    public static class NeedBand
    {
        public static double Critical;
    }

    public class NeedsSystem
    {
        public void Update(NeedsSystem other)
        {
            var level = NeedBand.Critical;
            var shadow = other.Critical;
        }
    }

    public class NeverMentioned
    {
    }
}
"""

CPP_HEADER = """\
#pragma once
#include "present_sibling.hpp"
#include "missing_sibling.hpp"
#include <vector>
#include <asio.hpp>
"""

CPP_SOURCE = """\
#include "Edriel/Edriel.hpp"
int main() { return 0; }
"""

# Every directive here points outside the index: the file is a HEADER full
# of includes, and it must not read as a leaf.
LEAF_HEADER = """\
#pragma once
#include "gone_a.hpp"
#include <grpcpp/grpcpp.h>
#include <google/protobuf/message.h>
"""


def _rig(tmp_path, monkeypatch, files: dict[str, str], name: str = "graphproj"):
    """Index a project of literal files with a stub embedder/store."""
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    core = Core(cfg, skip_stale_check=True)

    class StubEmbedder:
        def dimension(self):
            return 4

        def embed(self, texts):
            return [[0.1] * 4 for _ in texts]

        def query_instruction_text(self, q):
            return q

        def embed_with_errors(self, texts):
            return [[0.1] * 4 for _ in texts], []

        def close(self):
            pass

    class StubStore:
        def collection_exists(self, name):
            return True

        def create_collection(self, name, dim):
            pass

        def upsert_points(self, name, points):
            pass

        def purge_file_points(self, *a, **kw):
            return 0

        def swap_collection(self, tmp, target):
            pass

        def count_points(self, name):
            return 0

        def search(self, *a, **kw):
            return []

    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name=name)
    core.registry = reg
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    return core, entry, project, reg


@pytest.fixture()
def csharp_project(tmp_path, monkeypatch):
    core, entry, project, reg = _rig(
        tmp_path, monkeypatch, {"Vivarium.Sim/NeedsSystem.cs": CS_SOURCE})
    yield core, entry, project
    reg.close()


# ---------------------------------------------------------------------------
# a type reached through a member access is a reference to the type
# ---------------------------------------------------------------------------

def test_member_access_through_a_type_records_the_type_as_a_reference(
        csharp_project):
    core, entry, _project = csharp_project
    m = core.read_manifest(entry.slug)
    try:
        # The member row keeps its receiver (unchanged behaviour)...
        members = m.refs_to("Critical")
        assert members, "the member row must still exist"
        assert {r["receiver"] for r in members} == {"NeedBand", "other"}, members
        # ...and the type itself is now a reference row, not only a receiver.
        types = m.refs_to("NeedBand")
        assert types, "the type is invisible to find-references"
        assert [r["relationship"] for r in types] == ["type"], types
        assert types[0]["line"] == 12 and types[0]["from_symbol"] == "Update"
        assert types[0]["confidence"] == "ast"
    finally:
        m.close()


def test_a_receiver_that_binds_no_definition_is_not_a_type_reference(
        csharp_project):
    """A parameter and a local variable are not types, however type-like the
    member they carry looks."""
    core, entry, _project = csharp_project
    m = core.read_manifest(entry.slug)
    try:
        assert m.refs_to("other") == []
        assert m.refs_to("level") == []
    finally:
        m.close()


def test_find_references_reports_the_type_itself(csharp_project):
    core, entry, project = csharp_project
    result = runner.invoke(app, ["find-references", "NeedBand", "--project",
                                 str(project)])
    assert result.exit_code == 0, result.output
    assert "NeedsSystem.cs:12" in result.stdout
    assert "type NeedBand" in result.stdout
    assert "the name is absent" not in result.stdout


# ---------------------------------------------------------------------------
# three states, three honest sentences
# ---------------------------------------------------------------------------

def test_no_rows_but_a_definition_says_the_definition_exists(
        csharp_project):
    core, entry, project = csharp_project
    result = runner.invoke(app, ["find-references", "NeverMentioned",
                                 "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert "the name is absent" not in result.stdout
    assert "no references to 'NeverMentioned'" in result.stdout
    assert "defines it at" in result.stdout
    assert "NeedsSystem.cs:" in result.stdout
    payload = json.loads(runner.invoke(
        app, ["find-references", "NeverMentioned", "--project", str(project),
              "--json"]).stdout)
    assert payload["empty_reason"] == "defined-not-referenced"
    assert "defines it at" in payload["empty_reason_sentence"]


def test_a_partly_parsed_tree_never_claims_the_name_is_absent(
        tmp_path, monkeypatch):
    core, entry, project, reg = _rig(tmp_path, monkeypatch, {
        "good.py": "def steady():\n    return 1\n",
        "broken.py": "def (",
    }, name="partial")
    try:
        result = runner.invoke(app, ["find-references", "ghost",
                                     "--project", str(project)])
        assert result.exit_code == 0, result.output
        assert "the name is absent" not in result.stdout
        assert "no definition or reference row" in result.stdout
        assert "parsed 1 of 2 files" in result.stdout
        payload = json.loads(runner.invoke(
            app, ["find-references", "ghost", "--project", str(project),
                  "--json"]).stdout)
        assert payload["coverage"]["complete"] is False
        assert "parsed 1 of 2 files" in payload["empty_reason_sentence"]
    finally:
        reg.close()


def test_a_fully_parsed_tree_may_say_the_name_is_absent(tmp_path,
                                                       monkeypatch):
    core, entry, project, reg = _rig(
        tmp_path, monkeypatch, {"good.py": "def steady():\n    return 1\n"},
        name="whole")
    try:
        result = runner.invoke(app, ["find-references", "ghost",
                                     "--project", str(project)])
        assert result.exit_code == 0, result.output
        assert "no definition or reference row" not in result.stdout
        assert "parsed all" in result.stdout
    finally:
        reg.close()


# ---------------------------------------------------------------------------
# a type named where a TYPE belongs is a reference
# ---------------------------------------------------------------------------

TYPE_USE_SOURCE = """\
namespace Vivarium.Sim
{
    public class NeedBand { }

    public class Needs
    {
        private NeedBand _held;
        public NeedBand Held { get; set; }

        public NeedBand Resolve(NeedBand level)
        {
            var byName = new Dictionary<string, NeedBand>();
            var cast = (NeedBand)level;
            var shaped = level as NeedBand;
            if (level is NeedBand typed) { }
            return level;
        }
    }
}
"""

# A name with no definition at all, and two VALUE bindings: none of them is a
# type, and no row may be invented for them.
NO_DEFINITION_SOURCE = """\
namespace Vivarium.Sim
{
    public class Holder
    {
        public void Run(Mystery ghost, int count)
        {
            var level = count;
        }
    }
}
"""


# One line that names `Foo` BOTH as a plain type (`Foo y;`) and as a
# qualified scope (`Foo::Callback`): two captures for one site, one key.
COLLIDING_TYPE_AND_SCOPE = """\
struct Foo { using Callback = void (*)(int); };
void f() { Foo::Callback cb; Foo y; }
"""


@pytest.fixture()
def type_use_project(tmp_path, monkeypatch):
    core, entry, project, reg = _rig(
        tmp_path, monkeypatch, {"Vivarium.Sim/Needs.cs": TYPE_USE_SOURCE},
        name="typeuse")
    yield core, entry, project
    reg.close()


def test_a_type_named_in_a_type_position_is_a_reference(type_use_project):
    """A field, a property, a parameter, a generic argument, a cast, an `as`
    and an `is` pattern all name the type, and the receiver rule saw none."""
    core, entry, _project = type_use_project
    m = core.read_manifest(entry.slug)
    try:
        rows = m._conn.execute(
            "SELECT line, relationship, confidence, resolution,"
            " target_symbol_id FROM refs WHERE to_name = 'NeedBand'"
            " ORDER BY line").fetchall()
    finally:
        m.close()
    assert rows, "a type in a type position is invisible to find-references"
    assert {r[1] for r in rows} == {"type"}, rows
    assert {r[2] for r in rows} == {"ast"}, rows
    assert {r[3] for r in rows} == {"same_file"}, rows
    assert all(r[4] is not None for r in rows), rows
    # field, property, parameter/return (one row: same line and name), generic
    # argument, cast, `as`, `is` -- one row per site.
    assert sorted({r[0] for r in rows}) == [7, 8, 10, 12, 13, 14, 15], rows


def test_a_name_that_resolves_to_nothing_is_no_type_row(tmp_path, monkeypatch):
    """No definition, no row: a type position is not licence to invent one."""
    core, entry, _project, reg = _rig(
        tmp_path, monkeypatch, {"Vivarium.Sim/Holder.cs": NO_DEFINITION_SOURCE},
        name="nores")
    try:
        m = core.read_manifest(entry.slug)
        try:
            assert m.refs_to("Mystery") == []
            # A parameter and a local are value bindings, never type rows.
            assert m.refs_to("count") == []
            assert m.refs_to("level") == []
        finally:
            m.close()
    finally:
        reg.close()


def test_an_ambiguous_type_use_stays_unattributed_and_counted(
        tmp_path, monkeypatch):
    """Two definitions, no file-class tie-breaker: the row is a type use whose
    target is unknown -- kept and counted, never guessed and never dropped."""
    core, entry, _project, reg = _rig(tmp_path, monkeypatch, {
        "alpha/Shared.cs": "namespace Alpha { public class Shared { } }\n",
        "beta/Shared.cs": "namespace Beta { public class Shared { } }\n",
        "Use.cs": ("namespace Uses { public class User {"
                   " public void Run(Shared s) { } } }\n"),
    }, name="ambiguous")
    try:
        m = core.read_manifest(entry.slug)
        try:
            rows = m._conn.execute(
                "SELECT relationship, resolution, target_symbol_id"
                " FROM refs WHERE file = 'Use.cs' AND to_name = 'Shared'"
            ).fetchall()
        finally:
            m.close()
    finally:
        reg.close()
    assert rows == [("type", "none", None)], rows


def test_one_site_naming_a_type_twice_still_indexes(tmp_path, monkeypatch):
    """A plain type and a qualified scope on one line share one row key.

    `Foo::Callback cb; Foo y;` gives the site two captures for `Foo` -- one
    plain type, one scope. They are one (file, line, name, relationship) row,
    so converting the interim row must not collide with the type row already
    written there, and must not abort the pass.
    """
    core, entry, _project, reg = _rig(
        tmp_path, monkeypatch, {"a.cpp": COLLIDING_TYPE_AND_SCOPE},
        name="collide")
    try:
        m = core.read_manifest(entry.slug)
        try:
            rows = m._conn.execute(
                "SELECT relationship, resolution FROM refs"
                " WHERE file = 'a.cpp' AND to_name = 'Foo' AND line = 2"
            ).fetchall()
        finally:
            m.close()
    finally:
        reg.close()
    assert rows == [("type", "same_file")], rows


# ---------------------------------------------------------------------------
# an include is an importer, not a caller
# ---------------------------------------------------------------------------

def _call_and_import_project(tmp_path, monkeypatch):
    return _rig(tmp_path, monkeypatch, {
        "defs.py": "def probe():\n    return 1\n",
        "importer.py": "from defs import probe\n",
        "user.py": "from defs import probe\n\ndef go():\n    probe()\n",
    }, name="callers")


def test_import_edges_do_not_occupy_the_caller_list(tmp_path, monkeypatch):
    core, entry, project, reg = _call_and_import_project(tmp_path, monkeypatch)
    try:
        payload = json.loads(runner.invoke(
            app, ["find-callers", "probe", "--project", str(project),
                  "--json"]).stdout)
        roots = payload["roots"]
        assert [r["file"] for r in roots] == ["defs.py"], roots
        hits = [n for n in _walk(roots) if n["relationship"] != "definition"]
        assert hits, roots
        assert all(h["relationship"] == "call" for h in hits), hits
        assert payload["importer_count"] == 2, payload
        assert sorted(i["file"] for i in payload["importers"]) == [
            "importer.py", "user.py"]

        text = runner.invoke(app, ["find-callers", "probe", "--project",
                                   str(project)])
        assert text.exit_code == 0, text.output
        assert "[import:" not in text.stdout
        assert "importers (2)" in text.stdout
    finally:
        reg.close()


def test_find_references_keeps_imports_out_of_the_reference_count(
        tmp_path, monkeypatch):
    core, entry, project, reg = _call_and_import_project(tmp_path, monkeypatch)
    try:
        payload = json.loads(runner.invoke(
            app, ["find-references", "probe", "--project", str(project),
                  "--json"]).stdout)
        rows = payload["references"]
        assert [r["relationship"] for r in rows] == ["call"], rows
        assert payload["total"] == 1
        assert payload["importer_count"] == 2, payload
        assert sorted(i["file"] for i in payload["importers"]) == [
            "importer.py", "user.py"]

        text = runner.invoke(app, ["find-references", "probe", "--project",
                                   str(project)])
        assert text.exit_code == 0, text.output
        assert "includes probe" not in text.stdout
        assert "importers (2)" in text.stdout
        # The heading says what its rows ARE: files importing the file that
        # defines the subject, not directives naming the subject. The rows
        # carry the defining file's path, which the old heading mislabelled.
        assert ("importers (2) — files that import the file defining "
                "'probe'; an import is not a reference:") in text.stdout
        # The rows name the importing FILE and the directive's target, which
        # the old heading ("directives naming 'probe'") mislabelled.
        assert "- importer.py:1 import:import defs" in text.stdout

        # An explicit include filter still answers, on the name the directive
        # actually carries (a python import names its module).
        only = runner.invoke(app, ["find-references", "defs", "--project",
                                   str(project), "--relationship",
                                   "includes"])
        assert only.exit_code == 0, only.output
        assert "includes defs" in only.stdout
    finally:
        reg.close()


# ---------------------------------------------------------------------------
# deps names its levels and never reads as a leaf
# ---------------------------------------------------------------------------

def _include_project(tmp_path, monkeypatch):
    return _rig(tmp_path, monkeypatch, {
        "Edriel/Edriel.hpp": CPP_HEADER,
        "Edriel/leaf.hpp": LEAF_HEADER,
        "Edriel/present_sibling.hpp": "#pragma once\n",
        "main.cpp": CPP_SOURCE,
    }, name="includes")


def test_deps_names_its_depth_levels_plainly(tmp_path, monkeypatch):
    core, entry, project, reg = _include_project(tmp_path, monkeypatch)
    try:
        text = runner.invoke(app, ["deps", "main.cpp", "--project",
                                   str(project), "--direction", "out"])
        assert text.exit_code == 0, text.output
        assert " L1:" not in text.stdout and " L2:" not in text.stdout
        assert "imports of main.cpp (depth 1)" in text.stdout
        assert "Edriel/Edriel.hpp" in text.stdout

        payload = json.loads(runner.invoke(
            app, ["deps", "main.cpp", "--project", str(project),
                  "--direction", "out", "--format", "json"]).stdout)
        assert payload["imports"]["1"] == ["Edriel/Edriel.hpp"], payload
        assert "out" not in payload
        assert "in" not in payload
    finally:
        reg.close()


def test_deps_says_why_a_leaf_header_is_not_a_leaf(tmp_path, monkeypatch):
    """No resolved outbound edge: the answer names the unresolved includes."""
    core, entry, project, reg = _include_project(tmp_path, monkeypatch)
    try:
        text = runner.invoke(app, ["deps", "Edriel/leaf.hpp", "--project",
                                   str(project), "--direction", "out"])
        assert text.exit_code == 0, text.output
        assert "no resolved dependencies" not in text.stdout
        assert "none resolved" in text.stdout
        assert "3 include directives unresolved" in text.stdout
        assert "gone_a.hpp" in text.stdout and "grpcpp/grpcpp.h" in text.stdout
        placed = runner.invoke(app, ["deps", "Edriel/leaf.hpp", "--project",
                                     str(project), "--direction", "in"])
        assert "importers of Edriel/leaf.hpp: none" in placed.stdout
    finally:
        reg.close()


def test_deps_names_the_includes_a_header_could_not_resolve(
        tmp_path, monkeypatch):
    """A resolved out-edge AND unresolved includes: both are reported."""
    core, entry, project, reg = _include_project(tmp_path, monkeypatch)
    try:
        text = runner.invoke(app, ["deps", "Edriel/Edriel.hpp", "--project",
                                   str(project), "--direction", "out"])
        assert text.exit_code == 0, text.output
        assert "missing_sibling.hpp" in text.stdout and "asio.hpp" in text.stdout
        assert "3 directives (<asio.hpp>, <vector>, missing_sibling.hpp)" \
            in text.stdout
        payload = json.loads(runner.invoke(
            app, ["deps", "Edriel/Edriel.hpp", "--project", str(project),
                  "--direction", "out", "--format", "json"]).stdout)
        unresolved = payload["unresolved_imports"]["Edriel/Edriel.hpp"]
        assert sorted(unresolved) == ["<asio.hpp>", "<vector>",
                                      "missing_sibling.hpp"], sorted(unresolved)
    finally:
        reg.close()


def test_a_template_call_site_is_a_caller(tmp_path, monkeypatch):
    """`obj.method<T>(...)` and `free<T>(...)` are calls, not silence.

    The name sits one node deeper (template_method / template_function), so a
    plain identifier/field pattern read the call site as nothing and the two
    real callers of a template API stayed invisible.
    """
    core, entry, project, reg = _rig(tmp_path, monkeypatch, {
        "api.cpp": (
            "template<typename T> void publish(const char* t, bool r);\n"
            "void user(Client* client) {\n"
            "    client->publish<int>(\"x\", true);\n"
            "    publish<double>(\"y\");\n"
            "}\n"),
    }, name="templates")
    try:
        m = core.read_manifest(entry.slug)
        try:
            rows = m.refs_to("publish")
        finally:
            m.close()
        calls = [r for r in rows if r["relationship"] == "call"]
        assert [r["line"] for r in calls] == [3, 4], rows
    finally:
        reg.close()


def test_deps_keeps_a_second_depth_level(tmp_path, monkeypatch):
    core, entry, project, reg = _rig(tmp_path, monkeypatch, {
        "chain/a.py": "import chain.b\n",
        "chain/b.py": "import chain.c\n",
        "chain/c.py": "VALUE = 1\n",
    }, name="chain")
    try:
        payload = json.loads(runner.invoke(
            app, ["deps", "chain/a.py", "--project", str(project),
                  "--direction", "out", "--depth", "2",
                  "--format", "json"]).stdout)
        assert payload["imports"]["1"] == ["chain/b.py"], payload
        assert payload["imports"]["2"] == ["chain/c.py"], payload
        text = runner.invoke(app, ["deps", "chain/a.py", "--project",
                                   str(project), "--direction", "out"])
        assert "imports of chain/a.py (depth 2)" in text.stdout
    finally:
        reg.close()


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node["children"])


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
