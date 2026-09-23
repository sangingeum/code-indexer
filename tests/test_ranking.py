"""Unit tests for query-time ranking: metadata adjustments + hybrid
weighted-sum fusion.

Pure functions — no Qdrant/Ollama needed.
"""

import pytest

from code_indexer.ranking import (
    DEFINITION_BOOST,
    LEXICAL_WEIGHT,
    TEST_PATH_PENALTY,
    hybrid_fuse,
    lexical_score,
    metadata_adjustment,
    metadata_rerank,
    query_tokens,
    tokenize,
)


def hit(score: float, file: str = "src/app.py", symbol: str | None = None,
        snippet: str = "") -> dict:
    return {
        "score": score, "file": file, "symbol": symbol,
        "symbol_type": "function" if symbol else None,
        "lang": "python", "start_line": 1, "end_line": 10,
        "snippet": snippet,
    }


def test_tokenize_lowercases_and_skips_symbols():
    assert tokenize("Retry-on ECONNRESET!") == ["retry", "on", "econnreset"]
    assert tokenize(None) == []
    assert tokenize("") == []


def test_lexical_score_rewards_symbol_name_match():
    q = query_tokens("where do we retry on ECONNRESET")
    strong = hit(0.5, symbol="retry_on_econnreset",
                 snippet="socket reconnect handling")
    weak = hit(0.5, file="src/net.py", snippet="plain socket code")
    assert lexical_score(strong, q) > lexical_score(weak, q)


def test_lexical_score_zero_without_query_tokens():
    assert lexical_score(hit(0.5, symbol="foo"), []) == 0.0


def test_metadata_adjustment_definition_boost_and_test_penalty():
    q = query_tokens("pathfinding movement of pawns on the map")
    boosted = metadata_adjustment(hit(0.5, symbol="TravelerPolicy_Neighbor"), q)
    plain = metadata_adjustment(hit(0.5, file="src/grid/mover.cs"), q)
    assert boosted == pytest.approx(DEFINITION_BOOST)
    assert plain == 0.0


def test_metadata_adjustment_test_path_penalized():
    q = query_tokens("grid neighbor offsets")
    testy = metadata_adjustment(
        hit(0.5, file="tests/helpers/NeighborsTests.cs",
            symbol="Neighbors"), q)
    assert testy == pytest.approx(DEFINITION_BOOST - TEST_PATH_PENALTY)
    # Query explicitly about tests -> no penalty.
    qtest = query_tokens("neighbors test helper")
    assert metadata_adjustment(
        hit(0.5, file="tests/helpers/NeighborsTests.cs", symbol="Neighbors"),
        qtest) == pytest.approx(DEFINITION_BOOST)


def test_metadata_rerank_reorders_and_keeps_vector_score():
    q = query_tokens("occupancy map update")
    pool = [
        hit(0.50, file="tests/fixtures/occupancy_fixture.py"),
        hit(0.48, file="src/world/occupancy.py", symbol="update_occupancy"),
    ]
    out = metadata_rerank(pool, q)
    assert out[0]["symbol"] == "update_occupancy"
    assert out[0]["vector_score"] == 0.48
    assert out[0]["score"] > out[0]["vector_score"]


def test_hybrid_fuse_promotes_lexical_match_over_vector_leader():
    q = query_tokens("retry on econnreset")
    pool = [
        hit(0.55, file="src/net/socket.py", snippet="generic socket loop"),
        hit(0.50, file="src/net/retry.py", symbol="retry_on_econnreset"),
    ]
    out = hybrid_fuse(pool, q)
    assert out[0]["symbol"] == "retry_on_econnreset"
    # Fused scores are recorded alongside the original cosine.
    assert out[0]["vector_score"] == 0.50
    assert out[0]["lexical_score"] > 0
    assert out[0]["score"] == pytest.approx(
        0.50 + LEXICAL_WEIGHT * out[0]["lexical_score"], abs=1e-3)


def test_hybrid_fuse_stable_when_lexical_is_tied():
    q = query_tokens("totally unrelated tokens")
    pool = [hit(0.6, symbol="alpha"), hit(0.5, symbol="beta")]
    out = hybrid_fuse(pool, q)
    # With no lexical signal, vector order wins.
    assert [h["symbol"] for h in out] == ["alpha", "beta"]


def test_hybrid_fuse_empty_pool():
    assert hybrid_fuse([], query_tokens("anything")) == []
