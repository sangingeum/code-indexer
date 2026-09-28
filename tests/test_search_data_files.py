"""Search seam: the data-file mitigation is applied to real result sets.

Uses a stubbed store (no Qdrant) so the ranking seam — search_one's
over-fetch, down-weight, and top-k share cap — can be asserted directly.
"""

from __future__ import annotations

from code_indexer.config import Config
from code_indexer.core import Core
from code_indexer import ranking
from code_indexer.registry import ProjectEntry


class _Point:
    def __init__(self, score: float, payload: dict) -> None:
        self.score = score
        self.payload = payload


class _StubEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 4 for _ in texts]

    def dimension(self) -> int:
        return 4


class _StubStore:
    """Returns a fixed, score-ordered candidate pool."""

    def __init__(self, points: list[_Point]) -> None:
        self.points = points
        self.last_limit: int | None = None

    def search(self, name: str, vector: list[float], limit: int = 8,
               file_filter: str | None = None,
               symbol_type: str | None = None,
               language: str | None = None) -> list[_Point]:
        self.last_limit = limit
        return self.points[:limit]


def payload(file: str, lang: str, symbol: str | None = None) -> dict:
    return {
        "file": file, "lang": lang, "symbol": symbol,
        "symbol_type": "function" if symbol else None,
        "start_line": 1, "end_line": 10, "snippet": "body text",
    }


def _core(tmp_path, points: list[_Point]) -> Core:
    cfg = Config(
        ollama_url="", qdrant_url="", embed_model="stub",
        index_root=str(tmp_path / "root"), stale_ttl=60, embed_batch=48,
        upsert_batch=256, max_file_bytes=1048576, watch_debounce=3,
        watch_sweep_interval=300,
    )
    core = Core(cfg)
    core.embedder = _StubEmbedder()  # type: ignore[assignment]
    core.store = _StubStore(points)  # type: ignore[assignment]
    return core


def _entry() -> ProjectEntry:
    return ProjectEntry(path="/proj", slug="proj", created_at=0.0)


DATA_HEAVY_POOL = [
    _Point(0.536, payload(f"telemetry/run{i}.json", "json")) for i in range(5)
] + [
    _Point(0.500 - i / 100, payload(f"src/metrics{i}.py", "python",
                                    symbol=f"record_metrics_{i}"))
    for i in range(4)
]

QUERY = "summarize the metrics recorded in the latest validation run"


def test_search_one_mixes_code_into_a_data_heavy_window(tmp_path):
    core = _core(tmp_path, DATA_HEAVY_POOL)
    hits = core.search_one(_entry(), QUERY, limit=5, file_filter=None)
    window = hits[:5]
    data = sum(1 for h in window if ranking.is_data_payload(h))
    assert data <= 2, f"data-file chunks still dominate the window: {data}/5"
    assert any(not ranking.is_data_payload(h) for h in window)


def test_search_one_over_fetches_so_the_cap_has_candidates(tmp_path):
    core = _core(tmp_path, DATA_HEAVY_POOL)
    core.search_one(_entry(), QUERY, limit=5, file_filter=None)
    assert core.store.last_limit >= 15  # type: ignore[attr-defined]


def test_search_one_keeps_data_files_searchable(tmp_path):
    """Data files are down-weighted, never dropped from the result set."""
    core = _core(tmp_path, DATA_HEAVY_POOL)
    hits = core.search_one(_entry(), QUERY, limit=5, file_filter=None)
    files = {h["file"] for h in hits}
    assert "telemetry/run0.json" in files
    assert sum(1 for h in hits if ranking.is_data_payload(h)) == 5


def test_search_one_records_the_data_penalty(tmp_path):
    core = _core(tmp_path, DATA_HEAVY_POOL)
    hits = core.search_one(_entry(), QUERY, limit=5, file_filter=None)
    json_hit = next(h for h in hits if h["file"].endswith(".json"))
    assert json_hit["data_penalty"] > 0
    code_hit = next(h for h in hits if h["file"].endswith(".py"))
    assert code_hit["data_penalty"] == 0.0


def test_search_one_waives_the_mitigation_for_data_targeted_queries(tmp_path):
    core = _core(tmp_path, DATA_HEAVY_POOL)
    hits = core.search_one(_entry(), "find the json run manifest", limit=5,
                           file_filter=None)
    window = hits[:5]
    assert all(ranking.is_data_payload(h) for h in window)
