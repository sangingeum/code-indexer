"""The eval harness reports the SHIPPED shaping beside the unmerged one.

The harness measured one configuration (merging off) for the whole of the
retrieval rounds while a shipped read merges overlapping/adjacent same-file
hits into one union hit before the class key, so the quality ruler and the
product answer differed by a whole pass. These tests pin the merged arm to
the shipped configuration, pin the two arms to ONE retrieval per query, and
pin the arm flag's boundary behaviour.

All offline: the same fake embedder/store seam as tests/test_evalharness.py,
with a fixed point set whose adjacent same-file hits make the two shapings
measure differently.
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
from code_indexer import evalharness  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

PROJECT_FILES = {
    # Two adjacent declarations: the merge folds them into one union hit.
    "alpha.py": "def refresh_token():\n    return 1\n\n\ndef other():\n"
                "    return 2\n",
    # Two adjacent declarations ranked above everything else: merging them
    # frees one slot, which is where the two arms part company.
    "gamma.py": "def gamma_a():\n    return 1\n\n\ndef gamma_b():\n"
                "    return 2\n",
    "beta.py": "class Store:\n    def save(self):\n        return 3\n",
    "data.json": "{\"key\": \"value\"}\n",
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
            out.append([v / 255.0 for v in raw[:self.dim]])
        return out


class _FakeStore:
    """Search-side store stub: serves pre-baked points, ranked by score."""

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
                 index_root=tempfile.mkdtemp(prefix="ci-arm-"),
                 embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=3,
                 watch_sweep_interval=300)
    project = tempfile.mkdtemp(prefix="ci-arm-proj-")
    for name, text in PROJECT_FILES.items():
        with open(os.path.join(project, name), "w") as f:
            f.write(text)
    qpath = os.path.join(project, "queries.jsonl")
    with open(qpath, "w") as f:
        f.write(QUERIES)
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(project, name="armproj")
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
        _point_for("data.json", None, None, "json",
                   1, 1, 0.6, '{"key": "value"}'),
    ]
    core.store = fake_store  # type: ignore[assignment]
    # Index once so the manifest holds the symbol rows the harness scores a
    # merged union hit against (the label is matched by range overlap when the
    # union's own symbol field lists several names). The store stub keeps the
    # canned points above, so only the manifest comes from this pass.
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    core.run_index(entry.slug, project)
    yield core, entry, project, qpath
    reg.close()
    shutil.rmtree(cfg.index_root, ignore_errors=True)
    shutil.rmtree(project, ignore_errors=True)


def _rank(core, entry, hits: list[dict], relevant) -> int | None:
    """The 1-based rank of the first hit the harness scores as correct.

    It uses the harness's own rule on purpose: the comparison is between the
    arm's ranks and another answer's ranks, so both sides must be scored the
    same way -- a merged union hit carries its members' symbols joined, and
    only the harness's range-overlap rule recognises the label there.
    """
    for i, hit in enumerate(hits, 1):
        if any(evalharness._hit_correct(core, entry, hit, rel)
               for rel in relevant):
            return i
    return None


# ---------------------------------------------------------------------------
# the merged arm IS the shipped configuration
# ---------------------------------------------------------------------------

def test_the_merged_arm_reports_the_shipped_merge(env):
    """The merged arm equals `Core.search` with merge on -- the shipped read.

    Ranks are compared per query against the call `semantic-search` makes, so
    a third, unshipped configuration (say, merging after the class key) would
    fail here even if it looked plausible.
    """
    core, entry, project, qpath = env
    queries = evalharness.load_queries(qpath)
    shipped = {
        q.id: _rank(core, entry, core.search(q.query, project=project, limit=10,
                                ranking_mode="vector", merge=True,
                                mode="dense")["hits"], q.relevant)
        for q in queries
    }
    report = evalharness.run_eval(core, project, qpath, arm="merged")
    assert {r["id"]: r["rank"] for r in report["results"]} == shipped
    assert report["arm"] == "merged"


def test_the_two_arms_do_not_measure_one_configuration(env):
    """The arms differ on a pool of adjacent hits, so both are real."""
    core, entry, project, qpath = env
    report = evalharness.run_eval(core, project, qpath, arm="both")
    merged = {r["id"]: r["rank"] for r in report["arms"]["merged"]["results"]}
    unmerged = {r["id"]: r["rank"]
                for r in report["arms"]["unmerged"]["results"]}
    # Merging gamma_a+gamma_b frees a slot, so beta.py's labeled hit climbs.
    assert unmerged["q1"] == 4 and merged["q1"] == 3
    assert unmerged["q2"] == 3 and merged["q2"] == 2
    assert report["arms"]["merged"]["metrics"] != \
           report["arms"]["unmerged"]["metrics"]


# ---------------------------------------------------------------------------
# one retrieval, two shapings
# ---------------------------------------------------------------------------

def test_a_two_arm_run_retrieves_each_query_once(env):
    """The expensive work is shared: one embedding and one store search."""
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
    report = evalharness.run_eval(core, project, qpath, arm="both")

    n_queries = len(evalharness.load_queries(qpath))
    assert report["queries"] == n_queries
    assert len(searches) == n_queries
    assert len(embeds) == n_queries


# ---------------------------------------------------------------------------
# the arm flag at the CLI boundary
# ---------------------------------------------------------------------------

def test_the_cli_reports_both_arms_from_one_run(env, tmp_path, monkeypatch):
    """One report holds both arms, each naming itself."""
    core, entry, project, qpath = env
    out = tmp_path / "report.json"
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = CliRunner().invoke(
        app, ["eval", "--project", project, "--queries", qpath,
              "--arm", "both", "--out", str(out)])
    assert result.exit_code == 0, result.output
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["arm"] == "both"
    assert set(report["arms"]) == {"merged", "unmerged"}
    assert "arm=merged" in result.output
    assert "arm=unmerged" in result.output


def test_a_bad_arm_value_is_an_argument_error_before_any_read(
        env, monkeypatch):
    """A value outside the enumeration is exit 2, refused at the boundary.

    The core factory is made to explode, so this fails if the refusal moved
    past the read gates -- and the message must name the accepted arms.
    """
    core, entry, project, qpath = env

    def explode(skip):
        raise AssertionError("the core was built before the flag was refused")

    monkeypatch.setattr(cli, "_get_core", explode)
    result = CliRunner().invoke(
        app, ["eval", "--project", project, "--queries", qpath,
              "--arm", "sideways"])
    assert result.exit_code == 2
    assert "--arm" in result.output
    assert "merged" in result.output and "unmerged" in result.output


# ---------------------------------------------------------------------------
# the default arm is the one the older tables were measured on
# ---------------------------------------------------------------------------

def test_the_default_arm_still_reports_the_unmerged_numbers(env):
    """Passes at the pre-fix revision too: naming no arm runs the shaping the
    harness already ran, and its numbers still equal a merge-off search."""
    core, entry, project, qpath = env
    queries = evalharness.load_queries(qpath)
    unmerged = {
        q.id: _rank(core, entry, core.search(q.query, project=project, limit=10,
                                ranking_mode="vector", merge=False,
                                mode="dense")["hits"], q.relevant)
        for q in queries
    }
    report = evalharness.run_eval(core, project, qpath)
    assert {r["id"]: r["rank"] for r in report["results"]} == unmerged
    assert {"project", "mode", "queries", "metrics", "results"} <= set(report)
