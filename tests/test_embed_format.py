"""Embedding-text format guard: mismatch rebuilds, match reuses, fresh records.

A stored vector is only valid for the embed_text() construction that produced
it. The manifest records that construction in the ``embed_format`` meta key;
an incremental pass that finds a different (or absent) value must re-embed
everything rather than reuse cached chunk hashes, and a fresh pass records the
key so the comparison has a baseline.
"""

from __future__ import annotations

from code_indexer.config import Config
from code_indexer.embed_text import EMBED_FORMAT
from code_indexer.indexer import Indexer
from code_indexer.manifest import Manifest


class _FakeEmbedder:
    """Captures embedded texts; returns constant dummy vectors."""

    def __init__(self) -> None:
        self.dim = 4
        self.texts: list[str] = []

    def dimension(self) -> int:
        return self.dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [[0.1] * self.dim for _ in texts]


class _FakeStore:
    def __init__(self) -> None:
        self.created: list[str] = []

    def collection_exists(self, name: str) -> bool:
        return True

    def create_collection(self, name: str, dim: int) -> None:
        self.created.append(name)

    def purge_file_points(self, *args: object, **kwargs: object) -> int:
        return 0

    def upsert_points(self, name: str, points: list[object]) -> int:
        return len(points)


def _scanned(path: str, abs_path: str, content_hash: str) -> object:
    return type("S", (), {
        "path": path, "content_hash": content_hash, "size": 16,
        "abs_path": abs_path,
    })()


def _cfg(tmp_path) -> Config:
    return Config(
        ollama_url="", qdrant_url="", embed_model="stub",
        index_root=str(tmp_path / "root"), stale_ttl=60, embed_batch=48,
        upsert_batch=256, max_file_bytes=1048576, watch_debounce=3,
        watch_sweep_interval=300,
    )


def _setup(tmp_path, monkeypatch):
    """Indexer + fakes + a real manifest, over one tiny file.

    The content hash the scanner reports is mutable via the returned holder,
    so a test can simulate a file change without touching the chunk text
    (which keeps the chunk_hash stable — the cache-reuse case).
    """
    src = tmp_path / "a.py"
    src.write_text("def main():\n    pass\n")
    indexer = Indexer(_cfg(tmp_path), _FakeEmbedder(),  # type: ignore[arg-type]
                      _FakeStore())
    holder = ["h1"]

    import code_indexer.indexer as ix

    monkeypatch.setattr(
        ix, "scan_project",
        lambda p, max_file_bytes=None: [
            _scanned("a.py", str(src), holder[0])])

    manifest = Manifest(str(tmp_path / "manifest.db"))
    return indexer, indexer.embedder, manifest, holder


def test_fresh_manifest_records_the_format(tmp_path, monkeypatch):
    indexer, _embedder, manifest, _holder = _setup(tmp_path, monkeypatch)
    try:
        result = indexer.index_project(str(tmp_path), "slugx", manifest)
        assert result.chunks_embedded > 0
        assert manifest.get_meta("embed_format") == EMBED_FORMAT
    finally:
        manifest.close()


def test_matching_format_reuses_cached_chunks(tmp_path, monkeypatch):
    indexer, embedder, manifest, holder = _setup(tmp_path, monkeypatch)
    try:
        indexer.index_project(str(tmp_path), "slugx", manifest)
        assert manifest.get_meta("embed_format") == EMBED_FORMAT
        # File "changed" (new content hash) but the chunk text is identical,
        # so with a matching format the chunk hash is reused — no re-embed.
        holder[0] = "h2"
        embedder.texts.clear()
        second = indexer.index_project(str(tmp_path), "slugx", manifest)
        assert second.chunks_reused > 0
        assert second.chunks_embedded == 0
        assert not embedder.texts, "matching format must not re-embed"
    finally:
        manifest.close()


def test_format_mismatch_triggers_full_rebuild(tmp_path, monkeypatch):
    indexer, embedder, manifest, holder = _setup(tmp_path, monkeypatch)
    try:
        indexer.index_project(str(tmp_path), "slugx", manifest)
        # Simulate an index whose vectors came from an older construction.
        manifest.set_meta("embed_format", "bare-text-v0")
        holder[0] = "h2"
        embedder.texts.clear()
        third = indexer.index_project(str(tmp_path), "slugx", manifest)
        assert third.chunks_embedded > 0, "mismatch must re-embed everything"
        assert third.chunks_reused == 0
        assert embedder.texts, "mismatch must hand texts to the embedder"
        assert manifest.get_meta("embed_format") == EMBED_FORMAT
    finally:
        manifest.close()


def test_format_key_recorded_even_when_nothing_to_embed(tmp_path, monkeypatch):
    """A second unchanged pass still leaves the format recorded (baseline)."""
    indexer, _embedder, manifest, _holder = _setup(tmp_path, monkeypatch)
    try:
        indexer.index_project(str(tmp_path), "slugx", manifest)
        manifest.set_meta("embed_format", "")  # pretend it was lost
        indexer.index_project(str(tmp_path), "slugx", manifest)
        assert manifest.get_meta("embed_format") == EMBED_FORMAT
    finally:
        manifest.close()
