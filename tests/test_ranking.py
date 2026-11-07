"""Unit tests for query-time ranking: metadata adjustments + hybrid
weighted-sum fusion.

Pure functions — no Qdrant/Ollama needed.
"""

import pytest

from code_indexer.ranking import (
    DATA_FILE_PENALTY,
    DEFINITION_BOOST,
    LEXICAL_WEIGHT,
    cap_data_file_share,
    class_order,
    downweight_data_files,
    hybrid_fuse,
    is_data_payload,
    lexical_score,
    metadata_adjustment,
    metadata_rerank,
    query_tokens,
    targets_tests,
    tokenize,
)


def hit(score: float, file: str = "src/app.py", symbol: str | None = None,
        snippet: str = "", lang: str = "python") -> dict:
    return {
        "score": score, "file": file, "symbol": symbol,
        "symbol_type": "function" if symbol else None,
        "lang": lang, "start_line": 1, "end_line": 10,
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


def test_metadata_adjustment_definition_boost_only():
    q = query_tokens("pathfinding movement of pawns on the map")
    boosted = metadata_adjustment(hit(0.5, symbol="TravelerPolicy_Neighbor"), q)
    plain = metadata_adjustment(hit(0.5, file="src/grid/mover.cs"), q)
    assert boosted == pytest.approx(DEFINITION_BOOST)
    assert plain == 0.0


def test_metadata_adjustment_does_not_judge_paths():
    """File class is the one classifier: a test path earns no metadata penalty
    of its own (the class order demotes it), and the query has nothing to
    silence here."""
    q = query_tokens("grid neighbor offsets")
    testy = metadata_adjustment(
        hit(0.5, file="tests/helpers/NeighborsTests.cs", symbol="Neighbors"), q)
    source = metadata_adjustment(
        hit(0.5, file="src/world/neighbors.cs", symbol="Neighbors"), q)
    assert testy == source == pytest.approx(DEFINITION_BOOST)


def test_metadata_rerank_keeps_vector_score_and_class_order_demotes_tests():
    q = query_tokens("occupancy map update")
    pool = [
        hit(0.50, file="tests/fixtures/occupancy_fixture.py"),
        hit(0.48, file="src/world/occupancy.py", symbol="update_occupancy"),
    ]
    out = class_order(metadata_rerank(pool, q), q)
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


# -- data-file dominance mitigation -----------------------------------------


def test_is_data_payload_detects_data_files():
    assert is_data_payload(hit(0.5, file="telemetry/run.json", lang="json"))
    assert is_data_payload(hit(0.5, file="cfg/app.yaml", lang="yaml"))
    assert is_data_payload(hit(0.5, file="cfg/app.toml", lang="toml"))
    # extension fallback when no lang payload is present
    assert is_data_payload({"score": 0.5, "file": "data/runs.JSON"})
    assert not is_data_payload(hit(0.5, file="src/metrics.py"))
    assert not is_data_payload(hit(0.5, file="src/app.cs", lang="csharp"))


def test_downweight_data_files_promotes_code_over_data():
    pool = [
        hit(0.52, file="telemetry/run.json", lang="json"),
        hit(0.50, file="src/metrics.py", symbol="record_metrics"),
    ]
    out = downweight_data_files(pool, query_tokens("summarize run metrics"))
    assert out[0]["symbol"] == "record_metrics"
    data = next(h for h in out if h["file"].endswith(".json"))
    assert data["score"] == pytest.approx(0.52 - DATA_FILE_PENALTY)
    assert data["data_penalty"] == DATA_FILE_PENALTY
    assert data["vector_score"] == 0.52  # original cosine preserved


def test_downweight_keeps_data_files_searchable():
    pool = [hit(0.9, file="a.json", lang="json"), hit(0.4, file="b.py")]
    out = downweight_data_files(pool, query_tokens("metrics"))
    assert len(out) == 2, "data-file chunks must not be excluded"
    assert any(h["file"] == "a.json" for h in out)


def test_downweight_waived_when_query_targets_a_data_format():
    pool = [hit(0.5, file="a.json", lang="json")]
    out = downweight_data_files(pool, query_tokens("the json schema of a run"))
    assert out[0]["score"] == 0.5


def test_cap_data_file_share_mixes_code_into_the_window():
    pool = [hit(0.55 - i / 100, file=f"data/run{i}.json", lang="json")
            for i in range(5)]
    pool += [hit(0.48 - i / 100, file=f"src/m{i}.py", symbol=f"metrics_{i}")
             for i in range(4)]
    out = cap_data_file_share(
        downweight_data_files(pool, query_tokens("summarize metrics")),
        limit=5, qtokens=query_tokens("summarize metrics"))
    window = out[:5]
    assert sum(1 for h in window if is_data_payload(h)) <= 2
    assert sum(1 for h in window if not is_data_payload(h)) >= 3
    assert len(out) == len(pool), "cap must never drop hits"


def test_cap_keeps_data_files_searchable_beyond_the_window():
    pool = [hit(0.5 - i / 100, file=f"d{i}.json", lang="json") for i in range(6)]
    out = cap_data_file_share(pool, limit=5, qtokens=query_tokens("metrics"))
    assert [h["file"] for h in out if is_data_payload(h)] == \
        [f"d{i}.json" for i in range(6)]


def test_cap_waived_when_query_targets_a_data_format():
    pool = [hit(0.5 - i / 100, file=f"a{i}.json", lang="json") for i in range(5)]
    out = cap_data_file_share(pool, limit=5,
                              qtokens=query_tokens("find the yaml config"))
    assert [h["file"] for h in out] == [h["file"] for h in pool]


# -- class order: one ladder for every ranking and retrieval mode -----------


def test_class_order_promotes_source_over_a_higher_cosine_test_hit():
    """The ladder is a KEY, not a score nudge: a test hit that wins on cosine
    comes out below the source hit, and nothing leaves the answer."""
    pool = [
        hit(0.92, file="tests/test_staleness.py",
            symbol="test_stale_rejected"),
        hit(0.71, file="src/code_indexer/core.py", symbol="reject_stale"),
    ]
    out = class_order(pool, query_tokens("where do we reject a stale index"))
    assert [h["file"] for h in out] == [
        "src/code_indexer/core.py", "tests/test_staleness.py"]
    assert sorted(h["file"] for h in out) == sorted(h["file"] for h in pool)


def test_class_order_runs_the_whole_ladder():
    pool = [
        hit(0.90, file="docs/schema.md"),
        hit(0.88, file="build/generated_api.py"),
        hit(0.86, file="tests/test_api.py"),
        hit(0.84, file="examples/api_demo.py"),
        hit(0.82, file="src/api.py", symbol="api"),
    ]
    out = class_order(pool, query_tokens("run the api"))
    assert [h["file"] for h in out] == [
        "src/api.py", "examples/api_demo.py", "tests/test_api.py",
        "docs/schema.md", "build/generated_api.py"]


def test_class_order_keeps_the_modes_order_inside_a_rung():
    pool = [hit(0.9, file="src/a.py"), hit(0.8, file="src/b.py"),
            hit(0.85, file="tests/test_a.py")]
    out = class_order(pool, query_tokens("anything at all"))
    assert [h["file"] for h in out] == ["src/a.py", "src/b.py",
                                        "tests/test_a.py"]


def test_class_order_waiver_for_a_test_targeting_query():
    assert targets_tests(query_tokens("which tests cover the stale path"))
    pool = [hit(0.9, file="tests/test_stale.py", symbol="test_stale"),
            hit(0.5, file="src/stale.py", symbol="stale")]
    out = class_order(pool, query_tokens("which tests cover the stale path"))
    # The tests rung's demotion is waived: the mode's order survives across
    # source and tests.
    assert [h["file"] for h in out] == ["tests/test_stale.py",
                                        "src/stale.py"]


def test_class_order_waiver_does_not_reach_docs():
    pool = [hit(0.9, file="tests/test_x.py"), hit(0.8, file="docs/x.md")]
    out = class_order(pool, query_tokens("fixture test helpers"))
    assert [h["file"] for h in out] == ["tests/test_x.py", "docs/x.md"]


def test_data_format_waiver_keeps_the_data_hit_on_top():
    """A data-format query waives the pure-data down-weight, and the class
    order then leaves the two source-rung hits in relevance order."""
    pool = [hit(0.52, file="telemetry/run.json", lang="json"),
            hit(0.50, file="src/metrics.py", symbol="record_metrics")]
    qplain = query_tokens("summarize the run metrics")
    ordered = class_order(downweight_data_files(pool, qplain), qplain)
    assert ordered[0]["file"] == "src/metrics.py"

    qdata = query_tokens("the json run manifest")
    waived = class_order(downweight_data_files(pool, qdata), qdata)
    assert waived[0]["file"] == "telemetry/run.json"
    assert waived[0]["score"] == pytest.approx(0.52)
