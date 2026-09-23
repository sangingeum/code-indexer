"""Indexer embeds chunks with the contextual header, not raw chunk text."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from code_indexer.chunker import Chunk
from code_indexer.embed_text import embed_text
from code_indexer.indexer import Indexer
from code_indexer.config import load_config


class _FakeEmbedder:
    """Captures the texts handed to embed(); returns dummy vectors."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.dim = 8

    def dimension(self) -> int:
        return self.dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [[float(len(t) % 7)] * self.dim for t in texts]


class _FakeStore:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.upserted: int = 0

    def collection_exists(self, name: str) -> bool:
        return True

    def create_collection(self, name: str, dim: int) -> None:
        self.created.append(name)

    def purge_file_points(self, *args: Any, **kwargs: Any) -> int:
        return 0

    def upsert_points(self, name: str, points: list[Any]) -> int:
        self.upserted += len(points)
        return len(points)

    def count_points(self, name: str) -> int:
        return 0


class _FakeManifest:
    def __init__(self) -> None:
        self.files: dict[str, Any] = {}

    def all_files(self) -> dict:
        return {}

    def get_meta(self, key: str) -> str:
        return ""

    def set_meta(self, key: str, value: str) -> None:
        pass

    def upsert_files(self, rows: list[Any]) -> None:
        pass

    def replace_file_symbols(self, *args: Any, **kwargs: Any) -> None:
        pass

    def mark_scanned(self, branch: Any) -> None:
        pass

    def read_git_branch(self, project_path: str) -> Any:
        return None

    def delete_files(self, paths: list[str]) -> None:
        pass

    def close(self) -> None:
        pass


def test_indexer_embeds_contextual_text(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("def main():\n    pass\n")
    fake_emb = _FakeEmbedder()
    indexer = Indexer(load_config([]), fake_emb, _FakeStore())  # type: ignore[arg-type]

    import code_indexer.indexer as ix

    monkeypatch.setattr(ix, "scan_project",
                        lambda p, max_file_bytes=None: [
                            type("S", (), {"path": "a.py", "content_hash": "h1",
                                           "size": 16,
                                           "abs_path": str(tmp_path / "a.py")})()])

    indexer.index_project(str(tmp_path), "slugx", _FakeManifest())  # type: ignore[arg-type]

    assert fake_emb.texts, "embedder received no texts"
    for t in fake_emb.texts:
        # every embedded text must carry the file header, not raw chunk only
        assert t.startswith("a.py\nsymbol: "), t
        assert "def main():" in t
