"""A stub-backend index rig shared by the accuracy tests.

The indexer's only external needs are an embedder and a vector store; both
are stubbed here exactly as the other offline suites do it, so a full pass
runs with no Ollama and no Qdrant. ``index_project`` registers a project and
runs one pass, which is what an accuracy assertion about the manifest needs.
"""

from __future__ import annotations

import os

from code_indexer.config import Config
from code_indexer.core import Core
from code_indexer.registry import Registry


class _StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 4 for _ in texts]

    def embed_with_errors(self, texts: list[str]):
        return [[0.1] * 4 for _ in texts], []

    def query_instruction_text(self, query: str) -> str:
        return query

    def close(self) -> None:
        pass


class _StubStore:
    def collection_exists(self, name: str) -> bool:
        return True

    def create_collection(self, name: str, dim: int) -> None:
        pass

    def upsert_points(self, name: str, points: list) -> None:
        pass

    def purge_file_points(self, *args, **kwargs) -> int:
        return 0

    def count_points(self, name: str) -> int:
        return 0

    def swap_collection(self, collection: str, alias: str) -> None:
        pass

    def search(self, *args, **kwargs) -> list:
        return []


def stub_core(tmp_path, *, max_file_bytes: int = 1_048_576) -> Core:
    """A Core whose semantic clients are stubs."""
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256,
                 max_file_bytes=max_file_bytes,
                 watch_debounce=3, watch_sweep_interval=300)
    core = Core(cfg)
    core.embedder = _StubEmbedder()  # type: ignore[assignment]
    core.store = _StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    return core


def index_project(core: Core, project_path, name: str = "accuracyproj"):
    """Register ``project_path`` and run one indexing pass."""
    reg = Registry(os.path.join(core.cfg.index_root, "registry.db"))
    entry = reg.add(str(project_path), name=name)
    core.run_index(entry.slug, str(project_path))
    return reg, entry
