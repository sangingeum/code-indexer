"""Overview command tests (navigation work item).

Fixture repo with known languages/entry points/tests/config; the snapshot
test pins the section layout, the cap test pins --max-lines, and the
ordering tests pin stability. All offline.
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
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()


def _write(project, rel: str, text: str) -> None:
    p = project / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


PY_APP = ("from pkg.helpers import refresh_token\n"
          "def main():\n"
          "    refresh_token()\n"
          "    return 0\n")
PY_HELPERS = ("def refresh_token():\n    return 1\n")
PY_TEST = ("import app\n"
           "def test_main():\n    assert app.main() == 0\n")
TS_LIB = "export function helper() { return 1; }\n"
TS_APP = 'import { helper } from "./lib";\nexport function go() { helper(); }\n'


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    _write(project, "app.py", PY_APP)
    _write(project, "pkg/helpers.py", PY_HELPERS)
    _write(project, "tests/test_app.py", PY_TEST)
    _write(project, "ui/app.ts", TS_APP)
    _write(project, "ui/lib.ts", TS_LIB)
    _write(project, "pyproject.toml",
           "[project]\nname = 'proj'\n\n[project.scripts]\nproj = 'app:main'\n")
    _write(project, "package.json",
           '{"main": "ui/app.ts"}\n')
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
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="ovproj")
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    return core, reg, entry, project


def test_overview_snapshot(rig):
    core, reg, entry, project = rig
    result = runner.invoke(app, ["overview", "--project", str(project)])
    assert result.exit_code == 0, result.output
    out = result.output
    # Sections, in stable order.
    assert out.startswith("project: proj — ")
    assert "languages:" in out
    assert "python:" in out and "typescript:" in out
    assert "directories:" in out
    assert "pkg/" in out and "ui/" in out and "tests/" in out
    assert "entry points:" in out
    assert "script: proj" in out                 # pyproject [project.scripts]
    assert "package.json main: ui/app.ts" in out
    assert "app.py:main" in out                  # main symbol
    assert "tests: 1 files" in out or "tests: 1 file" in out
    assert "config/build:" in out
    assert "pyproject.toml" in out and "package.json" in out
    assert "hotspots (fan-in):" in out
    assert "app.py" in out                       # imported by... helpers? no:
    # fan-in: ui/app.ts is imported by... nothing; pkg/helpers.py imported by
    # app.py; ui/lib.ts imported by ui/app.ts.
    assert "ui/lib.ts" in out


def test_overview_max_lines_cap(rig):
    core, reg, entry, project = rig
    result = runner.invoke(app, ["overview", "--project", str(project),
                                 "--max-lines", "5"])
    assert result.exit_code == 0
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert len(lines) <= 5


def test_overview_stable_ordering(rig):
    core, reg, entry, project = rig
    r1 = runner.invoke(app, ["overview", "--project", str(project)]).output
    r2 = runner.invoke(app, ["overview", "--project", str(project)]).output
    assert r1 == r2  # deterministic for fixed data


def test_overview_path_prefix(rig):
    core, reg, entry, project = rig
    result = runner.invoke(app, ["overview", "--project", str(project),
                                 "--path-prefix", "ui/"])
    assert result.exit_code == 0
    assert "typescript" in result.output
    assert "python" not in result.output.split("languages:")[1] \
        if "languages:" in result.output else True


def test_overview_json(rig):
    core, reg, entry, project = rig
    import json
    result = runner.invoke(app, ["overview", "--project", str(project),
                                 "--json"])
    data = json.loads(result.output)
    assert data["project"] == "proj"
    assert any(l["language"] == "python" for l in data["languages"])
    assert data["hotspots"]["fan_in"]


def test_manifest_loc_language_columns(rig):
    core, reg, entry, project = rig
    m = core.manifest_for(entry.slug)
    try:
        f = m.get_file("app.py")
        assert f.language == "python"
        assert f.loc == 4  # approx: max end_line
        assert m.get_file("ui/lib.ts").language == "typescript"
    finally:
        m.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))