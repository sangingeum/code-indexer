"""A header spelled `.h` is read as the language its CONTENTS are written in.

A header's name does not say what it declares: C++ projects spell their
headers `.h` as often as `.hpp`. Reading every `.h` as C made a C++ header's
declarations invisible while its file row said `parsed` -- the false-absence
class the whole accuracy round exists to close. tree-sitter's cpp grammar
parses a C header too, so both directions are pinned here: a C-only header
still answers, a C++ header answers, and only a name that really is nowhere
in the tree is called absent.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from code_indexer import ts_chunker  # noqa: E402
import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.indexer import _lang_from_ext  # noqa: E402
from code_indexer.core import _lang_from_path  # noqa: E402
from pass_rig import index_project, stub_core  # noqa: E402

runner = CliRunner()

# C, written in a header named .h: the shape a C project ships.
C_HEADER = """\
#ifndef POINT_H
#define POINT_H

struct Point {
    int x;
};

int add(int a, int b);

#endif
"""

# C++, written in a header named .h: the shape a C++ project ships.
CPP_HEADER = """\
#pragma once

namespace geometry {

class Widget {
public:
    void run();
};

}
"""

CPP_SOURCE = """\
#include "cpp_header.h"

void use() {
    Widget w;
    w.run();
}
"""

C_SOURCE = """\
#include "c_only.h"

int twice(int n) { return add(n, n); }
"""


@pytest.fixture()
def header_project(tmp_path, monkeypatch):
    project = tmp_path / "headers"
    project.mkdir()
    for name, text in (("c_only.h", C_HEADER), ("cpp_header.h", CPP_HEADER),
                       ("use.cpp", CPP_SOURCE), ("use.c", C_SOURCE)):
        (project / name).write_text(text, encoding="utf-8")
    core = stub_core(tmp_path)
    reg, entry = index_project(core, project, name="headers")
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    yield core, entry, project
    reg.close()


def _files(core, entry) -> dict[str, tuple[str, str]]:
    m = core.manifest_for(entry.slug)
    try:
        return {path: (row.language, row.parse_verdict)
                for path, row in m.all_files().items()}
    finally:
        m.close()


# ---------------------------------------------------------------------------
# the mapping itself: a header is not C because of its name
# ---------------------------------------------------------------------------

def test_every_header_spelling_maps_to_one_language():
    """The chunker's map, the indexer's and the query map agree on headers."""
    for ext in ("h", "hpp"):
        assert ts_chunker.EXT_LANG[ext] == "cpp", ext
    for ext in ("h", "hpp", "hxx", "hh"):
        assert _lang_from_ext(f"a.{ext}") == "cpp", ext
    for ext in ("h", "hpp", "cc"):
        assert _lang_from_path(f"a.{ext}") == "cpp", ext


def test_a_header_is_stored_as_cpp_and_parsed(header_project):
    core, entry, _project = header_project
    files = _files(core, entry)
    assert files["c_only.h"] == ("cpp", "parsed"), files
    assert files["cpp_header.h"] == ("cpp", "parsed"), files
    assert files["use.cpp"] == ("cpp", "parsed"), files


def test_a_c_only_header_still_answers(header_project):
    """The cpp grammar reads C: nothing a C project ships becomes invisible.

    The declaration is a struct, because a chunk is a DECLARATION WITH A BODY:
    a bare prototype (`int add(int, int);`) is neither a symbol row nor a
    chunk in either grammar, so it is not what this property is about.
    """
    core, entry, project = header_project
    m = core.manifest_for(entry.slug)
    try:
        names = {s.name for s in m.symbols_for_file("c_only.h")}
    finally:
        m.close()
    assert "Point" in names, names

    result = runner.invoke(app, ["find-definition", "Point", "--project",
                                 str(project)])
    assert result.exit_code == 0, result.output
    assert "c_only.h" in result.stdout, result.stdout


def test_a_cpp_header_declaration_is_visible(header_project):
    """The case that motivated the remap: a C++ class in a `.h` header."""
    core, entry, project = header_project
    m = core.manifest_for(entry.slug)
    try:
        names = {s.name for s in m.symbols_for_file("cpp_header.h")}
        refs = m.refs_to("Widget")
    finally:
        m.close()
    assert "Widget" in names, names
    assert any(r["file"] == "use.cpp" for r in refs), refs

    result = runner.invoke(app, ["find-definition", "Widget", "--project",
                                 str(project)])
    assert result.exit_code == 0, result.output
    assert "cpp_header.h" in result.stdout, result.stdout

    found = runner.invoke(app, ["find-symbol", "Widget", "--project",
                                str(project), "--json"])
    assert found.exit_code == 0, found.output
    payload = json.loads(found.stdout)
    assert payload["match_count"] >= 1, payload
    assert payload["coverage"]["complete"] is True, payload["coverage"]


def test_a_name_really_absent_still_says_so(header_project):
    """The remap must not buy visibility by weakening the absence claim."""
    core, entry, project = header_project
    result = runner.invoke(app, ["find-symbol", "zzz_not_declared_here",
                                 "--project", str(project), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["match_count"] == 0
    assert payload["empty_reason"] == "name-absent"
    assert payload["coverage"]["complete"] is True, payload["coverage"]
    assert "the name is absent from an index that parsed 4 of 4 files" \
        in payload["empty_reason_sentence"]
