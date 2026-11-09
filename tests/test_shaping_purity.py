"""The retrieval pool is read-only: shaping it twice returns the same answer.

``search_both_shapings`` shapes one retrieval's pool twice (the shipped merge
and the unmerged arm), and the review of that round found the invariant
written nowhere and pinned by no test -- only a deepcopy comment that claimed
two passes rewrote ``score`` and ``snippet`` in place, which none of them does
(``cap_data_file_share`` only partitions, ``truncate_snippets`` writes into a
copy, the ordering passes return new lists of the same references).

These tests make the guarantee real rather than incidental: shaping one pool
twice must produce equal answers AND leave the pool byte-identical, and the
last test proves the assertion bites the moment a pass starts writing into
the hits it is handed.
"""

from __future__ import annotations

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from code_indexer import ranking  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

LONG_SNIPPET = "def refresh_token():\n    return load_store()\n" * 12
OPAQUE_SNIPPET = "class Store:\n    pass"


@pytest.fixture()
def shaped_core(tmp_path):
    """A Core whose only job here is the shaping (no store, no embedder)."""
    index_root = tmp_path / "index"
    index_root.mkdir(parents=True, exist_ok=True)
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(index_root), embed_batch=48,
                 upsert_batch=256, max_file_bytes=1048576, watch_debounce=3,
                 watch_sweep_interval=300)
    project = tmp_path / "project"
    project.mkdir()
    reg = Registry(os.path.join(str(index_root), "registry.db"))
    entry = reg.add(str(project), name="shapeproj")
    core = Core(cfg)
    yield core, entry
    reg.close()


def _hit(project: str, file: str, symbol: str | None, start: int, end: int,
         score: float, text: str, lang: str = "python",
         symbol_type: str | None = "function") -> dict:
    return {
        "project": project,
        "file": file,
        "score": score,
        "symbol": symbol,
        "symbol_type": symbol_type,
        "lang": lang,
        "start_line": start,
        "end_line": end,
        "snippet": text,
        "file_hash": "hash-of-it",
        "indexed_at": "2026-11-08T00:00:00Z",
    }


def _pool(project: str) -> dict[str, list[dict]]:
    """One pool with a reason to exercise every shaping pass.

    Adjacent same-file hits (the merge), a data chunk near the top (the share
    cap), a test file (the class ladder), overlapping siblings (the
    repeat-coverage order) and snippets far over the budget (the trim).
    """
    return {project: [
        _hit(project, "token_store.py", "refresh_token", 1, 3, 0.95,
             LONG_SNIPPET),
        _hit(project, "token_store.py", "store_token", 4, 6, 0.94,
             LONG_SNIPPET),
        _hit(project, "tests/test_token_store.py", "test_refresh", 1, 4,
             0.93, OPAQUE_SNIPPET),
        _hit(project, "config.json", None, 1, 1, 0.95,
             '{"key": "value"}', lang="json", symbol_type=None),
        _hit(project, "runner.py", "run", 10, 12, 0.90, OPAQUE_SNIPPET),
        _hit(project, "runner.py", "run_again", 11, 13, 0.89,
             OPAQUE_SNIPPET),
    ]}


def _shape(core: Core, entry, pool: dict, query: str = "refresh the token",
           limit: int = 4, max_chars: int = 300):
    return core._shape_answer(pool, [entry], [], query, limit, merge=True,
                              per_file=2, max_chars=max_chars)


def test_shaping_one_pool_twice_returns_the_same_answer_and_leaves_it_alone(
        shaped_core):
    """The property: the pool is read-only to the shaping.

    Equal answers prove the shaping is a function of its input; an unchanged
    pool proves nothing in the pass chain wrote into a hit or a list the
    caller still holds -- the invariant ``search_both_shapings`` relies on
    when it shapes the second arm.
    """
    core, entry = shaped_core
    pool = _pool(entry.path)
    snapshot = copy.deepcopy(pool)

    first = _shape(core, entry, pool)
    second = _shape(core, entry, pool)

    assert first == second
    assert pool == snapshot


def test_the_shaping_the_purity_test_covers_really_shapes(shaped_core):
    """Guard against a vacuous purity test: the passes must have done work."""
    core, entry = shaped_core
    answer = _shape(core, entry, _pool(entry.path), limit=6, max_chars=1200)
    hits = answer["hits"]
    # The merge collapsed the two adjacent token_store hits into one union.
    assert any(h.get("merged_count") == 2 for h in hits)
    # The budget trim shortened the long snippets.
    assert max(len(h["snippet"]) for h in hits) < len(LONG_SNIPPET)
    # The class ladder put the source hit ahead of the test hit.
    files = [h["file"] for h in hits]
    assert files.index("token_store.py") < files.index(
        "tests/test_token_store.py")


def test_an_in_place_pass_breaks_the_property_the_test_pins(
        shaped_core, monkeypatch):
    """A pass that writes into its input must fail the purity assertion.

    This is the test's own teeth: the same call, with one shaping pass made
    to mutate a score in place, has to change the caller's pool.
    """
    core, entry = shaped_core
    pool = _pool(entry.path)
    snapshot = copy.deepcopy(pool)
    real = ranking.cap_data_file_share

    def mutating(hits, *args, **kwargs):
        for hit in hits:
            hit["score"] = round(hit["score"] - 0.01, 4)
        return real(hits, *args, **kwargs)

    monkeypatch.setattr(ranking, "cap_data_file_share", mutating)
    first = _shape(core, entry, pool)
    assert pool != snapshot

    # And the second shaping of the already-rewritten pool sees the change.
    second = _shape(core, entry, pool)
    assert second != first
