"""A header spelled `.h` is read as the language its CONTENTS are written in.

A header's name does not say what it declares: C++ projects spell their
headers `.h` as often as `.hpp`, and C projects spell them `.h` always. So the
grammar is decided per file, from the file (C first; cpp when C's tree carries
an error or a missing node), and the index STORES the language that parse
used. The properties pinned here are the two false absences that decision
buys: a C-only header (a K&R definition) keeps every symbol and stays
`parsed`, and `--language c` finds a symbol that lives in a C header; a C++
header answers under cpp; and a name that really is nowhere is still called
absent. `.hpp/.hxx/.hh` are not ambiguous and stay cpp.
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

# C with a K&R definition -- legal C, not C++: the construct that made a
# `.h` read as cpp lose a symbol and turn into `parse_error`.
KNR_HEADER = """\
#ifndef KNR_H
#define KNR_H

struct Kept {
    int value;
};

int old_style(a)
int a;
{
    return a;
}

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
    for name, text in (("c_only.h", C_HEADER), ("knr.h", KNR_HEADER),
                       ("cpp_header.h", CPP_HEADER),
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


def _symbols(core, entry, path: str) -> set[str]:
    m = core.manifest_for(entry.slug)
    try:
        return {s.name for s in m.symbols_for_file(path)}
    finally:
        m.close()


# ---------------------------------------------------------------------------
# the decision is made per file, and the language stored is the one it used
# ---------------------------------------------------------------------------

def test_a_header_is_stored_in_the_language_its_contents_are_written_in(
        header_project):
    """A C header stores `c`, a C++ header stores `cpp` -- both parsed."""
    core, entry, _project = header_project
    files = _files(core, entry)
    assert files["c_only.h"] == ("c", "parsed"), files
    assert files["knr.h"] == ("c", "parsed"), files
    assert files["cpp_header.h"] == ("cpp", "parsed"), files
    assert files["use.c"] == ("c", "parsed"), files
    assert files["use.cpp"] == ("cpp", "parsed"), files


def test_a_c_header_with_a_knr_definition_keeps_every_symbol_and_parses(
        header_project):
    """The C-only construct a filename policy cannot see: a K&R definition.

    Read as cpp, this header reported `parse_error` and `old_style` vanished
    from the symbol table -- the C-side false absence the per-file decision
    exists to remove.
    """
    core, entry, _project = header_project
    names = _symbols(core, entry, "knr.h")
    assert names == {"Kept", "old_style"}, names
    assert _files(core, entry)["knr.h"] == ("c", "parsed")


def test_a_c_only_header_still_answers(header_project):
    """A C header's declaration answers find-definition, as it did before."""
    core, entry, project = header_project
    result = runner.invoke(app, ["find-definition", "Point", "--project",
                                 str(project)])
    assert result.exit_code == 0, result.output
    assert "c_only.h" in result.stdout, result.stdout


def test_find_symbol_with_language_c_finds_a_symbol_in_a_c_header(
        header_project):
    """`--language c` answers from the grammar the C header was read with."""
    core, entry, project = header_project
    result = runner.invoke(app, ["find-symbol", "Point", "--project",
                                 str(project), "--language", "c", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["match_count"] >= 1, payload
    assert {row["file"] for row in payload["symbols"]} >= {"c_only.h"}, payload


def test_find_symbol_with_language_c_finds_a_knr_definition(
        header_project):
    """The symbol the cpp grammar lost is visible under the language it has."""
    core, entry, project = header_project
    result = runner.invoke(app, ["find-symbol", "old_style", "--project",
                                 str(project), "--language", "c", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["match_count"] == 1, payload
    assert payload["symbols"][0]["file"] == "knr.h", payload


def test_a_cpp_header_is_stored_as_cpp_and_parsed(header_project):
    """The case that motivated the remap: a C++ class in a `.h` header."""
    core, entry, project = header_project
    names = _symbols(core, entry, "cpp_header.h")
    m = core.manifest_for(entry.slug)
    try:
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
                                str(project), "--language", "cpp", "--json"])
    assert found.exit_code == 0, found.output
    payload = json.loads(found.stdout)
    assert payload["match_count"] >= 1, payload
    assert payload["coverage"]["complete"] is True, payload["coverage"]


def test_a_header_spelling_that_is_unambiguous_stays_cpp(tmp_path):
    """`.hpp/.hxx/.hh` name one language; only `.h` is decided from contents."""
    assert ts_chunker.AMBIGUOUS_HEADER_EXTENSIONS == {"h"}
    for ext in ("hpp", "hxx", "hh"):
        assert ts_chunker.EXT_LANG[ext] == "cpp", ext
        lang, _tree, verdict = ts_chunker.parse_tree_with_language(
            f"classy.{ext}", CPP_HEADER)
        assert (lang, verdict) == ("cpp", ts_chunker.PARSE_PARSED), (lang, verdict)


def test_a_name_really_absent_still_says_so(header_project):
    """The decision must not buy visibility by weakening the absence claim."""
    core, entry, project = header_project
    result = runner.invoke(app, ["find-symbol", "zzz_not_declared_here",
                                 "--project", str(project), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["match_count"] == 0
    assert payload["empty_reason"] == "name-absent"
    assert payload["coverage"]["complete"] is True, payload["coverage"]
    assert "the name is absent from an index that parsed 5 of 5 files" \
        in payload["empty_reason_sentence"]
