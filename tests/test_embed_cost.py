"""Embedding-cost tests (CI-14): query instruction, content-addressed cache.

Offline: scripted embedders count Ollama-equivalent calls.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.embed_cache import EmbedCache, cache_key  # noqa: E402
from code_indexer.embedder import Embedder  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402


# ---------------------------------------------------------------------------
# query instruction
# ---------------------------------------------------------------------------

def test_query_instruction_applies_to_queries_only(monkeypatch):
    monkeypatch.setenv("QUERY_INSTRUCTION",
                       "Given a question, retrieve relevant source code")
    e = Embedder(host="http://stub", model="m")
    assert e.query_instruction_text("auth refresh") == (
        "Instruct: Given a question, retrieve relevant source code\n"
        "Query: auth refresh")


def test_query_instruction_on_by_default_after_eval_win(monkeypatch):
    """CI-14 gate result: the instruction is the DEFAULT (eval win, recall@1
    +0.048 / MRR +0.040); QUERY_INSTRUCTION='' disables it."""
    monkeypatch.delenv("QUERY_INSTRUCTION", raising=False)
    e = Embedder(host="http://stub", model="m")
    assert e.query_instruction.startswith(
        "Given a natural language question")
    assert e.query_instruction_text("q").startswith("Instruct: ")
    assert "\nQuery: " in e.query_instruction_text("q")
    monkeypatch.setenv("QUERY_INSTRUCTION", "")
    e2 = Embedder(host="http://stub", model="m")
    assert e2.query_instruction_text("auth refresh") == "auth refresh"


def test_instructed_query_never_collides_with_raw_document(monkeypatch):
    monkeypatch.setenv("QUERY_INSTRUCTION", "task")
    e = Embedder(host="http://stub", model="m")
    assert e.query_instruction_text("doc text") != "doc text"
    from code_indexer.embed_cache import cache_key as ck
    assert ck(e.query_instruction_text("doc text"), "m", 4, "v1") != \
        ck("doc text", "m", 4, "v1")


# ---------------------------------------------------------------------------
# content-addressed cache
# ---------------------------------------------------------------------------

def test_cache_roundtrip_float16(tmp_path):
    cache = EmbedCache(str(tmp_path))
    vec = [0.1, -0.25, 1000.5, -3.75]
    cache.put(cache_key("text-a", "m", 4, "v1"), vec)
    got = cache.get(cache_key("text-a", "m", 4, "v1"))
    assert len(got) == 4
    # float16 precision: ~3 decimal digits — document the tiny loss.
    for a, b in zip(vec, got):
        assert abs(a - b) < 0.05
    assert cache.get(cache_key("text-b", "m", 4, "v1")) is None
    cache.close()


def test_cache_key_separates_model_dim_version():
    a = cache_key("t", "m1", 4, "v1")
    assert cache_key("t", "m2", 4, "v1") != a
    assert cache_key("t", "m1", 8, "v1") != a
    assert cache_key("t", "m1", 4, "v2") != a
    assert cache_key("different text", "m1", 4, "v1") != a


def test_cache_eviction_lru(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBED_CACHE_MAX_GB", "0.000001")  # ~1KB cap
    cache = EmbedCache(str(tmp_path))
    for i in range(40):
        cache.put(cache_key(f"text-{i}", "m", 4, "v1"), [0.5] * 128)
    # Oldest-used evicted; recent ones retained.
    assert cache.get(cache_key("text-0", "m", 4, "v1")) is None
    assert cache.get(cache_key("text-39", "m", 4, "v1")) is not None
    cache.close()


class CountingEmbedder(Embedder):
    def __init__(self, cache=None):
        super().__init__(host="http://stub", model="m", cache=cache)
        self.calls = 0

    def _embed_batch(self, batch):
        self.calls += 1
        return [[0.1] * 4 for _ in batch]


def test_embed_uses_cache_no_second_ollama_call(tmp_path):
    cache = EmbedCache(str(tmp_path))
    e = CountingEmbedder(cache=cache)
    e._dim = 4
    texts = ["same text", "other text"]
    e.embed(texts)
    assert e.calls >= 1
    before = e.calls
    v2 = e.embed(texts)
    assert e.calls == before          # 0 new Ollama calls: all cache hits
    assert len(v2) == 2
    cache.close()


# ---------------------------------------------------------------------------
# acceptance: re-indexing an unchanged project makes 0 Ollama calls
# ---------------------------------------------------------------------------

class ScriptedIndexEmbedder:
    """Counts embed() batches (the Ollama-equivalent calls)."""

    def __init__(self, cache):
        self.cache = cache
        self.calls = 0
        self._dim = 4

    def dimension(self):
        return 4

    def query_instruction_text(self, q):
        return q

    def embed(self, texts):
        self.calls += 1
        return [[0.1] * 4 for _ in texts]

    def embed_with_errors(self, texts):
        self.calls += 1
        return [[0.1] * 4 for _ in texts], []

    def close(self):
        pass


def _rig(tmp_path):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def a():\n    return 1\n")
    (project / "b.py").write_text("def b():\n    return 2\n")
    core = Core(cfg)
    if os.environ.get("EMBED_CACHE", "1") in ("0", "false"):
        emb = ScriptedIndexEmbedder(None)
    else:
        from code_indexer.embed_cache import EmbedCache
        emb = ScriptedIndexEmbedder(EmbedCache(cfg.index_root))
    core.embedder = emb  # type: ignore[assignment]
    core.indexer.embedder = emb  # type: ignore[assignment]

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

    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    return core, reg, cfg, project


def test_reindex_unchanged_project_zero_ollama_calls(tmp_path):
    core, reg, cfg, project = _rig(tmp_path)
    entry = reg.add(str(project), name="cacheproj")
    r1 = core.run_index(entry.slug, str(project))
    assert r1["state"] == "idle"
    calls_after_first = core.embedder.calls
    assert calls_after_first > 0

    # Re-add style second pass (unchanged files): the content-addressed cache
    # must serve every chunk — zero Ollama-equivalent calls.
    r2 = core.run_index(entry.slug, str(project))
    assert r2["state"] == "idle"
    assert core.embedder.calls == calls_after_first
    reg.close()


def test_cache_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBED_CACHE", "0")
    core, reg, cfg, project = _rig(tmp_path)
    assert core.embedder.cache is None
    reg.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))