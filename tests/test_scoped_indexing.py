"""Scoped (subtree-first) indexing tests (CI-23).

Monorepo fixture: indexing only services/a/** leaves other dirs untouched
and reported as out of scope. All offline.
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
from code_indexer.scanner import scan_project  # noqa: E402

runner = CliRunner()


def test_scan_include_scopes_subtree(tmp_path):
    for name in ("services/a/one.py", "services/b/two.py",
                 "docs/readme.md", "app.py"):
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n")
    scanned = {f.path for f in scan_project(str(tmp_path),
                                            include=["services/a/**"])}
    assert "services/a/one.py" in scanned
    assert "services/b/two.py" not in scanned
    assert "docs/readme.md" not in scanned
    assert "app.py" not in scanned
    # Bare dir pattern includes everything under it.
    scanned2 = {f.path for f in scan_project(str(tmp_path),
                                             include=["services/b"])}
    assert "services/b/two.py" in scanned2
    assert "services/a/one.py" not in scanned2


def test_scan_priority_orders_first(tmp_path):
    for name in ("aaa.py", "zzz.py", "core/first.py"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x = 1\n")
    scanned = [f.path for f in scan_project(str(tmp_path),
                                            priority=["core/**"])]
    assert scanned.index("core/first.py") < scanned.index("aaa.py")


# ---------------------------------------------------------------------------

@pytest.fixture()
def rig(tmp_path, monkeypatch):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "monorepo"
    for rel in ("services/a/handler.py", "services/b/worker.py",
                "docs/guide.md", "web/ui.ts"):
        p = project / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("def scoped_symbol():\n    return 1\n"
                     if rel.endswith(".py") else "text\n")
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
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    return core, reg, cfg, project


def test_monorepo_scoped_indexing(rig):
    core, reg, cfg, project = rig
    # add-project --include 'services/a/**': other dirs untouched.
    result = runner.invoke(app, ["add-project", str(project),
                                 "--name", "monoproj",
                                 "--include", "services/a/**"])
    assert result.exit_code == 0, result.output
    assert "scoped indexing" in result.output
    m = core.manifest_for("monoproj")
    try:
        files = set(m.all_files())
        assert "services/a/handler.py" in files
        assert "services/b/worker.py" not in files  # out of scope
        assert "web/ui.ts" not in files
        assert "docs/guide.md" not in files
        assert m.get_meta("scope_include") == "services/a/**"
    finally:
        m.close()


def test_index_status_reports_out_of_scope(rig):
    core, reg, cfg, project = rig
    runner.invoke(app, ["add-project", str(project), "--name", "monoproj",
                        "--include", "services/a/**"])
    result = runner.invoke(app, ["index-status", str(project)])
    assert result.exit_code == 0, result.output
    assert "scope=services/a/**" in result.output
    assert "not indexed:" in result.output
    assert "web" in result.output and "docs" in result.output


def test_index_more_extends_scope(rig):
    core, reg, cfg, project = rig
    runner.invoke(app, ["add-project", str(project), "--name", "monoproj",
                        "--include", "services/a/**"])
    result = runner.invoke(app, ["index-more", str(project), "services/b"])
    assert result.exit_code == 0, result.output
    assert "services/b" in result.output
    m = core.manifest_for("monoproj")
    try:
        files = set(m.all_files())
        assert "services/b/worker.py" in files  # newly in scope
        assert "web/ui.ts" not in files
        assert "services/a/handler.py" in files
    finally:
        m.close()


def test_out_of_scope_note_on_queries(rig):
    core, reg, cfg, project = rig
    runner.invoke(app, ["add-project", str(project), "--name", "monoproj",
                        "--include", "services/a/**"])
    result = runner.invoke(app, ["semantic-search", "scoped_symbol",
                                 "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert "scoped to services/a/**" in result.output
    assert "index-more" in result.output


def test_unscoped_projects_have_no_note(rig):
    core, reg, cfg, project = rig
    runner.invoke(app, ["add-project", str(project), "--name", "fullproj"])
    result = runner.invoke(app, ["semantic-search", "scoped_symbol",
                                 "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert "index-more extends" not in result.output


def test_cli_scoped_search_json_still_works(rig):
    core, reg, cfg, project = rig
    runner.invoke(app, ["add-project", str(project), "--name", "monoproj",
                        "--include", "services/a/**"])
    result = runner.invoke(app, ["semantic-search", "scoped_symbol",
                                 "--project", str(project), "--json"])
    assert result.exit_code == 0
    assert '"schema": 1' in result.output


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))