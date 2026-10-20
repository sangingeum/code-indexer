"""Graph extraction + graph command tests (dependency/call-graph work item).

Fixtures carry KNOWN edges; the false-positive test pins that names appearing
only in comments/strings are NOT references (AST path). All offline.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import json  # noqa: E402

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.graph_extract import (  # noqa: E402
    extract_graph, extract_graph_textual, resolve_imports)

runner = CliRunner()


# ---------------------------------------------------------------------------
# extraction units
# ---------------------------------------------------------------------------

PY_SRC = (
    "import os\n"
    "from .helpers import refresh_token\n"
    "from helpers import other_fn\n"
    "\n"
    "class Session(Base):\n"
    "    def connect(self):\n"
    "        refresh_token()\n"
    "        other_fn(os.name)\n"
    "        return 1\n"
    "# refresh_token in a comment is NOT a ref\n"
    's = "refresh_token in a string is NOT a ref"\n'
)

TS_SRC = (
    'import { helper } from "./lib";\n'
    "const a = helper();\n"
    "class Widget extends Base {}\n"
    "// helper in a comment\n"
)

C_SRC = (
    '#include "util.h"\n'
    "int main(void) {\n"
    "    helper();\n"
    "    return 0;\n"
    "}\n"
    "/* helper in a comment */\n"
)


def test_python_extraction_known_edges():
    imports, refs = extract_graph("mod/a.py", PY_SRC, "python")
    targets = {i[0] for i in imports}
    assert "os" in targets and ".helpers" in targets
    ref_names = {(r[2], r[3]) for r in refs}
    assert ("refresh_token", "call") in ref_names
    assert ("other_fn", "call") in ref_names
    assert ("Base", "base_class") in ref_names
    # False positives: names ONLY in comments/strings never captured. The row
    # is (line, from_symbol, to_name, relationship, receiver) — the LINE is
    # index 0, so guard the line against the comment (line 10) and the string
    # (line 11). (Indexing r[1] here measured from_symbol, a name, against
    # line numbers: vacuous — issue #15.)
    assert all(r[0] != 10 and r[0] != 11 for r in refs
               if r[2] == "refresh_token")
    # The same name IS captured from real code (line 7) — the positive
    # direction, so the guard above is not satisfied by capturing nothing.
    assert any(r[0] == 7 and r[2] == "refresh_token" and r[3] == "call"
               for r in refs)


def test_ts_extraction():
    imports, refs = extract_graph("ui/app.ts", TS_SRC, "typescript")
    assert imports and imports[0][0] == "./lib"
    assert any(r[2] == "helper" and r[3] == "call" for r in refs)
    assert any(r[2] == "Base" for r in refs)


def test_c_extraction():
    imports, refs = extract_graph("src/main.c", C_SRC, "c")
    assert imports[0][0] == "util.h"
    assert any(r[2] == "helper" and r[3] == "call" for r in refs)
    # The comment-only mention is not a ref (line 6 absent).
    assert all(r[0] != 6 for r in refs)


# ---------------------------------------------------------------------------
# the AST import/include target reaches the reference store
# ---------------------------------------------------------------------------

def test_python_import_produces_an_includes_reference_row():
    """`includes` coverage for python, not only C/C++."""
    src = "import os\nimport pkg.helpers\nfrom .helpers import refresh_token\n"
    imports, refs = extract_graph("mod/a.py", src, "python")
    assert {i[0] for i in imports} >= {"os", "pkg.helpers", ".helpers"}
    includes = {r[2] for r in refs if r[3] == "includes"}
    assert includes == {"os", "pkg.helpers", ".helpers"}
    # The imported NAME (`refresh_token`) is not an include target.
    assert "refresh_token" not in includes


@pytest.mark.parametrize("lang,path,src,target", [
    ("typescript", "ui/a.ts", 'import { helper } from "./lib";\n', "./lib"),
    ("tsx", "ui/a.tsx", 'import React from "react";\n', "react"),
    ("javascript", "ui/a.js", 'import { helper } from "./lib";\n', "./lib"),
    ("go", "svc/a.go", 'package main\n\nimport "fmt"\n', "fmt"),
    ("csharp", "Sim/A.cs", "using System;\n", "System"),
    ("c", "src/a.c", '#include "util.h"\n', "util.h"),
    ("cpp", "src/a.cpp", '#include "shape.hpp"\n', "shape.hpp"),
])
def test_ast_import_target_is_an_includes_reference(lang, path, src, target):
    """AST import/include coverage across every AST language, each against its
    own grammar."""
    _imports, refs = extract_graph(path, src, lang)
    includes = [r for r in refs if r[3] == "includes"]
    assert includes, f"{lang}: no includes row for {target!r}"
    assert includes[0][2] == target, includes


@pytest.mark.parametrize("lang,path", [
    ("typescript", "ui/a.ts"),
    ("tsx", "ui/a.tsx"),
    ("javascript", "ui/a.js"),
])
def test_require_call_is_an_import_and_an_includes_row(lang, path):
    """`require("./x")` yields BOTH the import row (kind `require`) and the
    `includes` reference row — the require coverage the scoping keeps."""
    imports, refs = extract_graph(path, 'const x = require("./x");\n', lang)
    assert [(i[0], i[2]) for i in imports] == [("./x", "require")], imports
    includes = [r for r in refs if r[3] == "includes"]
    assert [r[2] for r in includes] == ["./x"], refs
    # The require call itself is still a `call` row.
    assert any(r[2] == "require" and r[3] == "call" for r in refs)


@pytest.mark.parametrize("lang,path", [
    ("typescript", "ui/a.ts"),
    ("tsx", "ui/a.tsx"),
    ("javascript", "ui/a.js"),
])
def test_string_argument_call_is_not_an_include_or_import(lang, path):
    """`alert("hi")` yields neither an imports row nor an includes row — only
    a `call` row for `alert` (issue #12; both directions pinned)."""
    imports, refs = extract_graph(path, 'alert("hi");\n', lang)
    assert imports == []
    assert [r for r in refs if r[3] == "includes"] == []
    assert any(r[2] == "alert" and r[3] == "call" for r in refs)


def test_angled_include_stays_unresolved_and_unreferenced():
    """The documented C/C++ limitation, pinned: a system header is neither an
    `imports` row nor an `includes` reference row (the grammar node is a
    `system_lib_string`, which the query does not capture)."""
    imports, refs = extract_graph("src/a.cpp", '#include <vector>\n', "cpp")
    assert imports == []
    assert [r for r in refs if r[3] == "includes"] == []


def test_ast_language_is_actually_ast_backed(lang_of=("tsx", "javascript")):
    """The `tsx`/`javascript` entries in LANGUAGES_WITH_AST have a query file
    (a language listed there without one silently produces NO rows at all)."""
    import os as _os
    from code_indexer import graph_extract
    for lang in lang_of:
        assert lang in graph_extract.LANGUAGES_WITH_AST
        qp = _os.path.join(_os.path.dirname(graph_extract.__file__),
                           "queries", lang, "references.scm")
        assert _os.path.isfile(qp), f"missing query file for {lang}"
        assert graph_extract._load_query(lang) is not None, (
            f"{lang}: the query file exists but does not compile")


def test_heuristic_fallback_flags_confidence():
    rust = "use std::io;\nfn main() { helper(); }\n"
    imports, refs = extract_graph_textual("a.rs", rust)
    assert imports[0][0] == "std::io;"
    assert any(r[2] == "helper" for r in refs)


def test_textual_scan_emits_a_call_row_for_a_tight_name_paren():
    """A language with no AST query still answers a `calls` filter."""
    src = (
        "use std::io;\n"
        "fn main() { helper(); }\n"
        "let value = compute (x);\n"
        "let other = plain_name;\n"
        "# comment_lead();\n"
    )
    _imports, refs = extract_graph_textual("a.rs", src)
    pairs = {(r[2], r[3]) for r in refs}
    assert ("helper", "call") in pairs
    # A name with whitespace before the paren is NOT a call (the prose shape).
    assert ("compute", "identifier") in pairs
    assert ("plain_name", "identifier") in pairs
    # One occurrence yields one row — a call site is not also an identifier.
    assert sum(1 for r in refs if r[2] == "helper") == 1
    # A comment-leading line is skipped entirely, so no call row for it.
    assert not any(r[2] == "comment_lead" for r in refs)


def test_import_resolution_python_relative_and_src():
    import tempfile
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "pkg"))
    os.makedirs(os.path.join(root, "src", "svc"))
    open(os.path.join(root, "pkg", "helpers.py"), "w").close()
    open(os.path.join(root, "pkg", "__init__.py"), "w").close()
    open(os.path.join(root, "src", "svc", "jobs.py"), "w").close()
    res = resolve_imports(root, "app.py", "python",
                          [".pkg.helpers", "svc.jobs", "external.lib"])
    assert res[".pkg.helpers"] == "pkg/helpers.py"
    assert res["svc.jobs"] == "src/svc/jobs.py"
    assert res["external.lib"] is None


def test_import_resolution_js_and_c():
    import tempfile
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "ui"))
    os.makedirs(os.path.join(root, "inc"))
    open(os.path.join(root, "ui", "lib.ts"), "w").close()
    open(os.path.join(root, "inc", "util.h"), "w").close()
    res_js = resolve_imports(root, "ui/app.ts", "typescript", ["./lib"])
    assert res_js["./lib"] == "ui/lib.ts"
    res_c = resolve_imports(root, "ui/main.c", "c",
                            ['"util.h"', "<stdio.h>"])
    assert res_c['"util.h"'] is None  # quoted include resolves vs file dir
    # The C resolver joins with the file's dir: 'ui/util.h' does not exist.
    res_c2 = resolve_imports(root, "main.c", "c", ['"inc/util.h"'])
    assert res_c2['"inc/util.h"'] == "inc/util.h"
    assert res_c["<stdio.h>"] is None


# ---------------------------------------------------------------------------
# end-to-end through the indexer + commands
# ---------------------------------------------------------------------------

def _rig(tmp_path, monkeypatch):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    (project / "pkg").mkdir(parents=True)
    (project / "pkg" / "__init__.py").write_text("")
    (project / "pkg" / "helpers.py").write_text(
        "def refresh_token():\n    return 1\n")
    (project / "app.py").write_text(
        "from pkg.helpers import refresh_token\n"
        "def run():\n"
        "    refresh_token()\n"
        "    return 0\n"
        "# refresh_token comment only here\n")
    # ONE enclosing function calling the same target on two lines: two call
    # SITES, one `from_symbol` — the shape that used to collapse into the
    # first (issue #21).
    (project / "repeat.py").write_text(
        "from pkg.helpers import refresh_token\n"
        "def twice():\n"
        "    refresh_token()\n"
        "    refresh_token()\n"
        "    return 0\n")
    # A caller of `run`, so a depth cap has something real to stop.
    (project / "chain.py").write_text(
        "from app import run\n"
        "def outer():\n"
        "    run()\n"
        "    return 0\n")
    # Four DISTINCT callee targets in one symbol, so a node cap below four
    # refuses more than one edge — a fixture that can tell "counts every
    # refusal" from "counts the first".
    (project / "pkg" / "shapes.py").write_text(
        "def square():\n"
        "    return 1\n"
        "def circle():\n"
        "    return 2\n"
        "def triangle():\n"
        "    return 3\n"
        "def hexagon():\n"
        "    return 4\n"
        "def compose():\n"
        "    square()\n"
        "    circle()\n"
        "    triangle()\n"
        "    hexagon()\n"
        "    return 0\n")
    # THREE resolved out-edges from one file, for the same reason on the
    # `deps` side. The targets are deliberately outside the `refresh_token`
    # call graph, so this fixture does not deepen that walk.
    (project / "pkg" / "leaves.py").write_text(
        "def stub_one():\n"
        "    return 1\n")
    (project / "pkg" / "aggregate.py").write_text(
        "import pkg\n"
        "import pkg.leaves\n"
        "import pkg.shapes\n")
    (project / "ui.ts").write_text(
        'import { helper } from "./lib";\nexport function go() { helper(); }\n')
    (project / "lib.ts").write_text("export function helper() { return 1; }\n")
    (project / "main.c").write_text(
        '#include "util.h"\nint main() { helper(); return 0; }\n')
    (project / "util.h").write_text("void helper(void);\n")
    # A textual-fallback language (no AST query): a tight `name(` yields a
    # `call` row here.
    (project / "worker.rs").write_text(
        "use std::io;\nfn main() { helper(); }\n")
    core = Core(cfg)

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

        def count_points(self, name):
            return 0

        def search(self, *a, **kw):
            return []

    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    from code_indexer.registry import Registry
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="graphproj")
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    return core, reg, entry, project


def test_indexer_populates_graph_tables(tmp_path, monkeypatch):
    core, reg, entry, project = _rig(tmp_path, monkeypatch)
    m = core.manifest_for(entry.slug)
    try:
        imports = m.import_rows()
        assert {(i["file"], i["raw_target"], i["resolved_file"])
                for i in imports} >= {
            ("app.py", "pkg.helpers", "pkg/helpers.py"),
            ("main.c", "util.h", "util.h"),
        }, imports
        callers = m.callers_of("refresh_token")
        assert any(c["file"] == "app.py" and c["from_symbol"] == "run"
                   and c["relationship"] == "call" for c in callers)
        assert all(c["confidence"] == "ast" for c in callers)
        # The comment-only mention produced no ref row on that line.
        assert all(c["line"] != 5 for c in callers)
    finally:
        m.close()


def test_ast_import_keeps_its_imports_row_and_gains_an_includes_ref(
        tmp_path, monkeypatch):
    """The `imports` table is unchanged; the reference
    store gains the matching `includes` row."""
    core, reg, entry, project = _rig(tmp_path, monkeypatch)
    m = core.manifest_for(entry.slug)
    try:
        imports = {(i["file"], i["raw_target"], i["resolved_file"])
                   for i in m.import_rows()}
        assert ("app.py", "pkg.helpers", "pkg/helpers.py") in imports, imports
        includes = m.refs_to("pkg.helpers", relationships=("includes",))
        assert includes, "the python import produced no includes reference"
        assert includes[0]["file"] == "app.py"
        assert includes[0]["confidence"] == "ast"
    finally:
        m.close()


def test_textual_language_stores_a_heuristic_call_row(tmp_path, monkeypatch):
    """The `call` row survives to the store, heuristic."""
    core, reg, entry, project = _rig(tmp_path, monkeypatch)
    m = core.manifest_for(entry.slug)
    try:
        rows = m.callers_of("helper")
        rust = [r for r in rows if r["file"] == "worker.rs"]
        assert rust, rows
        assert rust[0]["relationship"] == "call"
        assert rust[0]["confidence"] == "heuristic"
    finally:
        m.close()


def test_find_callers_command(tmp_path, monkeypatch):
    core, reg, entry, project = _rig(tmp_path, monkeypatch)
    result = runner.invoke(app, ["find-callers", "refresh_token",
                                 "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert "app.py" in result.output and "run" in result.output
    assert "[call/ast]" in result.output
    result2 = runner.invoke(app, ["find-callers", "refresh_token",
                                  "--project", str(project), "--json"])
    assert result2.exit_code == 0
    assert '"relationship": "call"' in result2.output


def test_find_callees_command(tmp_path, monkeypatch):
    core, reg, entry, project = _rig(tmp_path, monkeypatch)
    result = runner.invoke(app, ["find-callees", "run",
                                 "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert "refresh_token" in result.output


def test_deps_command_resolved_edges(tmp_path, monkeypatch):
    core, reg, entry, project = _rig(tmp_path, monkeypatch)
    result = runner.invoke(app, ["deps", "app.py", "--project", str(project),
                                 "--direction", "out"])
    assert result.exit_code == 0, result.output
    assert "pkg/helpers.py" in result.output
    result_in = runner.invoke(app, ["deps", "pkg/helpers.py", "--project",
                                    str(project), "--direction", "in"])
    assert "app.py" in result_in.output


def test_graph_caps_respected(tmp_path, monkeypatch):
    core, reg, entry, project = _rig(tmp_path, monkeypatch)
    # max_nodes=1 must yield at most 1 node in the rendered tree.
    result = runner.invoke(app, ["find-callers", "refresh_token",
                                 "--project", str(project),
                                 "--max-nodes", "1"])
    assert result.exit_code == 0
    body = [ln for ln in result.output.splitlines() if ln.startswith(("pkg", "app"))]
    assert len(body) <= 2  # definition root + cap
    # ...and the cap is reported, never silent (issue #21).
    assert "node cap 1" in result.output


def _walk_json(payload):
    """Every node of a graph JSON payload, depth-first."""
    out = []

    def walk(node):
        out.append(node)
        for child in node["children"]:
            walk(child)

    for root in payload["roots"]:
        walk(root)
    return out


def test_find_callers_reports_every_call_site_in_one_enclosing_symbol(
        tmp_path, monkeypatch):
    """One function calling the same target twice is two hits, two lines —
    the enclosing symbol is NOT the node identity (issue #21)."""
    core, reg, entry, project = _rig(tmp_path, monkeypatch)
    result = runner.invoke(app, ["find-callers", "refresh_token",
                                 "--project", str(project), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    sites = {(n["line"], n["symbol"]) for n in _walk_json(payload)
             if n["file"] == "repeat.py" and n["symbol"] == "twice"}
    assert sites == {(3, "twice"), (4, "twice")}, sites
    text = runner.invoke(app, ["find-callers", "refresh_token",
                               "--project", str(project)])
    assert text.exit_code == 0, text.output
    assert "repeat.py:3" in text.output and "repeat.py:4" in text.output


def test_find_callers_reports_a_refused_edge_instead_of_dropping_it(
        tmp_path, monkeypatch):
    """A node cap below the direct-hit count is reported, not silent."""
    core, reg, entry, project = _rig(tmp_path, monkeypatch)
    result = runner.invoke(app, ["find-callers", "refresh_token",
                                 "--project", str(project),
                                 "--max-nodes", "2", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["truncated"] is True
    assert payload["omitted"] > 0, payload
    assert "roots" in payload  # the existing key keeps its shape
    text = runner.invoke(app, ["find-callers", "refresh_token",
                               "--project", str(project), "--max-nodes", "2"])
    assert text.exit_code == 0, text.output
    # The note rides WITH the answer: stdout. `Result.output` would mix the
    # two streams and could not tell this from an inverted echo() call.
    assert "note:" in text.stdout and "node cap 2" in text.stdout
    assert "note:" not in text.stderr
    # The note must not present the refused count as the whole population.
    assert "lower bound" in text.stdout


def test_find_callers_reports_a_depth_cap(tmp_path, monkeypatch):
    """A depth-bounded answer is incomplete too, and says so."""
    core, reg, entry, project = _rig(tmp_path, monkeypatch)
    result = runner.invoke(app, ["find-callers", "refresh_token",
                                 "--project", str(project),
                                 "--depth", "1", "--max-nodes", "500"])
    assert result.exit_code == 0, result.output
    assert "deeper references exist beyond depth 1" in result.output
    payload = json.loads(runner.invoke(
        app, ["find-callers", "refresh_token", "--project", str(project),
              "--depth", "1", "--max-nodes", "500", "--json"]).stdout)
    assert payload["truncated"] is True


def test_find_callers_generous_cap_is_not_truncated(tmp_path, monkeypatch):
    """The pin in the other direction: a complete answer says complete."""
    core, reg, entry, project = _rig(tmp_path, monkeypatch)
    result = runner.invoke(app, ["find-callers", "refresh_token",
                                 "--project", str(project),
                                 "--max-nodes", "500", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["truncated"] is False, payload.get("notes")
    assert payload["omitted"] == 0


def test_unknown_symbol_is_not_reported_as_truncated(tmp_path, monkeypatch):
    """Negative control: nothing was refused, so nothing is truncated."""
    core, reg, entry, project = _rig(tmp_path, monkeypatch)
    result = runner.invoke(app, ["find-callers", "never_mentioned_anywhere",
                                 "--project", str(project), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["truncated"] is False
    assert payload["omitted"] == 0
    assert any("nothing in this index mentions it" in n
               for n in payload.get("notes", []))


def test_find_callees_reports_targets_not_sites_and_its_truncation(
        tmp_path, monkeypatch):
    """find-callees keeps TARGET-set identity (one target called twice is one
    callee) and still reports a refused edge."""
    core, reg, entry, project = _rig(tmp_path, monkeypatch)
    twice = json.loads(runner.invoke(
        app, ["find-callees", "twice", "--project", str(project),
              "--max-nodes", "500", "--json"]).stdout)
    targets = [n for n in _walk_json(twice)
               if n["file"] == "pkg/helpers.py" and n["symbol"] == "refresh_token"]
    assert len(targets) == 1, targets
    # Four distinct callees under a cap of 2: one fits beside the definition
    # root and the other three are refused, so `omitted` can only be 3 if
    # every refusal is counted, not the first.
    capped = runner.invoke(app, ["find-callees", "compose", "--project",
                                 str(project), "--max-nodes", "2", "--json"])
    assert capped.exit_code == 0, capped.output
    payload = json.loads(capped.stdout)
    assert payload["truncated"] is True
    assert payload["omitted"] == 3, payload
    text = runner.invoke(app, ["find-callees", "compose", "--project",
                               str(project), "--max-nodes", "2"])
    assert "note:" in text.stdout and "node cap 2" in text.stdout
    assert "note:" not in text.stderr


def test_deps_reports_its_truncation_and_keeps_its_levels(tmp_path, monkeypatch):
    core, reg, entry, project = _rig(tmp_path, monkeypatch)
    payload = json.loads(runner.invoke(
        app, ["deps", "app.py", "--project", str(project), "--direction",
              "out", "--format", "json"]).stdout)
    assert payload["out"], payload  # per-level dict unchanged
    assert payload["truncated"] is False and payload["omitted"] == 0
    # Three resolved out-edges under a cap of 2: the start file leaves room
    # for one, so exactly two are refused — a count that requires counting
    # every refusal.
    capped = runner.invoke(app, ["deps", "pkg/aggregate.py", "--project",
                                 str(project), "--direction", "out",
                                 "--max-nodes", "2", "--format", "json"])
    assert capped.exit_code == 0, capped.output
    payload = json.loads(capped.stdout)
    assert payload["truncated"] is True
    assert payload["omitted"] == 2, payload
    text = runner.invoke(app, ["deps", "pkg/aggregate.py", "--project",
                               str(project), "--direction", "out",
                               "--max-nodes", "2"])
    assert "note:" in text.stdout and "node cap 2" in text.stdout
    assert "note:" not in text.stderr


def _wide_rig(tmp_path, monkeypatch, callers: int = 250):
    """A project with `callers` distinct call sites for one name — more than
    the 200-row store cap that used to sit under every graph walk (issue #22).

    `models.py` defines `pairA`; five caller files hold 50 functions each that
    call it and import nothing, so every edge is a call site and no import
    fan-in edge inflates the count. `ghost_fn` is called just as often and
    defined nowhere, which exercises the no-definition seed path.
    """
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "wide"
    project.mkdir()
    (project / "models.py").write_text(
        "def pairA():\n    return 1\n")
    per_file = callers // 5
    for f in range(5):
        body = []
        for i in range(per_file):
            body.append(f"def caller_{f}_{i:03d}():")
            body.append("    pairA()")
            body.append("    ghost_fn()")
        (project / f"callers_{f}.py").write_text("\n".join(body) + "\n")
    core = Core(cfg)

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

        def count_points(self, name):
            return 0

        def search(self, *a, **kw):
            return []

    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    from code_indexer.registry import Registry
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="wideproj")
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    return core, reg, entry, project


def test_every_call_site_above_the_old_store_cap_is_in_the_answer(
        tmp_path, monkeypatch):
    """250 call sites: the store used to hand the walk 200 and say nothing."""
    core, reg, entry, project = _wide_rig(tmp_path, monkeypatch)
    result = runner.invoke(app, ["find-callers", "pairA", "--project",
                                 str(project), "--max-nodes", "400",
                                 "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    sites = [n for n in _walk_json(payload) if n["relationship"] == "call"]
    assert len(sites) == 250, len(sites)
    assert payload["truncated"] is False, payload.get("notes")
    assert payload["omitted"] == 0


def test_seeded_reference_list_applies_and_announces_its_node_cap(
        tmp_path, monkeypatch):
    """The no-definition seed path reads the whole store and caps HERE.

    Before the store stopped capping, this path returned the store's 200 rows
    and claimed a complete answer; it must not replace that with an unbounded
    answer either — the walk's own cap decides, and reports.
    """
    core, reg, entry, project = _wide_rig(tmp_path, monkeypatch)
    capped = runner.invoke(app, ["find-callers", "ghost_fn", "--project",
                                 str(project), "--max-nodes", "40", "--json"])
    assert capped.exit_code == 0, capped.output
    payload = json.loads(capped.stdout)
    assert len(payload["roots"]) == 40
    assert payload["truncated"] is True
    assert payload["omitted"] == 210, payload["omitted"]
    assert any("node cap 40" in n for n in payload["notes"])
    # Generous cap: the same list, complete, and nothing claims truncation.
    full = runner.invoke(app, ["find-callers", "ghost_fn", "--project",
                               str(project), "--max-nodes", "400", "--json"])
    assert full.exit_code == 0, full.output
    payload = json.loads(full.stdout)
    assert len(payload["roots"]) == 250
    assert payload["truncated"] is False
    assert payload["omitted"] == 0
    assert not any("node cap" in n for n in payload["notes"])


def _many_definitions_rig(tmp_path, monkeypatch, definitions: int = 40):
    """A project defining ONE name more times than the old 25-row browse cap:
    `definitions` generated classes, each with a `step` method and nothing
    that references it. The definition roots ARE the answer here, so a walk
    handed 25 of them cannot be distinguished from a complete one by its
    node count alone — which is why the note names the root set (issue #22).
    """
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "manydefs"
    project.mkdir()
    for i in range(definitions):
        (project / f"models_{i:02d}.py").write_text(
            f"class Model{i:02d}:\n"
            f"    def step(self):\n"
            f"        return {i}\n")
    core = Core(cfg)

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

        def count_points(self, name):
            return 0

        def search(self, *a, **kw):
            return []

    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    from code_indexer.registry import Registry
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="manydefs")
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    return core, reg, entry, project


def test_every_definition_root_reaches_the_walk(tmp_path, monkeypatch):
    """40 definitions of one name: the walk is handed all 40, not 25.

    The store read behind the roots used to cap at 25, so 15 subtrees were
    never walked and the answer still said `truncated: false` — issue #22's
    filed symptom, live on this tool's own index.
    """
    core, reg, entry, project = _many_definitions_rig(tmp_path, monkeypatch)
    result = runner.invoke(app, ["find-callers", "step", "--project",
                                 str(project), "--max-nodes", "400",
                                 "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert len(payload["roots"]) == 40, len(payload["roots"])
    assert payload["truncated"] is False, payload.get("notes")
    assert payload["omitted"] == 0

    # The same read is what `find-callees` roots at.
    callees = json.loads(runner.invoke(
        app, ["find-callees", "step", "--project", str(project),
              "--max-nodes", "400", "--json"]).stdout)
    assert len(callees["roots"]) == 40, len(callees["roots"])


def test_a_capped_root_list_says_how_many_roots_exist(tmp_path, monkeypatch):
    """A root set larger than the node cap is cut, and the cut is named.

    The refusals are NOT `omitted`: that field stays "edges examined and
    refused at the node cap". So a root-capped answer is another instance of
    the documented `truncated: true, omitted: 0` combination, and `truncated`
    carries the incompleteness.
    """
    core, reg, entry, project = _many_definitions_rig(tmp_path, monkeypatch)
    result = runner.invoke(app, ["find-callers", "step", "--project",
                                 str(project), "--max-nodes", "10", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert len(payload["roots"]) == 10, len(payload["roots"])
    assert payload["truncated"] is True
    assert payload["omitted"] == 0, payload
    note = "\n".join(payload["notes"])
    assert "40 definition roots exist" in note, note
    assert "node cap 10" in note and "30 roots not shown" in note, note
    assert "raise --max-nodes" in note, note
    # ...and the text answer carries the same line on stdout, with the tree.
    text = runner.invoke(app, ["find-callers", "step", "--project",
                               str(project), "--max-nodes", "10"])
    assert "30 roots not shown" in text.stdout


def test_find_symbol_exact_name_is_uncapped_and_honours_limit(
        tmp_path, monkeypatch):
    """No surface could list all 40 definitions even by asking: exact-name
    mode never forwarded --limit. Browse mode keeps its default cap."""
    core, reg, entry, project = _many_definitions_rig(tmp_path, monkeypatch)
    rows = json.loads(runner.invoke(
        app, ["find-symbol", "step", "--project", str(project),
              "--json"]).stdout)
    assert len(rows) == 40, len(rows)
    asked = json.loads(runner.invoke(
        app, ["find-symbol", "step", "--project", str(project),
              "--limit", "100", "--json"]).stdout)
    assert len(asked) == 40, len(asked)
    bounded = json.loads(runner.invoke(
        app, ["find-symbol", "step", "--project", str(project),
              "--limit", "3", "--json"]).stdout)
    assert len(bounded) == 3, len(bounded)
    # find-definition is the same exact-name read, same answer.
    text = runner.invoke(app, ["find-definition", "step", "--project",
                               str(project)])
    assert text.exit_code == 0, text.output
    assert len([ln for ln in text.stdout.splitlines() if ln.startswith("- ")]) \
        == 40


def test_mcp_graph_tools_exist():
    import code_indexer.server as server
    assert hasattr(server, "find_callers") and hasattr(server, "find_callees")
    assert hasattr(server, "deps")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))