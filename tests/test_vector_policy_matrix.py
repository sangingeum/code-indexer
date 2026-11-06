"""The version policy matrix: three axes, and which bump invalidates what.

    extraction bump   -> rows invalidated, collection and dimension intact
    embedding bump    -> the collection is replaced, rows untouched
    schema bump       -> the manifest migrates on open, collection intact

Plus the one rule that is not negotiable: vectors are never mixed across
models, so a changed identity is a NEW collection under its own name.
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
from code_indexer.errors import RebuildRequiredError, VectorsDisabledError  # noqa: E402
from code_indexer.graph_extract import EXTRACTION_VERSION  # noqa: E402
from code_indexer.manifest import SCHEMA_VERSION, Manifest  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()


class StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]

    def embed_with_errors(self, texts):
        return [[0.1] * 4 for _ in texts], []


class StubStore:
    def __init__(self):
        self.collections: set[str] = set()
        self.aliases: dict[str, str] = {}
        self.dropped: list[str] = []
        self.upserts: list[tuple[str, int]] = []

    def collection_exists(self, name: str) -> bool:
        return name in self.collections or name in self.aliases

    def create_collection(self, name: str, dim: int) -> None:
        self.collections.add(name)

    def drop_collection(self, name: str) -> None:
        self.dropped.append(name)
        self.collections.discard(name)

    def physical_name(self, name: str) -> str | None:
        if name in self.aliases:
            return self.aliases[name]
        return name if name in self.collections else None

    def _alias_exists(self, alias_name: str) -> bool:
        return alias_name in self.aliases

    def swap_collection(self, tmp: str, target: str) -> None:
        old = self.physical_name(target)
        if old == tmp:
            return
        if old == target and not self._alias_exists(target):
            self.collections.discard(target)
            self.dropped.append(target)
        self.aliases[target] = tmp
        if old is not None and old != target and old != tmp:
            self.dropped.append(old)
            self.collections.discard(old)

    def upsert_points(self, name: str, points) -> int:
        self.upserts.append((name, len(points)))
        return len(points)

    def purge_file_points(self, *a, **kw) -> int:
        return 0

    def search(self, name, vector, limit=8, file_filter=None,
               symbol_type=None, language=None):
        return []


def _core(root, model: str = "stub-model"):
    cfg = Config(ollama_url="", qdrant_url="", embed_model=model,
                 index_root=str(root), stale_ttl=60, embed_batch=48,
                 upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    core = Core(cfg)
    embedder = StubEmbedder()
    store = StubStore()
    core.embedder = embedder  # type: ignore[assignment]
    core.store = store  # type: ignore[assignment]
    core.indexer.embedder = embedder  # type: ignore[assignment]
    core.indexer.store = store  # type: ignore[assignment]
    return core, store


def _project_and_entry(tmp_path, root, core):
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    (project / "a.py").write_text(
        "def alpha():\n    return 1\n", encoding="utf-8")
    reg = Registry(os.path.join(str(root), "registry.db"))
    entry = reg.get_by_path(str(project)) or reg.add(str(project), name="pol")
    return project, entry, reg


def test_embedding_bump_replaces_the_collection_and_keeps_the_rows(tmp_path):
    root = tmp_path / "state"
    core, store = _core(root, "model-a")
    project, entry, reg = _project_and_entry(tmp_path, root, core)
    assert core.run_index(entry.slug, str(project), vectors="on")["state"] == "idle"

    manifest = Manifest(str(root / entry.slug / "manifest.db"))
    try:
        rows_before = {path: (f.content_hash, f.size)
                       for path, f in manifest.all_files().items()}
        chunks_before = [(c.file, c.chunk_index, c.chunk_hash)
                         for c in manifest.all_chunks()]
        identity_a = manifest.get_meta("embedding_version")
        collection_a = store.physical_name(f"idx_{entry.slug}")
    finally:
        manifest.close()
    upsert_files_before = len(store.upserts)

    core_b, _store_b = _core(root, "model-b")
    core_b.registry = core.registry
    # One vector store across model configs (as a real Qdrant is).
    core_b.store = store  # type: ignore[assignment]
    core_b.indexer.store = store  # type: ignore[assignment]
    result = core_b.run_index(entry.slug, str(project))
    assert result["state"] == "idle", result
    assert not result["result"]["files_indexed"], "a vector rebuild reparses nothing"

    manifest = Manifest(str(root / entry.slug / "manifest.db"))
    try:
        rows_after = {path: (f.content_hash, f.size)
                      for path, f in manifest.all_files().items()}
        chunks_after = [(c.file, c.chunk_index, c.chunk_hash)
                        for c in manifest.all_chunks()]
        identity_b = manifest.get_meta("embedding_version")
        assert manifest.get_meta("vectors_state") == "current"
    finally:
        manifest.close()
    assert rows_after == rows_before
    assert chunks_after == chunks_before
    assert identity_a != identity_b and identity_b.startswith("model-b/")
    # A NEW collection under the new identity, old one dropped after the swap.
    live = store.aliases.get(f"idx_{entry.slug}")
    assert live is not None and live != collection_a
    assert collection_a in store.dropped
    # Re-embedded every chunk into the new collection.
    assert any(name == live for name, _n in store.upserts[upsert_files_before:])
    reg.close()


def test_schema_bump_migrates_the_manifest_and_leaves_the_collection(tmp_path):
    root = tmp_path / "state"
    core, store = _core(root)
    project, entry, reg = _project_and_entry(tmp_path, root, core)
    assert core.run_index(entry.slug, str(project), vectors="on")["state"] == "idle"
    upserts_before = list(store.upserts)

    # Pretend an older schema wrote this manifest.
    path = str(root / entry.slug / "manifest.db")
    manifest = Manifest(path)
    try:
        manifest.set_meta("schema_version", "6")
        manifest.set_meta("index_schema_version", "6")
    finally:
        manifest.close()

    migrated = Manifest(path)  # the open migrates, and writes nothing else
    try:
        assert migrated.get_meta("schema_version") == SCHEMA_VERSION
        assert migrated.get_meta("index_schema_version") == SCHEMA_VERSION
        assert migrated.count_chunks() > 0
    finally:
        migrated.close()
    assert store.upserts == upserts_before, "a schema bump must not re-embed"
    reg.close()


def test_extraction_bump_invalidates_rows_but_keeps_the_collection(tmp_path):
    root = tmp_path / "state"
    core, store = _core(root)
    project, entry, reg = _project_and_entry(tmp_path, root, core)
    assert core.run_index(entry.slug, str(project), vectors="on")["state"] == "idle"
    collection = store.physical_name(f"idx_{entry.slug}")

    manifest = Manifest(str(root / entry.slug / "manifest.db"))
    try:
        manifest.set_meta("extraction_version", str(EXTRACTION_VERSION - 1))
    finally:
        manifest.close()

    assert core.vectors_status(entry)["rows"] == "absent"
    with pytest.raises(RebuildRequiredError):
        core.assert_rows_acceptable(entry)
    # The collection is untouched by the row verdict.
    assert store.physical_name(f"idx_{entry.slug}") == collection
    reg.close()


def test_semantic_search_on_vectors_off_is_a_config_error(tmp_path, monkeypatch):
    root = tmp_path / "state"
    core, store = _core(root)
    project, entry, reg = _project_and_entry(tmp_path, root, core)
    assert core.run_index(entry.slug, str(project), vectors="off")["state"] == "idle"
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)

    result = runner.invoke(app, ["semantic-search", "alpha",
                                 "--project", str(project)])
    assert result.exit_code == 4, (result.output, result.stderr)
    assert result.stdout == ""
    assert result.stderr.startswith("ConfigError: ")
    assert "--vectors on" in result.stderr
    # It never degrades to a successful lexical answer.
    assert "search results" not in result.output
    reg.close()


def test_semantic_search_on_a_stale_embedding_identity_refuses(tmp_path, monkeypatch):
    root = tmp_path / "state"
    core, store = _core(root, "model-a")
    project, entry, reg = _project_and_entry(tmp_path, root, core)
    assert core.run_index(entry.slug, str(project), vectors="on")["state"] == "idle"

    core_b, _store_b = _core(root, "model-b")
    core_b.registry = core.registry
    monkeypatch.setattr(cli, "_get_core", lambda skip: core_b)
    result = runner.invoke(app, ["semantic-search", "alpha",
                                 "--project", str(project)])
    assert result.exit_code == 4, (result.output, result.stderr)
    assert result.stdout == ""
    assert "model-b" in result.stderr and "reindex-project" in result.stderr
    reg.close()


def test_vectors_off_refusal_is_the_config_error_family():
    exc = VectorsDisabledError("x")
    assert exc.render().startswith("ConfigError: ")
    assert exc.exit_code == 4
