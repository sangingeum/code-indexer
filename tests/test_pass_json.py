"""The four pass commands' --json mode (ADR-0003 section 7).

A pass reports its own work, so its JSON object carries the fields the text
renderer prints -- the path, the slug, the effective vector mode, the scope
where there is one, and the pass outcome as data -- and never the staleness
block, which belongs to commands that answer from the index. ``schema`` stays
1 and a failure carries ``ErrorType``.
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
        self.collections: set[str] = set()
        self.aliases: dict[str, str] = {}

    def collection_exists(self, name):
        return True

    def collection_names(self):
        return set(self.collections)

    def physical_name(self, name):
        if name in self.aliases:
            return self.aliases[name]
        return name if self.collection_exists(name) else None

    def drop_alias(self, name):
        return self.aliases.pop(name, None) is not None

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
        self.collections.discard(name)
        for alias, target in list(self.aliases.items()):
            if target == name:
                del self.aliases[alias]


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "monorepo"
    for rel in ("services/a/handler.py", "services/b/worker.py"):
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


def _json(result) -> dict:
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# --- add-project -----------------------------------------------------------

def test_add_project_json_reports_the_registration_and_the_pass(rig):
    core, _cfg, project, _store = rig
    payload = _json(runner.invoke(app, ["add-project", str(project),
                                        "--name", "monoproj", "--json"]))
    assert payload["schema"] == 1
    assert payload["command"] == "add-project"
    assert payload["path"] == str(project)
    assert payload["slug"] == "monoproj"
    assert payload["registered"] is True
    assert payload["already_registered"] is False
    assert payload["vectors"] == "off"
    assert payload["index"]["state"] == "idle"
    assert isinstance(payload["index"]["result"], dict)
    assert any("vectors=off" in note for note in payload["notes"])


def test_add_project_json_says_already_registered(rig):
    _core, _cfg, project, _store = rig
    runner.invoke(app, ["add-project", str(project), "--name", "monoproj"])
    payload = _json(runner.invoke(app, ["add-project", str(project), "--json"]))
    assert payload["already_registered"] is True
    assert payload["index"] is None


def test_add_project_json_reports_a_failure_as_the_error_type(rig):
    _core, _cfg, project, _store = rig
    result = runner.invoke(app, ["add-project", str(project / "gone"), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ErrorType"] == "NotFoundError"
    assert payload["schema"] == 1


# --- index-more ------------------------------------------------------------

def test_index_more_json_reports_the_scope_and_the_pass(rig):
    _core, _cfg, project, _store = rig
    runner.invoke(app, ["add-project", str(project), "--name", "monoproj"])
    payload = _json(runner.invoke(
        app, ["index-more", "--project", "monoproj", "services/b", "--json"]))
    assert payload["command"] == "index-more"
    assert payload["scope_include"] == ["services/b"]
    assert payload["index"]["state"] == "idle"
    assert payload["path"] == str(project)


# --- remove-project --------------------------------------------------------

def test_remove_project_json_reports_the_drop(rig):
    core, _cfg, project, store = rig
    runner.invoke(app, ["add-project", str(project), "--name", "monoproj"])
    payload = _json(runner.invoke(app, ["remove-project", "monoproj", "--json"]))
    assert payload["command"] == "remove-project"
    assert payload["removed"] is True
    assert payload["slug"] == "monoproj"
    assert payload["alias"] == "idx_monoproj"
    assert core.registry.get_by_name("monoproj") is None
    assert isinstance(payload["collections"], list)


# --- reindex-project -------------------------------------------------------

def test_reindex_project_json_reports_the_rebuild(rig):
    _core, _cfg, project, _store = rig
    runner.invoke(app, ["add-project", str(project), "--name", "monoproj"])
    payload = _json(runner.invoke(
        app, ["reindex-project", "--project", "monoproj", "--json"]))
    assert payload["command"] == "reindex-project"
    assert payload["slug"] == "monoproj"
    assert payload["vectors"] == "off"
    assert payload["index"]["state"] == "idle"


# --- the text surface is unchanged ----------------------------------------

def test_the_text_output_still_reads_the_same(rig):
    _core, _cfg, project, _store = rig
    result = runner.invoke(app, ["add-project", str(project), "--name", "monoproj"])
    assert result.exit_code == 0, result.output
    assert f"registered {project} (slug monoproj)" in result.output
    assert "note: vectors=off" in result.output
    assert "monoproj: " in result.output
