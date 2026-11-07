"""Reads answer from the manifest and never dial a service for that verdict.

`list-projects`, `lookup-project`, `index-status` and `doctor` reach their
answers from the registry, the manifests and configuration alone. The
fingerprint verdict compares the keys configuration can state; the recorded
embedding dimension is reported `unverified` because a read path never probes
for it. Any attempt to build a client fails the test.
"""

from __future__ import annotations

import os

import pytest
from typer.testing import CliRunner

import code_indexer.cli as cli
from code_indexer.cli import app
from code_indexer.config import Config
from code_indexer.core import UNVERIFIED, Core
from code_indexer.fingerprint import configured_values
from code_indexer.registry import Registry

runner = CliRunner()


def _config(index_root) -> Config:
    return Config(ollama_url="http://127.0.0.1:1",
                  qdrant_url="http://127.0.0.1:1", embed_model="stub",
                  index_root=str(index_root), embed_batch=48,
                  upsert_batch=256, max_file_bytes=1048576, watch_debounce=3,
                  watch_sweep_interval=300)


def _rig(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    core = Core(_config(tmp_path / "state"))
    reg = Registry(os.path.join(core.cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="p")
    core.registry = reg
    manifest = core.manifest_for(entry.slug)
    try:
        manifest.set_meta("last_indexed", "1700000000")
        for key, value in configured_values(core.cfg.embed_model).items():
            manifest.set_meta(key, value)
        manifest.set_meta("embed_dim", "4")
    finally:
        manifest.close()

    def refuse(self):
        raise AssertionError("a read constructed a semantic client")

    monkeypatch.setattr(Core, "_make_store", refuse)
    monkeypatch.setattr(Core, "_make_embedder", refuse)
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    return core, entry


def test_core_construction_builds_neither_client(tmp_path, monkeypatch):
    core = Core(_config(tmp_path / "state"))
    assert core._embedder is None
    assert core._store is None
    assert core._indexer is None


def test_lookup_project_contacts_no_service(tmp_path, monkeypatch):
    core, entry = _rig(tmp_path, monkeypatch)
    result = runner.invoke(app, ["lookup-project", entry.path])
    assert result.exit_code == 0, result.output
    assert f"registered path={entry.path}" in result.output
    assert "collection=idx_" in result.output


def test_index_status_contacts_no_service(tmp_path, monkeypatch):
    core, entry = _rig(tmp_path, monkeypatch)
    result = runner.invoke(app, ["index-status", entry.path])
    assert result.exit_code == 0, result.output
    assert "state=idle" in result.output
    assert "fingerprint=unverified" in result.output
    assert "needs-reindex" not in result.output


def test_the_verdict_is_unverified_when_only_the_dimension_is_unknown(
        tmp_path, monkeypatch):
    core, entry = _rig(tmp_path, monkeypatch)
    status = core.fingerprint_status(entry)
    assert status["state"] == UNVERIFIED
    assert status["ok"] is True
    core.assert_fingerprint_ok(entry)


def test_a_vector_side_mismatch_is_stale_not_a_row_rebuild(tmp_path, monkeypatch):
    core, entry = _rig(tmp_path, monkeypatch)
    manifest = core.manifest_for(entry.slug)
    try:
        manifest.set_meta("embed_model", "a-different-model")
    finally:
        manifest.close()
    status = core.fingerprint_status(entry)
    # An embedding-only change is not a row problem: the rows answer normally
    # and the VECTOR side is what says stale (ADR-0001 section 5).
    assert status["ok"] is True
    assert status["vectors"] == "stale"
    with pytest.raises(Exception, match="reindex-project"):
        core.assert_fingerprint_ok(entry)
