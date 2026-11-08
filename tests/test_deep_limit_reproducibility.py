"""Deep-limit answers are reproducible: same read, same index, same bytes.

A deep ``--limit`` asks the store for thousands of chunks, so equal or
near-equal scores are common. Two things used to make the answer depend on
something other than the index:

1. every score sort fell back to the order the candidates arrived in, and
   the merge below it walked a same-file group in SCORE order, so which
   ranges collapsed into one union depended on that arrival order;
2. the query embedding was recomputed whenever the dimension was unknown,
   and the service's cold-loaded run returns a materially different vector
   than its warm one (measured: 1.5e-3 to 8.5e-3 per component on the texts
   measured), so the first call after idle searched a different vector than
   the second.

The tests here pin the shape the user sees: the second invocation equals the
first, and the member set does not move.
"""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from code_indexer.embed_cache import EmbedCache  # noqa: E402
from code_indexer.embedder import Embedder  # noqa: E402
from code_indexer.ranking import (  # noqa: E402
    class_order,
    merge_overlapping,
)

DEEP_LIMIT = 3000


def _hit(file: str, start: int, end: int, score: float,
         symbol: str | None = None) -> dict:
    return {
        "project": "p", "file": file, "score": score,
        "symbol": symbol, "symbol_type": "function" if symbol else None,
        "lang": "python", "start_line": start, "end_line": end,
        "snippet": "x",
    }


def _deep_pool() -> list[dict]:
    """A deep-limit candidate pool built from the shapes that caused trouble.

    * equal scores across files (the ties a rounded cosine produces);
    * equal scores inside one file;
    * equal symbol names defined in different files;
    * nested and gapped ranges, so a merge has something to get wrong.
    """
    pool: list[dict] = []
    for i in range(60):
        file = f"src/module_{i:02d}.py"
        pool.append(_hit(file, 1 + i, 40 + i, 0.5, "shape"))
        pool.append(_hit(file, 60 + i, 90 + i, 0.5, "shape"))
        pool.append(_hit(file, 120 + i, 150 + i, 0.5, None))
    for i in range(40):
        file = f"tests/test_{i:02d}.py"
        pool.append(_hit(file, 1, 10, 0.4, "test_shape"))
        pool.append(_hit(file, 12, 20, 0.4, "test_shape"))
        pool.append(_hit(file, 200, 240, 0.35, None))
    return pool


def test_deep_limit_order_does_not_depend_on_arrival_order():
    """Same pool, different arrival order — same bytes, same members.

    The pool is far deeper than any page a caller reads (it stands in for a
    ``--limit 3000`` answer) and holds many exact score ties, which is what a
    deep limit produces. Shuffling the arrival order is exactly what a
    differently-ordered store page looks like.
    """
    pool = _deep_pool()
    shuffled = list(pool)
    random.Random(20261108).shuffle(shuffled)

    first = [f"{h['file']}:{h['start_line']}-{h['end_line']}:{h['score']}"
             for h in class_order(merge_overlapping(pool), [])]
    second = [f"{h['file']}:{h['start_line']}-{h['end_line']}:{h['score']}"
              for h in class_order(merge_overlapping(shuffled), [])]
    assert len(first) <= len(pool)
    assert len(first) > 100, "the fixture must exercise a deep answer"
    assert second == first, "the arrival order changed the answer bytes"


def test_membership_survives_a_tiny_score_perturbation():
    """The member set is a function of the hits, not of their scores.

    A vector search's scores move by 1.5e-3 to 8.5e-3 per component between
    a cold and a warm model load (on the texts measured). That must not
    change WHICH ranges the answer reports: only where they sit in the order.
    """
    pool = _deep_pool()
    jittered = []
    rng = random.Random(7)
    for hit in pool:
        moved = dict(hit)
        moved["score"] = round(hit["score"] + rng.uniform(-0.03, 0.03), 4)
        jittered.append(moved)

    def members(hits: list[dict]) -> set[tuple[str, int, int]]:
        merged = merge_overlapping(hits)
        return {(h["file"], h["start_line"], h["end_line"]) for h in merged}

    assert members(jittered) == members(pool)


def test_merge_never_invents_a_range_between_chunks():
    """A union may only cover lines some hit actually covers.

    The old score-order walk merged a hit into whichever union it met last,
    so a low-scoring hit at lines 5-50 seen after a high-scoring hit at
    100-110 produced a "union" 5-110 that no chunk spans.
    """
    hits = [_hit("a.py", 1, 10, 0.90),
            _hit("a.py", 100, 110, 0.85),
            _hit("a.py", 5, 50, 0.50)]
    merged = merge_overlapping(hits)
    covered = {(h["start_line"], h["end_line"]) for h in merged}
    assert covered == {(1, 50), (100, 110)}


class _CountingClient:
    """Fake ollama client returning a vector float16 storage cannot hold."""

    def __init__(self, vector: list[float]) -> None:
        self.vector = vector
        self.calls = 0

    def embed(self, model, input, keep_alive=None):
        self.calls += 1
        return {"embeddings": [list(self.vector) for _ in input]}

    def close(self) -> None:
        pass


class _FakeOllama:
    """Stands in for the imported ollama module's ``Client`` class."""

    def __init__(self, client: _CountingClient) -> None:
        self.client = client

    def Client(self, **kwargs):  # noqa: N802 — mirrors the ollama API
        return self.client


def test_query_embedding_is_the_same_on_the_first_read_and_the_second(tmp_path,
                                                              monkeypatch):
    """The cache is canonical: a miss and a hit search identical bytes.

    The computed vector is deliberately off the float16 grid, standing in for
    the numeric difference between a cold-loaded embedding model and a warm
    one. Pre-fix, the first read searched the raw vector and every later read
    searched its float16 rounded copy.
    """
    cache = EmbedCache(str(tmp_path))
    try:
        raw = [0.1234567, -0.7654321, 0.50000007, 0.33333337]
        client = _CountingClient(raw)
        embedder = Embedder(host="http://stub", model="stub-model", cache=cache)
        monkeypatch.setattr(embedder, "_ollama", _FakeOllama(client))
        embedder._dim = len(raw)

        first = embedder.embed_query_once("a query")
        second = embedder.embed_query_once("a query")
        assert first == second, "the second read used different bytes"
        assert first != raw, "the read path must use the stored representation"
    finally:
        cache.close()
