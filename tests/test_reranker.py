"""Reranker interface + heuristic reranker tests (retrieval work item).

All offline; deterministic for fixed input by construction.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.reranker import (HeuristicReranker, NoReranker,  # noqa: E402
                                   RERANK_POOL, select_reranker)


def _hit(file="src/app.py", symbol=None, score=0.9, start=1, end=5):
    return {"project": "/p", "file": file, "start_line": start, "end_line": end,
            "score": score, "symbol": symbol,
            "symbol_type": "function" if symbol else None, "lang": "python",
            "snippet": "code"}


def test_interface_select_and_noop():
    assert isinstance(select_reranker("heuristic"), HeuristicReranker)
    assert isinstance(select_reranker("none"), NoReranker)
    with pytest.raises(ValueError, match="unknown rerank mode"):
        select_reranker("cross-encoder")
    hits = [_hit()]
    assert NoReranker().rerank(hits, "query") == hits


def test_symbol_token_overlap_boosts():
    hits = [_hit(file="src/other.py", symbol=None, score=0.80),
            _hit(file="src/session.py", symbol="refresh_token", score=0.79)]
    out = HeuristicReranker().rerank(hits, "refresh token expiry")
    assert out[0]["symbol"] == "refresh_token"  # boosted past the higher score
    assert out[0]["rerank_delta"] > 0


def test_path_token_overlap_boosts():
    hits = [_hit(file="src/unrelated.py", symbol=None, score=0.80),
            _hit(file="src/auth/session.py", symbol=None, score=0.79)]
    out = HeuristicReranker().rerank(hits, "session handling")
    assert out[0]["file"] == "src/auth/session.py"


def test_fuzzy_subtoken_boost():
    hits = [_hit(file="src/x.py", symbol="refreshToken", score=0.78),
            _hit(file="src/y.py", symbol="other_thing", score=0.79)]
    out = HeuristicReranker().rerank(hits, "refresh token")
    assert out[0]["symbol"] == "refreshToken"  # camelCase sub-token match


def test_repeat_file_penalty_diversifies():
    hits = [
        _hit(file="a.py", score=0.90),
        _hit(file="a.py", score=0.89, start=10, end=15),
        _hit(file="a.py", score=0.88, start=20, end=25),
        _hit(file="b.py", score=0.86),
    ]
    out = HeuristicReranker().rerank(hits, "unrelated query terms")
    files = [h["file"] for h in out]
    # b.py outranks the third a.py hit (penalty from the 3rd onward).
    assert files.index("b.py") < files.index("a.py", 2)
    # No random jitter: same input, same output.
    again = HeuristicReranker().rerank(hits, "unrelated query terms")
    assert again == out


def test_pool_cap_constant():
    assert RERANK_POOL == 30


def test_query_without_tokens_is_noop():
    hits = [_hit()]
    assert HeuristicReranker().rerank(hits, "") == hits