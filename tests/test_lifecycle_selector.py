"""Lifecycle command selector surface tests (issue #29).

``lookup-project``, ``reindex-project``, ``remove-project`` and ``index-more``
accept the same dual selector as ``index-status``: a positional path or a
``--project`` / ``--name`` flag, both resolved through the one registry lookup
that understands a path, a slug, or a custom name. Mutating commands refuse to
guess a project from the working directory.
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

runner = CliRunner()


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
    def __init__(self):
        self.dropped: list[str] = []

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

    def drop_collection(self, name):
        self.dropped.append(name)


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "monorepo"
    for rel in ("services/a/handler.py", "services/b/worker.py",
                "docs/guide.md"):
        p = project / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("def scoped_symbol():\n    return 1\n")
    core = Core(cfg)
    store = StubStore()
    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = store  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    return core, cfg, project, store


def _register(project, name="monoproj", include=None):
    args = ["add-project", str(project), "--name", name]
    for glob in include or []:
        args += ["--include", glob]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result


def _second_project(tmp_path):
    other = tmp_path / "second"
    other.mkdir()
    (other / "mod.py").write_text("def other_symbol():\n    return 2\n")
    _register(other, "otherproj")
    return other


# --- lookup-project --------------------------------------------------------

def test_lookup_project_accepts_project_flag(rig):
    core, cfg, project, _store = rig
    _register(project)
    result = runner.invoke(app, ["lookup-project", "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert "registered" in result.output
    assert str(project) in result.output


def test_lookup_project_positional_form_still_works(rig):
    core, cfg, project, _store = rig
    _register(project)
    result = runner.invoke(app, ["lookup-project", str(project)])
    assert result.exit_code == 0, result.output
    assert "registered" in result.output
    assert str(project) in result.output


def test_lookup_project_resolves_slug_and_name_via_flag(rig):
    core, cfg, project, _store = rig
    _register(project)
    entry = core.registry.get_by_name("monoproj")
    for selector in (entry.slug, "monoproj"):
        result = runner.invoke(app, ["lookup-project", "--project", selector])
        assert result.exit_code == 0, result.output
        assert "registered" in result.output
        assert entry.path in result.output


def test_lookup_project_flag_wins_when_both_given(rig, tmp_path):
    core, cfg, project, _store = rig
    _register(project)
    first = core.registry.get_by_name("monoproj")
    _second_project(tmp_path)
    second = core.registry.get_by_name("otherproj")
    result = runner.invoke(app, ["lookup-project", str(first.path),
                                 "--project", "otherproj"])
    assert result.exit_code == 0, result.output
    assert second.path in result.output
    assert first.path not in result.output


def test_lookup_project_unregistered_prints_and_exits_zero(rig):
    core, cfg, project, _store = rig
    _register(project)
    flag = runner.invoke(app, ["lookup-project", "--project", "nope"])
    assert flag.exit_code == 0, flag.output
    assert "not registered: " in flag.output
    positional = runner.invoke(app, ["lookup-project", str(project / "gone")])
    assert positional.exit_code == 0, positional.output
    assert "not registered: " in positional.output


def test_lookup_project_empty_call_resolves_by_cwd(rig, monkeypatch):
    core, cfg, project, _store = rig
    _register(project)
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["lookup-project"])
    assert result.exit_code == 0, result.output
    assert "registered" in result.output
    assert str(project) in result.output


# --- reindex-project -------------------------------------------------------

def _stub_reindex(core, monkeypatch):
    monkeypatch.setattr(
        core, "reindex_with_swap",
        lambda slug, path: {"state": "indexed", "result": {"indexed": 1}})


def test_reindex_project_accepts_project_flag(rig, monkeypatch):
    core, cfg, project, _store = rig
    _register(project)
    _stub_reindex(core, monkeypatch)
    result = runner.invoke(app, ["reindex-project", "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert "monoproj:" in result.output


def test_reindex_project_positional_form_still_works(rig, monkeypatch):
    core, cfg, project, _store = rig
    _register(project)
    _stub_reindex(core, monkeypatch)
    result = runner.invoke(app, ["reindex-project", str(project)])
    assert result.exit_code == 0, result.output
    assert "monoproj:" in result.output


def test_reindex_project_resolves_slug_and_name_via_flag(rig, monkeypatch):
    core, cfg, project, _store = rig
    _register(project)
    _stub_reindex(core, monkeypatch)
    entry = core.registry.get_by_name("monoproj")
    for selector in (entry.slug, "monoproj"):
        result = runner.invoke(app, ["reindex-project", "--project", selector])
        assert result.exit_code == 0, result.output
        assert "monoproj:" in result.output


def test_reindex_project_flag_wins_when_both_given(rig, tmp_path, monkeypatch):
    core, cfg, project, _store = rig
    _register(project)
    _second_project(tmp_path)
    _stub_reindex(core, monkeypatch)
    result = runner.invoke(app, ["reindex-project", str(project),
                                 "--project", "otherproj"])
    assert result.exit_code == 0, result.output
    assert "otherproj:" in result.output
    assert "monoproj:" not in result.output


def test_reindex_project_unregistered_dies(rig):
    core, cfg, project, _store = rig
    _register(project)
    result = runner.invoke(app, ["reindex-project", "--project", "nope"])
    assert result.exit_code == 1, result.output
    assert "NotFoundError:" in result.output
    assert "not registered" in result.output


def test_reindex_project_refuses_to_guess_from_cwd(rig, monkeypatch):
    core, cfg, project, _store = rig
    _register(project)
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["reindex-project"])
    assert result.exit_code == 2, result.output
    assert "ArgumentError:" in result.output
    assert "needs an explicit project" in result.output
    assert "registered projects:" in result.output
    assert str(project) in result.output


# --- remove-project --------------------------------------------------------

def test_remove_project_accepts_project_flag_and_removes(rig):
    core, cfg, project, store = rig
    _register(project)
    entry = core.registry.get_by_name("monoproj")
    manifest_dir = os.path.join(cfg.index_root, entry.slug)
    assert os.path.isdir(manifest_dir)
    stray = os.path.join(cfg.index_root, f"{entry.slug}.stray")
    with open(stray, "w", encoding="utf-8") as fh:
        fh.write("x")
    result = runner.invoke(app, ["remove-project", "--project", "monoproj"])
    assert result.exit_code == 0, result.output
    assert "removed " in result.output
    assert "collection idx_monoproj dropped" in result.output
    assert store.dropped == ["idx_monoproj"]
    assert core.registry.get_by_name("monoproj") is None
    assert not os.path.exists(manifest_dir)
    assert not os.path.exists(stray)


def test_remove_project_positional_form_still_works(rig):
    core, cfg, project, store = rig
    _register(project)
    result = runner.invoke(app, ["remove-project", str(project)])
    assert result.exit_code == 0, result.output
    assert "removed " in result.output
    assert store.dropped == ["idx_monoproj"]
    assert core.registry.get_by_name("monoproj") is None


def test_remove_project_resolves_slug_and_name_via_flag(rig):
    core, cfg, project, store = rig
    _register(project)
    entry = core.registry.get_by_name("monoproj")
    for selector in (entry.slug, "monoproj"):
        _register(project, "monoproj")
        result = runner.invoke(app, ["remove-project", "--project", selector])
        assert result.exit_code == 0, result.output
        assert "removed " in result.output
        assert core.registry.get_by_name("monoproj") is None


def test_remove_project_flag_wins_when_both_given(rig, tmp_path):
    core, cfg, project, store = rig
    _register(project)
    _second_project(tmp_path)
    result = runner.invoke(app, ["remove-project", str(project),
                                 "--project", "otherproj"])
    assert result.exit_code == 0, result.output
    assert "removed " in result.output
    assert core.registry.get_by_name("otherproj") is None
    assert core.registry.get_by_name("monoproj") is not None
    assert store.dropped == ["idx_otherproj"]


def test_remove_project_unregistered_dies(rig):
    core, cfg, project, _store = rig
    _register(project)
    result = runner.invoke(app, ["remove-project", "--project", "nope"])
    assert result.exit_code == 1, result.output
    assert "NotFoundError:" in result.output
    assert "not registered" in result.output


def test_remove_project_refuses_to_guess_from_cwd(rig, monkeypatch):
    core, cfg, project, _store = rig
    _register(project)
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["remove-project"])
    assert result.exit_code == 2, result.output
    assert "ArgumentError:" in result.output
    assert "needs an explicit project" in result.output
    assert "registered projects:" in result.output


# --- index-more ------------------------------------------------------------

def _scope(core):
    m = core.manifest_for("monoproj")
    try:
        return [ln for ln in (m.get_meta("scope_include") or "").splitlines()
                if ln.strip()]
    finally:
        m.close()


def test_index_more_historical_two_positional_form(rig):
    core, cfg, project, _store = rig
    _register(project, include=["services/a/**"])
    result = runner.invoke(app, ["index-more", str(project), "services/b"])
    assert result.exit_code == 0, result.output
    assert "services/b" in result.output
    assert "services/b" in _scope(core)


def test_index_more_accepts_project_flag(rig):
    core, cfg, project, _store = rig
    _register(project, include=["services/a/**"])
    result = runner.invoke(app, ["index-more", "--project", "monoproj",
                                 "services/b"])
    assert result.exit_code == 0, result.output
    assert "services/b" in result.output
    assert "services/b" in _scope(core)


def test_index_more_rejects_flag_plus_two_positionals(rig):
    core, cfg, project, _store = rig
    _register(project)
    result = runner.invoke(app, ["index-more", "--project", "monoproj",
                                 str(project), "services/b"])
    assert result.exit_code == 2, result.output
    assert "ArgumentError:" in result.output
    assert "plus two positionals" in result.output


def test_index_more_refuses_to_guess_from_cwd(rig, monkeypatch):
    core, cfg, project, _store = rig
    _register(project)
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["index-more"])
    assert result.exit_code == 2, result.output
    assert "needs an explicit project" in result.output
    assert "registered projects:" in result.output


def test_index_more_requires_a_subpath(rig):
    core, cfg, project, _store = rig
    _register(project)
    result = runner.invoke(app, ["index-more", "monoproj"])
    assert result.exit_code == 2, result.output
    assert "needs a subpath" in result.output


def test_index_more_unregistered_dies(rig):
    core, cfg, project, _store = rig
    _register(project)
    result = runner.invoke(app, ["index-more", "nope", "services/b"])
    assert result.exit_code == 1, result.output
    assert "NotFoundError:" in result.output
    assert "not registered" in result.output


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
