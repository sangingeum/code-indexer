"""The vectors-on pass reads no source file: it embeds from the manifest.

This is the test that keeps the core/extension split honest. The project is
indexed once with vectors off, then the source tree is made unreadable to the
process. The vectors-on pass must still succeed, because every input it uses
— the chunk text and hash — lives in the manifest.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.manifest import Manifest  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402


class RecordingEmbedder:
    def __init__(self):
        self.texts: list[str] = []

    def dimension(self) -> int:
        return 4

    def embed(self, texts):
        self.texts.extend(texts)
        return [[0.1] * 4 for _ in texts]

    def embed_with_errors(self, texts):
        self.texts.extend(texts)
        return [[0.1] * 4 for _ in texts], []


class RecordingStore:
    def __init__(self):
        self.collections: set[str] = set()
        self.aliases: dict[str, str] = {}
        self.upserts: list[tuple[str, int]] = []

    def collection_exists(self, name: str) -> bool:
        return name in self.collections or name in self.aliases

    def create_collection(self, name: str, dim: int) -> None:
        self.collections.add(name)

    def drop_collection(self, name: str) -> None:
        self.collections.discard(name)

    def physical_name(self, name: str) -> str | None:
        if name in self.aliases:
            return self.aliases[name]
        return name if name in self.collections else None

    def _alias_exists(self, alias_name: str) -> bool:
        return alias_name in self.aliases

    def swap_collection(self, tmp: str, target: str) -> None:
        self.aliases[target] = tmp

    def upsert_points(self, name: str, points) -> int:
        self.upserts.append((name, len(points)))
        return len(points)

    def purge_file_points(self, *a, **kw) -> int:
        return 0


def _config(root) -> Config:
    return Config(ollama_url="", qdrant_url="", embed_model="stub-model",
                  index_root=str(root), embed_batch=48,
                  upsert_batch=256, max_file_bytes=1048576,
                  watch_debounce=3, watch_sweep_interval=300)


def test_vectors_on_embeds_stored_chunks_with_the_source_tree_unreadable(
        tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text(
        "def alpha():\n    return 1\n", encoding="utf-8")
    root = tmp_path / "state"
    core = Core(_config(root))
    embedder = RecordingEmbedder()
    store = RecordingStore()
    core.embedder = embedder  # type: ignore[assignment]
    core.store = store  # type: ignore[assignment]
    core.indexer.embedder = embedder  # type: ignore[assignment]
    core.indexer.store = store  # type: ignore[assignment]
    reg = Registry(os.path.join(str(root), "registry.db"))
    entry = reg.add(str(project), name="novec")

    off = core.run_index(entry.slug, str(project), vectors="off")
    assert off["state"] == "idle", off
    assert embedder.texts == []  # the service-free pass embedded nothing
    assert store.upserts == []

    # Every source path becomes unreadable to this process.
    os.chmod(project, 0o000)
    try:
        with pytest.raises(PermissionError):
            os.listdir(project)
        result = core.run_index(entry.slug, str(project), vectors="on")
    finally:
        os.chmod(project, 0o755)

    assert result["state"] == "idle", result
    assert result["result"]["chunks_embedded"] > 0
    assert store.upserts, "the vectors-on pass must upsert points"
    # The embedded text is the header plus the STORED chunk text.
    assert any(text.startswith("a.py") for text in embedder.texts)
    assert any("def alpha" in text for text in embedder.texts)

    manifest = Manifest(str(root / entry.slug / "manifest.db"))
    try:
        assert manifest.get_meta("vectors") == "on"
        assert manifest.get_meta("vectors_state") == "current"
        assert manifest.get_meta("embedding_version").startswith("stub-model/4/")
    finally:
        manifest.close()
    reg.close()
