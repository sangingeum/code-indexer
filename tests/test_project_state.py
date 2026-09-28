"""Registered-but-never-indexed projects must not report a plain idle state.

A registered project whose manifest records no completed indexing pass (an
interrupted or killed `add-project`, or an index that never started) reports
`state=never-indexed`, in `list-projects` / `index-status` / `add-project`
output; a successful pass clears it back to idle.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from code_indexer.cli import app
from code_indexer.config import Config
from code_indexer.core import NEVER_INDEXED, Core

runner = CliRunner()


class StubEmbedder:
    def dimension(self) -> int:
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

    def purge_file_points(self, name, project, path, min_chunk_index=0):
        pass


def _stub_core(cfg: Config) -> Core:
    c = Core(cfg)
    c.embedder = StubEmbedder()
    c.store = StubStore()
    c.indexer.embedder = c.embedder
    c.indexer.store = c.store
    return c


@pytest.fixture()
def core(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    # The CLI keeps a process-wide Core singleton; drop it so each test's CLI
    # invocation resolves the current INDEX_ROOT instead of a previous test's.
    import code_indexer.cli as cli

    cli._core = None
    cli._core_skip = False
    cfg = Config(
        ollama_url="http://stub", qdrant_url="http://stub", embed_model="stub",
        index_root=str(tmp_path / "state"), stale_ttl=0, embed_batch=48,
        upsert_batch=256, max_file_bytes=1048576, watch_debounce=1,
        watch_sweep_interval=3600)
    c = _stub_core(cfg)
    yield c
    cli._core = None
    cli._core_skip = False


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("def a():\n    return 1\n")
    return root


def test_registered_without_a_pass_reports_never_indexed(core, project):
    entry = core.registry.add(str(project))
    assert core.has_successful_pass(entry.slug) is False
    assert core.effective_state(entry.slug) == NEVER_INDEXED
    summary = core.status_summary(entry)
    assert "state=never-indexed" in summary
    assert "last_indexed=never" in summary


def test_interrupted_pass_still_reports_never_indexed(core, project):
    """A killed initial pass leaves a manifest but no committed pass."""
    entry = core.registry.add(str(project))
    manifest = core.manifest_for(entry.slug)  # what run_index does first
    manifest.set_meta("chunk_hashes", "a.py|sha256:deadbeef")
    manifest.close()
    assert core.has_successful_pass(entry.slug) is False
    assert "state=never-indexed" in core.status_summary(entry)


def test_successful_pass_clears_never_indexed(core, project):
    entry = core.registry.add(str(project))
    assert core.effective_state(entry.slug) == NEVER_INDEXED
    result = core.run_index(entry.slug, str(project))
    assert result["state"] == "idle"
    assert core.has_successful_pass(entry.slug) is True
    assert core.effective_state(entry.slug) == "idle"
    summary = core.status_summary(entry)
    assert "state=idle" in summary
    assert "last_indexed=never" not in summary


def test_in_flight_and_error_states_are_not_refined(core, project):
    entry = core.registry.add(str(project))
    core._set_state(entry.slug, state="indexing")
    assert core.effective_state(entry.slug) == "indexing"
    core._set_state(entry.slug, state="error", error="boom")
    assert core.effective_state(entry.slug) == "error"


def test_list_projects_reports_the_distinct_state(core, project):
    core.registry.add(str(project))
    result = runner.invoke(app, ["list-projects"])
    assert result.exit_code == 0
    assert "state=never-indexed" in result.output


def test_index_status_reports_the_distinct_state(core, project):
    core.registry.add(str(project))
    result = runner.invoke(app, ["index-status", str(project)])
    assert result.exit_code == 0
    assert "state=never-indexed" in result.output
    assert "no indexing pass has completed" in result.output


def test_index_status_returns_to_idle_after_a_pass(core, project):
    entry = core.registry.add(str(project))
    core.run_index(entry.slug, str(project))
    result = runner.invoke(app, ["index-status", str(project)])
    assert result.exit_code == 0
    assert "state=idle" in result.output
    assert "never-indexed" not in result.output


def test_add_project_hints_at_the_unfinished_pass(core, project):
    core.registry.add(str(project))
    result = runner.invoke(app, ["add-project", str(project)])
    assert result.exit_code == 0
    assert "already registered" in result.output
    assert "state=never-indexed" in result.output
    assert "no indexing pass has completed" in result.output
