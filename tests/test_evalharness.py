"""Eval harness tests: metrics math, query parsing, compare, CLI wiring.

All offline: a fake embedder produces deterministic hash-based vectors and the
store seam is stubbed, so a fixed index yields deterministic metrics (the CI-16
determinism requirement) with no Ollama/Qdrant traffic.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer import evalharness  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

# ---------------------------------------------------------------------------
# fixtures: a tiny project + fake embedder, deterministic vectors
# ---------------------------------------------------------------------------

PROJECT_FILES = {
    "alpha.py": "def refresh_token():\n    return 1\n\n\ndef other():\n    return 2\n",
    "beta.py": "class Store:\n    def save(self):\n        return 3\n",
    "data.json": "{\"key\": \"value\"}\n",
}


class FakeEmbedder:
    """Deterministic embedder: sha256 of the text drives the vector dims."""

    def __init__(self, dim: int = 16):
        self.dim = dim

    def dimension(self) -> int:
        return self.dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            digest = hashlib.sha256(t.encode("utf-8")).digest()
            raw = digest * (self.dim // len(digest) + 1)
            vec = [v / 255.0 for v in raw[:self.dim]]
            out.append(vec)
        return out


class _FakeStore:
    """Search-side store stub: serves pre-baked points for the fake vectors."""

    def __init__(self) -> None:
        # collection -> list of objects with .score/.payload
        self.points: dict[str, list] = {}

    def collection_exists(self, name: str) -> bool:
        return True

    def create_collection(self, name: str, dim: int) -> None:
        pass

    def search(self, name: str, vector: list[float], limit: int = 8,
               file_filter=None, symbol_type=None, language=None):
        pts = self.points.get(name, [])
        ranked = sorted(pts, key=lambda p: p.score, reverse=True)
        return ranked[:limit]

    def upsert_points(self, name: str, points: list) -> int:
        return len(points)

    def purge_file_points(self, *args, **kwargs) -> int:
        return 0

    def count_points(self, name: str) -> int:
        return len(self.points.get(name, []))


def _point_for(file: str, symbol: str | None, symbol_type: str | None,
               lang: str, start: int, end: int, score: float,
               text: str):
    from types import SimpleNamespace
    payload = {"file": file, "symbol": symbol, "symbol_type": symbol_type,
               "lang": lang, "start_line": start, "end_line": end,
               "snippet": text[:200]}
    return SimpleNamespace(score=score, payload=payload)


@pytest.fixture()
def env(monkeypatch):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=tempfile.mkdtemp(prefix="ci-eval-"),
                 stale_ttl=60, embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=3,
                 watch_sweep_interval=300)
    project = tempfile.mkdtemp(prefix="ci-eval-proj-")
    for name, text in PROJECT_FILES.items():
        with open(os.path.join(project, name), "w") as f:
            f.write(text)
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(project, name="evalproj")
    core = Core(cfg)
    # seam replacement (AGENT.md: embedder/store stubbed at the Core seam,
    # no network): fake embedder + fake store with a fixed point set.
    core.embedder = FakeEmbedder()  # type: ignore[assignment]
    fake_store = _FakeStore()
    fake_store.points[f"idx_{entry.slug}"] = [
        _point_for("alpha.py", "refresh_token", "function", "python",
                   1, 2, 0.9, "def refresh_token(): return 1"),
        _point_for("alpha.py", "other", "function", "python",
                   4, 5, 0.8, "def other(): return 2"),
        _point_for("beta.py", "Store", "class", "python",
                   1, 3, 0.85, "class Store: def save(self): return 3"),
        _point_for("data.json", None, None, "json",
                   1, 1, 0.6, '{"key": "value"}'),
    ]
    core.store = fake_store  # type: ignore[assignment]
    # Never let the staleness pass run a real index against backends.
    monkeypatch.setattr(core, "maybe_refresh",
                        lambda *a, **kw: {"state": "skipped"})
    yield core, reg, entry, project, cfg, fake_store
    reg.close()
    shutil.rmtree(cfg.index_root, ignore_errors=True)
    shutil.rmtree(project, ignore_errors=True)


QUERIES = (
    '{"id": "q1", "query": "refresh the token", '
    '"relevant": [{"file": "alpha.py", "symbol": "refresh_token"}], '
    '"tags": ["conceptual"]}\n'
    '{"id": "q2", "query": "save the store", '
    '"relevant": [{"file": "beta.py", "symbol": "Store"}], "tags": ["x"]}\n'
    '{"id": "q3", "query": "nothing matches this", '
    '"relevant": [{"file": "missing.py"}], "tags": ["x"]}\n'
)


# ---------------------------------------------------------------------------
# query parsing
# ---------------------------------------------------------------------------

def test_load_queries_roundtrip(tmp_path):
    p = tmp_path / "queries.jsonl"
    p.write_text(QUERIES, encoding="utf-8")
    qs = evalharness.load_queries(p)
    assert [q.id for q in qs] == ["q1", "q2", "q3"]
    assert qs[0].relevant[0].symbol == "refresh_token"
    assert qs[2].tags == ["x"]


def test_load_queries_rejects_missing_fields(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text('{"id": "q1", "query": "x"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="missing field"):
        evalharness.load_queries(p)


def test_load_queries_rejects_empty_relevant(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text('{"id": "q1", "query": "x", "relevant": []}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="non-empty"):
        evalharness.load_queries(p)


def test_load_queries_rejects_bad_json(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bad JSON"):
        evalharness.load_queries(p)


# ---------------------------------------------------------------------------
# eval against the fake index (determinism)
# ---------------------------------------------------------------------------

def test_run_eval_is_deterministic_for_fixed_index(env):
    core, reg, entry, project, cfg, fake_store = env
    qpath = os.path.join(project, "queries.jsonl")
    with open(qpath, "w") as f:
        f.write(QUERIES)
    rep1 = evalharness.run_eval(core, project, qpath)
    rep2 = evalharness.run_eval(core, project, qpath)
    # ranks and metrics identical; latency lives in the summary only
    assert [r["rank"] for r in rep1["results"]] == \
           [r["rank"] for r in rep2["results"]]
    for key in ("recall@1", "recall@3", "mrr", "ndcg@10"):
        assert rep1["metrics"][key] == rep2["metrics"][key]


def test_run_eval_hit_counts_and_misses(env):
    core, reg, entry, project, cfg, fake_store = env
    qpath = os.path.join(project, "queries.jsonl")
    with open(qpath, "w") as f:
        f.write(QUERIES)
    rep = evalharness.run_eval(core, project, qpath)
    assert rep["queries"] == 3
    ranks = {r["id"]: r["rank"] for r in rep["results"]}
    assert ranks["q3"] is None            # irrelevant target -> miss
    assert ranks["q1"] is not None        # the real symbol must be found
    # recall@1 counts only rank-1 hits; the miss drags recall below 1.0
    assert rep["metrics"]["recall@1"] < 1.0
    assert rep["metrics"]["mrr"] > 0.0


def test_run_eval_unknown_mode_rejected(env):
    core, reg, entry, project, cfg, fake_store = env
    qpath = os.path.join(project, "queries.jsonl")
    with open(qpath, "w") as f:
        f.write(QUERIES)
    with pytest.raises(ValueError, match="unknown eval mode"):
        evalharness.run_eval(core, project, qpath, mode="semantic")


def test_run_eval_accepts_hybrid_alias(env):
    core, reg, entry, project, cfg, fake_store = env
    qpath = os.path.join(project, "queries.jsonl")
    with open(qpath, "w") as f:
        f.write(QUERIES)
    dense = evalharness.run_eval(core, project, qpath, mode="dense")
    hybrid = evalharness.run_eval(core, project, qpath, mode="hybrid")
    ranking_keys = ("recall@1", "recall@3", "recall@5", "recall@10",
                    "mrr", "ndcg@10")
    assert all(dense["metrics"][k] == hybrid["metrics"][k]
               for k in ranking_keys)  # alias until the lexical index lands
    assert hybrid["mode"] == "hybrid"


# ---------------------------------------------------------------------------
# report compare
# ---------------------------------------------------------------------------

def _report(ranks: dict[str, int | None]) -> dict:
    return {
        "metrics": {"recall@1": sum(r == 1 for r in ranks.values()) / len(ranks),
                    "mrr": 0.5},
        "results": [{"id": qid, "rank": r, "latency_s": 0.1,
                     "output_chars": 100, "approx_tokens": 25}
                    for qid, r in sorted(ranks.items())],
    }


def test_compare_reports_delta_and_win_loss():
    a = _report({"q1": 1, "q2": 3, "q3": None})
    b = _report({"q1": 1, "q2": 2, "q3": None})
    text = evalharness.compare_reports(a, b)
    assert "improved (1): q2" in text
    assert "regressed (0): -" in text
    assert "unchanged (2): q1, q3" in text


def test_compare_reports_regression():
    a = _report({"q1": 2})
    b = _report({"q1": 5})
    text = evalharness.compare_reports(a, b)
    assert "regressed (1): q1" in text


def test_compare_reports_zero_delta_for_identical():
    r = _report({"q1": 1, "q2": None})
    text = evalharness.compare_reports(r, r)
    assert "improved (0)" in text and "regressed (0)" in text
    assert "unchanged (2): q1, q2" in text


# ---------------------------------------------------------------------------
# CLI wiring (typer runner, offline core)
# ---------------------------------------------------------------------------

def test_cli_eval_writes_report(env, tmp_path, monkeypatch, capsys):
    from typer.testing import CliRunner
    from code_indexer.cli import app

    core, reg, entry, project, cfg, fake_store = env
    qpath = os.path.join(project, "queries.jsonl")
    with open(qpath, "w") as f:
        f.write(QUERIES)
    out = tmp_path / "report.json"
    monkeypatch.setattr("code_indexer.cli._get_core", lambda skip: core)
    result = CliRunner().invoke(
        app, ["eval", "--project", project, "--queries", qpath,
              "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.is_file()
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["metrics"]["mrr"] >= 0.0
    # report goes to --out; stdout keeps the summary + per-query lines
    assert "recall@1=" in result.output


def test_cli_eval_compare(env, tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from code_indexer.cli import app

    core, reg, entry, project, cfg, fake_store = env
    qpath = os.path.join(project, "queries.jsonl")
    with open(qpath, "w") as f:
        f.write(QUERIES)
    rep_a = tmp_path / "a.json"
    rep_b = tmp_path / "b.json"
    with open(rep_a, "w") as f:
        json.dump(_report({"q1": 1, "q2": None}), f)
    with open(rep_b, "w") as f:
        json.dump(_report({"q1": 1, "q2": 2}), f)
    monkeypatch.setattr("code_indexer.cli._get_core", lambda skip: core)
    result = CliRunner().invoke(
        app, ["eval-compare", str(rep_a), str(rep_b)])
    assert result.exit_code == 0, result.output
    assert "improved (1): q2" in result.output


def test_cli_eval_bad_queries_file_errors(env, tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from code_indexer.cli import app

    core, reg, entry, project, cfg, fake_store = env
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"id": "q1"}\n', encoding="utf-8")
    monkeypatch.setattr("code_indexer.cli._get_core", lambda skip: core)
    result = CliRunner().invoke(
        app, ["eval", "--project", project, "--queries", str(bad)])
    assert result.exit_code == 1
    assert "missing field" in result.output


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))