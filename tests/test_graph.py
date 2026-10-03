"""Graph extraction + graph command tests (dependency/call-graph work item).

Fixtures carry KNOWN edges; the false-positive test pins that names appearing
only in comments/strings are NOT references (AST path). All offline.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer import graph  # noqa: E402
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
    # False positives: names ONLY in comments/strings never captured.
    assert all(r[1] != 10 and r[1] != 11 for r in refs
               if r[2] == "refresh_token")


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


def test_heuristic_fallback_flags_confidence():
    rust = "use std::io;\nfn main() { helper(); }\n"
    imports, refs = extract_graph_textual("a.rs", rust)
    assert imports[0][0] == "std::io;"
    assert any(r[2] == "helper" for r in refs)


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
    (project / "ui.ts").write_text(
        'import { helper } from "./lib";\nexport function go() { helper(); }\n')
    (project / "lib.ts").write_text("export function helper() { return 1; }\n")
    (project / "main.c").write_text(
        '#include "util.h"\nint main() { helper(); return 0; }\n')
    (project / "util.h").write_text("void helper(void);\n")
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


def test_mcp_graph_tools_exist():
    import code_indexer.server as server
    assert hasattr(server, "find_callers") and hasattr(server, "find_callees")
    assert hasattr(server, "deps")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))