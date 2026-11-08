"""One query text against one index: the read path fixes the bytes it searches.

What a cache cannot cover is a state, not a text: a read whose dimension
probe fails used to return the raw vector and store nothing, so the next read
re-embedded the same text and searched whatever the service answered then --
and the service does not answer the same vector twice. Measured on this
machine: the first call for a text after a model reload and an immediately
repeated call differ by up to 8.4e-3 per component, with every component
moving, and at a deep limit that is 2 members in and 2 out of a 400-hit
answer.

The fix is at the tool's end and it is not a warm-up: the warm-up was
measured and does not settle it (a probe call before the query does not
change which form the query receives). The bytes are fixed instead -- the
vector is rounded to the stored representation and written on a miss, and
the dimension of a failed probe is taken from the vector received, so the
row is still written.

The residual is a limit, not a hope: a text keeps the form of its FIRST
embedding, and with the cache disabled there is nowhere to store the bytes.
This file asserts both the property and the stated limit.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from code_indexer.embed_cache import EmbedCache, stored_representation  # noqa: E402
from code_indexer.embedder import Embedder  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# A vector off the float16 grid, and the warm one the service returns
# afterwards: the measured shape of the drift, at the same scale.
COLD = [0.1234567, -0.7654321, 0.50000007, 0.33333337]
WARM = [0.1244567, -0.7644321, 0.50100007, 0.33433337]


class _ColdThenWarmClient:
    """First call for a text: the cold vector. Later calls: the warm one."""

    def __init__(self) -> None:
        self.seen: dict[str, int] = {}

    def embed(self, model, input, keep_alive=None):
        out = []
        for text in input:
            count = self.seen.get(text, 0)
            self.seen[text] = count + 1
            out.append(list(COLD if count == 0 else WARM))
        return {"embeddings": out}

    def close(self) -> None:
        pass


class _FakeOllama:
    """Stands in for the imported ollama module's ``Client`` class."""

    def __init__(self, client: _ColdThenWarmClient) -> None:
        self.client = client

    def Client(self, **kwargs):  # noqa: N802 -- mirrors the ollama API
        return self.client


def _embedder(monkeypatch, cache) -> Embedder:
    embedder = Embedder(host="http://stub", model="stub-model", cache=cache)
    monkeypatch.setattr(embedder, "_ollama", _FakeOllama(_ColdThenWarmClient()))
    return embedder


def _probe_fails() -> int:
    raise RuntimeError("dimension probe failed (the state under test)")


def test_a_failed_probe_still_fixes_the_bytes_the_read_searches(tmp_path,
                                                               monkeypatch):
    """The state the cache could not cover: probe fails, vector is raw.

    The first read may land on the cold form -- nothing can tell -- but it
    must be the form every later read searches too. Pre-fix the first read
    returned the raw cold vector and stored nothing, so the second read
    re-embedded and searched the warm one: a different answer for one request.
    """
    cache = EmbedCache(str(tmp_path / "cache"))
    try:
        embedder = _embedder(monkeypatch, cache)
        embedder.dimension_once = _probe_fails  # type: ignore[method-assign]

        first = embedder.embed_query_once("a query")
        second = embedder.embed_query_once("a query")
        assert first == second, "the second read searched different bytes"
        assert first == stored_representation(COLD), (
            "the first read must search the stored representation")
        assert second != WARM, (
            "the second read reached the service again; nothing was stored")
        rows = cache._connect().execute(
            "SELECT COUNT(*) FROM embed_cache").fetchone()[0]
        assert rows == 1, rows
    finally:
        cache.close()


def test_the_vector_the_search_uses_is_the_same_on_two_reads(tmp_path,
                                                             monkeypatch):
    """The seam a search crosses: `Core._query_vector` answers alike twice."""
    from code_indexer.config import Config
    from code_indexer.core import Core

    cache = EmbedCache(str(tmp_path / "cache"))
    try:
        cfg = Config(ollama_url="http://stub", qdrant_url="http://stub",
                     embed_model="stub-model",
                     index_root=str(tmp_path / "state"), embed_batch=8,
                     upsert_batch=8, max_file_bytes=1048576,
                     watch_debounce=1, watch_sweep_interval=3600)
        core = Core(cfg)
        embedder = _embedder(monkeypatch, cache)
        embedder.dimension_once = _probe_fails  # type: ignore[method-assign]
        core.embedder = embedder  # type: ignore[assignment]

        first = core._query_vector("a query")
        second = core._query_vector("a query")
        assert first == second
    finally:
        cache.close()


def test_a_cache_disabled_read_is_not_deterministic(tmp_path, monkeypatch):
    """The stated limit, asserted as behaviour: no cache, no fixed bytes.

    This is what the reference documents promise, and all they promise. It is
    not a defect to fix here: with the cache off the tool has nowhere to keep
    the bytes, and a warm-up does not help (the service answers the odd form
    again after any other request).
    """
    embedder = _embedder(monkeypatch, None)
    embedder._dim = len(COLD)  # the dimension is known; there is no cache

    first = embedder.embed_query_once("a query")
    second = embedder.embed_query_once("a query")
    assert first == COLD
    assert second == WARM
    assert first != second, (
        "if these ever agree the recorded limit is wrong and the reference "
        "document must be corrected")


def test_the_reference_states_the_limit_this_file_measures():
    """The documented limit is the one the test above establishes."""
    semantic = " ".join((REPO / "skill" / "references" / "semantic.md")
                       .read_text(encoding="utf-8").split())
    failures = " ".join((REPO / "skill" / "references" / "failures.md")
                       .read_text(encoding="utf-8").split())
    assert "two reads can search two different vectors" in semantic
    assert "a text keeps the form of its FIRST embedding" in semantic
    assert "there is nowhere to store the bytes" in failures
    assert "EMBED_CACHE" in failures
