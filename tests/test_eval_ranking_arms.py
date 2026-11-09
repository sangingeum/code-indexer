"""The eval harness can report the two merge TIMINGS side by side.

The repeat-coverage rule was measured where the merge was off; the shipped
answer merges same-file runs before the class key, so the rule is an identity
there. These tests pin the instrument that puts the two orderings on the SAME
retrieval: the ``ranked`` arm fixes the order on the unmerged hits and merges
only for display, the ``merge-timing`` pair reports it beside the shipped
``merged`` arm, and the display merge keeps the order it is handed.

Offline: the same fake embedder/store seam as tests/test_eval_merged_arm.py.
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
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer import evalharness, ranking  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

PROJECT_FILES = {
    # Two adjacent declarations of one file: the merge folds them.
    "gamma.py": "def gamma_a():\n    return 1\n\n\ndef gamma_b():\n"
                "    return 2\n",
    # Two chunks over the SAME lines: the repeat-coverage rule sees a repeat.
    "alpha.py": "def refresh_token():\n    return 1\n\n\ndef other():\n"
                "    return 2\n",
    "beta.py": "class Store:\n    def save(self):\n        return 3\n",
}

QUERIES = (
    '{"id": "q1", "query": "save the store", '
    '"relevant": [{"file": "beta.py", "symbol": "Store"}], "tags": ["x"]}\n'
    '{"id": "q2", "query": "refresh the token", '
    '"relevant": [{"file": "alpha.py", "symbol": "refresh_token"}], '
    '"tags": ["conceptual"]}\n'
    '{"id": "q3", "query": "nothing matches this", '
    '"relevant": [{"file": "missing.py"}], "tags": ["x"]}\n'
)


class FakeEmbedder:
    def __init__(self, dim: int = 16):
        self.dim = dim

    def dimension(self) -> int:
        return self.dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            digest = hashlib.sha256(t.encode("utf-8")).digest()
            raw = digest * (self.dim // len(digest) + 1)
            out.append([v / 255.0 for v in raw[:self.dim]])
        return out


class _FakeStore:
    def __init__(self) -> None:
        self.points: dict[str, list] = {}

    def collection_exists(self, name: str) -> bool:
        return True

    def create_collection(self, name: str, dim: int) -> None:
        pass

    def search(self, name: str, vector: list[float], limit: int = 8,
               file_filter=None, symbol_type=None, language=None):
        ranked = sorted(self.points.get(name, []),
                        key=lambda p: p.score, reverse=True)
        return ranked[:limit]

    def upsert_points(self, name: str, points: list) -> int:
        return len(points)

    def purge_file_points(self, *args, **kwargs) -> int:
        return 0

    def swap_collection(self, collection: str, alias: str) -> None:
        pass

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
                 index_root=tempfile.mkdtemp(prefix="ci-rankarm-"),
                 embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=3,
                 watch_sweep_interval=300)
    project = tempfile.mkdtemp(prefix="ci-rankarm-proj-")
    for name, text in PROJECT_FILES.items():
        with open(os.path.join(project, name), "w") as f:
            f.write(text)
    qpath = os.path.join(project, "queries.jsonl")
    with open(qpath, "w") as f:
        f.write(QUERIES)
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(project, name="rankarmproj")
    core = Core(cfg)
    core.embedder = FakeEmbedder()  # type: ignore[assignment]
    fake_store = _FakeStore()
    fake_store.points[f"idx_{entry.slug}"] = [
        _point_for("gamma.py", "gamma_a", "function", "python",
                   1, 2, 0.95, "def gamma_a(): return 1"),
        _point_for("gamma.py", "gamma_b", "function", "python",
                   5, 6, 0.94, "def gamma_b(): return 2"),
        _point_for("alpha.py", "refresh_token", "function", "python",
                   1, 2, 0.9, "def refresh_token(): return 1"),
        _point_for("beta.py", "Store", "class", "python",
                   1, 3, 0.85, "class Store: def save(self): return 3"),
        _point_for("alpha.py", "other", "function", "python",
                   4, 5, 0.8, "def other(): return 2"),
    ]
    core.store = fake_store  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    core.run_index(entry.slug, project)
    yield core, entry, project, qpath
    reg.close()
    shutil.rmtree(cfg.index_root, ignore_errors=True)
    shutil.rmtree(project, ignore_errors=True)


def _hit(score: float, file: str, start: int, end: int,
         symbol: str = "") -> dict:
    return {"project": "/p", "file": file, "score": score,
            "symbol": symbol, "start_line": start, "end_line": end}


# ---------------------------------------------------------------------------
# the display merge keeps the order it is handed
# ---------------------------------------------------------------------------

def test_the_displayed_order_is_the_ranked_order():
    """No re-sort: the union takes its best-ranked member's slot.

    ``merge_overlapping`` sorts by score; a list whose given order is NOT
    score order proves ``merge_for_display`` never touches it.
    """
    ranked = [_hit(0.5, "a.py", 1, 10), _hit(0.9, "b.py", 1, 10)]
    displayed = ranking.merge_for_display(ranked)
    assert [h["file"] for h in displayed] == ["a.py", "b.py"]
    # And the shipped merge of the same list really does re-sort, so the
    # assertion above is about this pass and not about the input order.
    assert [h["file"] for h in ranking.merge_overlapping(ranked)] == \
        ["b.py", "a.py"]


def test_a_repeated_span_yields_its_slot():
    """A demoted repeat contributes no slot to the displayed answer."""
    ranked = [_hit(0.9, "a.py", 1, 10, "first"),
              _hit(0.8, "b.py", 1, 10, "distinct"),
              # The repeat was demoted behind b.py's distinct span.
              _hit(0.85, "a.py", 5, 12, "repeat")]
    displayed = ranking.merge_for_display(ranked)
    assert [h["file"] for h in displayed] == ["a.py", "b.py"]
    assert displayed[0]["start_line"] == 1 and displayed[0]["end_line"] == 12
    assert displayed[0]["merged_count"] == 2


def test_the_merge_membership_is_the_shipped_merge_membership():
    """Same unions as ``merge_overlapping`` -- only the slots differ."""
    pool = [_hit(0.9, "a.py", 1, 10), _hit(0.88, "a.py", 12, 20),
            _hit(0.87, "a.py", 40, 50), _hit(0.86, "b.py", 1, 10)]
    shipped = {(h["file"], h["start_line"], h["end_line"])
               for h in ranking.merge_overlapping(pool)}
    displayed = {(h["file"], h["start_line"], h["end_line"])
                 for h in ranking.merge_for_display(pool)}
    assert displayed == shipped


# ---------------------------------------------------------------------------
# the harness: one retrieval, two timings
# ---------------------------------------------------------------------------

def test_the_merge_timing_pair_retrieves_each_query_once(env):
    """One embedding and one store search per query for both timings."""
    core, entry, project, qpath = env
    searches: list[int] = []
    embeds: list[int] = []
    real_search = core.store.search
    real_embed = core.embedder.embed

    def counting_search(*args, **kwargs):
        searches.append(1)
        return real_search(*args, **kwargs)

    def counting_embed(texts):
        embeds.append(len(texts))
        return real_embed(texts)

    core.store.search = counting_search  # type: ignore[method-assign]
    core.embedder.embed = counting_embed  # type: ignore[method-assign]
    report = evalharness.run_eval(core, project, qpath, arm="merge-timing")

    n_queries = len(evalharness.load_queries(qpath))
    assert report["queries"] == n_queries
    assert len(searches) == n_queries
    assert len(embeds) == n_queries
    assert report["arm"] == "merge-timing"
    assert set(report["arms"]) == {"merged", "ranked"}


def test_the_two_timings_deliver_the_same_regions(env):
    """The ranked arm is a display ORDER, not a different answer set.

    This is the shape the measurement reads: if the alternative changed which
    regions a reader gets (not just their order), it would be a different
    product surface and not a merge-timing question.
    """
    core, entry, project, qpath = env
    for q in evalharness.load_queries(qpath):
        shaped = core.search_both_shapings(
            q.query, project=project, limit=10, mode="dense",
            shapings=("merged", "ranked"))

        def regions(hits):
            return [(h["file"], h["start_line"], h["end_line"])
                    for h in hits]

        merged = regions(shaped.answers["merged"]["hits"])
        ranked = regions(shaped.answers["ranked"]["hits"])
        assert sorted(merged) == sorted(ranked), q.id
        assert len(merged) == len(ranked), q.id


def test_the_single_ranked_arm_names_itself(env):
    core, entry, project, qpath = env
    report = evalharness.run_eval(core, project, qpath, arm="ranked")
    assert report["arm"] == "ranked"
    assert "arms" not in report
    assert set(report["metrics"]) >= {"mrr", "ndcg@10", "recall@10"}


# ---------------------------------------------------------------------------
# the arm flag at the CLI boundary
# ---------------------------------------------------------------------------

def test_the_cli_help_names_the_merge_timings(env):
    result = CliRunner().invoke(app, ["eval", "--help"])
    assert result.exit_code == 0
    assert "ranked" in result.output
    assert "merge-timing" in result.output


def test_an_unknown_arm_is_refused_before_any_read(env, monkeypatch):
    core, entry, project, qpath = env

    def explode(skip):
        raise AssertionError("the core was built before the flag was refused")

    monkeypatch.setattr(cli, "_get_core", explode)
    result = CliRunner().invoke(
        app, ["eval", "--project", project, "--queries", qpath,
              "--arm", "ranked-elsewhere"])
    assert result.exit_code == 2
    assert "merge-timing" in result.output


def test_the_cli_reports_the_merge_timings_from_one_run(env, tmp_path,
                                                       monkeypatch):
    core, entry, project, qpath = env
    out = tmp_path / "report.json"
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = CliRunner().invoke(
        app, ["eval", "--project", project, "--queries", qpath,
              "--arm", "merge-timing", "--out", str(out)])
    assert result.exit_code == 0, result.output
    report = json.loads(out.read_text(encoding="utf-8"))
    assert set(report["arms"]) == {"merged", "ranked"}
    assert "arm=merged" in result.output
    assert "arm=ranked" in result.output
