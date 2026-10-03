"""Result shaping tests (token-budget work item).

Covers: overlapping-hit merge, generic per-file cap, budget trimming with the
one-hit-header slack, snippet truncation, RRF fusion across projects, and
per-format CLI snapshots (text / compact / json) with the default render
unchanged. All offline via stub seams.
"""

from __future__ import annotations

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.ranking import (cap_per_file, merge_overlapping,  # noqa: E402
                                  rrf_fuse, trim_to_budget,
                                  truncate_snippets)
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()


def _hit(project="p", file="a.py", start=1, end=5, score=0.9,
         symbol=None, snippet="line\n" * 20):
    return {"project": project, "file": file, "start_line": start,
            "end_line": end, "score": score, "symbol": symbol,
            "symbol_type": "function" if symbol else None, "lang": "python",
            "snippet": snippet}


# ---------------------------------------------------------------------------
# merge_overlapping
# ---------------------------------------------------------------------------

def test_merge_overlapping_same_file():
    hits = [_hit(start=1, end=10, score=0.9, symbol="one"),
            _hit(start=8, end=20, score=0.8, symbol="two")]
    out = merge_overlapping(hits)
    assert len(out) == 1
    assert out[0]["start_line"] == 1 and out[0]["end_line"] == 20
    assert out[0]["score"] == 0.9          # best score wins
    assert "two" in out[0]["symbol"]        # contributing symbols listed
    assert out[0]["merged_count"] == 2


def test_merge_adjacent_within_gap():
    hits = [_hit(start=1, end=10, score=0.9),
            _hit(start=12, end=20, score=0.8)]   # gap of 1 (<= adjacency 2)
    assert len(merge_overlapping(hits)) == 1


def test_no_merge_far_apart_or_cross_file():
    hits = [_hit(file="a.py", start=1, end=10),
            _hit(file="a.py", start=40, end=50),
            _hit(file="b.py", start=1, end=10)]
    assert len(merge_overlapping(hits)) == 3


# ---------------------------------------------------------------------------
# cap_per_file
# ---------------------------------------------------------------------------

def test_cap_per_file_diversifies():
    hits = [_hit(file="a.py", score=0.9 + i * 0.01) for i in range(4)] + \
           [_hit(file="b.py", score=0.5)]
    out = cap_per_file(hits, per_file=2)
    files = [h["file"] for h in out]
    assert files.count("a.py") == 2
    assert "b.py" in files           # diversification, not just truncation
    assert out[0]["file"] == "a.py"  # order preserved


def test_cap_per_file_disabled_at_zero():
    hits = [_hit(file="a.py") for _ in range(5)]
    assert len(cap_per_file(hits, per_file=0)) == 5


# ---------------------------------------------------------------------------
# budget trimming
# ---------------------------------------------------------------------------

def test_trim_to_budget_drops_lowest_ranked():
    hits = [_hit(score=0.9 - i * 0.01, snippet="x" * 300) for i in range(5)]
    kept, dropped = trim_to_budget(hits, max_chars=1000)
    assert dropped == 3
    assert len(kept) == 2
    assert kept[0]["score"] == 0.9  # highest-ranked survives


def test_trim_to_budget_never_exceeds_by_more_than_one_header():
    hits = [_hit(score=0.9, snippet="x" * 300), _hit(score=0.5, snippet="y" * 300)]
    # Budget exactly fits two hits (380 each) + one header of slack (80).
    kept, dropped = trim_to_budget(hits, max_chars=380 * 2 + 80)
    assert len(kept) == 2 and dropped == 0


def test_trim_to_budget_single_oversize_hit_kept():
    """One candidate that alone exceeds the budget: an empty result is worse
    than an over-budget single hit (documented slack)."""
    hits = [_hit(score=0.9, snippet="x" * 2000)]
    kept, dropped = trim_to_budget(hits, max_chars=500)
    assert kept and dropped == 0


def test_trim_to_budget_tokens():
    hits = [_hit(score=0.9, snippet="x" * 400) for _ in range(4)]
    kept, dropped = trim_to_budget(hits, max_tokens=100)
    assert len(kept) * (400 + 80) <= 400 + 80 + 100 * 4  # within slack


def test_truncate_snippets_caps_each():
    hits = [_hit(snippet="x" * 2000) for _ in range(3)]
    out = truncate_snippets(hits, None, 200)
    assert all(len(h["snippet"]) <= 200 * 4 // 3 - 80 + 1
               for h in out)


def test_no_budget_is_noop():
    hits = [_hit() for _ in range(3)]
    kept, dropped = trim_to_budget(hits)
    assert kept == hits and dropped == 0
    assert truncate_snippets(hits, None, None) == hits


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------

def test_rrf_fuses_and_compares_ranks_not_scores():
    # Project A: hit X at rank 1 with score 0.5; project B: hit Y rank 1
    # score 0.99 — rank fusion puts both rank-1 hits level; a hit appearing
    # in both lists wins over single-list hits.
    a = [_hit(project="A", file="shared.py", start=1, end=5, score=0.5),
         _hit(project="A", file="a_only.py", start=1, end=5, score=0.9)]
    b = [_hit(project="B", file="shared.py", start=1, end=5, score=0.99),
         _hit(project="B", file="b_only.py", start=1, end=5, score=0.4)]
    out = rrf_fuse({"A": a, "B": b}, limit=4)
    assert out[0]["file"] == "shared.py"       # appears in both lists
    assert "rrf_score" in out[0]
    assert out[0]["vector_score"] in (0.5, 0.99)  # original kept for display


def test_rrf_respects_limit():
    hits = [_hit(project=f"P{i}", file=f"f{i}.py") for i in range(6)]
    out = rrf_fuse({f"P{i}": [h] for i, h in enumerate(hits)}, limit=3)
    assert len(out) == 3


# ---------------------------------------------------------------------------
# CLI format snapshots (default unchanged; compact added)
# ---------------------------------------------------------------------------

@pytest.fixture()
def rig(tmp_path, monkeypatch):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text(
        "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n")
    core = Core(cfg)

    class StubEmbedder:
        def dimension(self):
            return 4

        def embed(self, texts):
            return [[0.1] * 4 for _ in texts]

    class StubStore:
        def collection_exists(self, name):
            return True

        def create_collection(self, name, dim):
            pass

        def upsert_points(self, name, points):
            pass

        def purge_file_points(self, *a, **kw):
            return 0

        def count_points(self, name):
            return 0

        def search(self, name, vector, limit=8, file_filter=None,
                   symbol_type=None, language=None):
            class _Hit:
                def __init__(self, payload, score):
                    self.payload, self.score = payload, score
            # Two overlapping hits in one file + one in another.
            return [
                _Hit({"file": "a.py", "symbol": "alpha", "symbol_type": "function",
                      "lang": "python", "start_line": 1, "end_line": 2,
                      "snippet": "def alpha():\n    return 1"}, 0.9),
                _Hit({"file": "a.py", "symbol": "beta", "symbol_type": "function",
                      "lang": "python", "start_line": 5, "end_line": 6,
                      "snippet": "def beta():\n    return 2"}, 0.85),
                _Hit({"file": "b.py", "symbol": None, "symbol_type": None,
                      "lang": "python", "start_line": 1, "end_line": 3,
                      "snippet": "plain module docstring"}, 0.6),
            ][:limit]

    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="shapeproj")
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    yield core, reg, entry
    reg.close()
    monkeypatch.undo()
    shutil.rmtree(cfg.index_root, ignore_errors=True)


def test_default_text_format_unchanged(rig):
    core, reg, entry = rig
    out = core.search_for_display("query", project=entry.path, fmt="text")
    assert out.startswith("search results (score desc):")
    assert "- [0.9000]" in out  # the historical render


def test_compact_format_snapshot(rig):
    core, reg, entry = rig
    out = core.search_for_display("query", project=entry.path, fmt="compact")
    lines = out.splitlines()
    assert all("::" in ln and ln.count("  ") >= 2 for ln in lines)
    # Overlapping a.py hits merged into one range 1-6.
    assert "a.py:1-6" in lines[0]
    assert lines[0].endswith("0.9000")


def test_json_format_snapshot(rig):
    core, reg, entry = rig
    out = core.search_for_display("query", project=entry.path, fmt="json")
    payload = json.loads(out)
    assert set(payload) == {"schema", "hits", "truncated", "dropped"}
    assert payload["schema"] == 1
    # Merged hit carries both symbols.
    top = payload["hits"][0]
    assert top["end_line"] == 6 and "beta" in top["symbol"]


def test_cli_compact_flag(rig):
    result = runner.invoke(app, ["semantic-search", "query", "--format",
                                 "compact"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0].count("  ") >= 2


def test_budget_trailing_line(rig):
    core, reg, entry = rig
    out = core.search_for_display("query", project=entry.path, fmt="compact",
                                  max_chars=200)
    lines = out.splitlines()
    assert "dropped by the output budget" in lines[-1]
    assert lines[-1].startswith("(")


def test_per_file_cap_through_core(rig):
    core, reg, entry = rig
    result = core.search("query", project=entry.path, per_file=1)
    files = [h["file"] for h in result["hits"]]
    assert files.count("a.py") == 1


def test_output_size_reduction_with_budget(rig):
    """The audited baseline: text --limit 8 ≈ 2.2 KB. A compact + budgeted
    call must come in well under it."""
    core, reg, entry = rig
    compact = core.search_for_display("query", project=entry.path,
                                      fmt="compact", max_tokens=200)
    assert len(compact) < 2200
    assert len(compact) < len(core.search_for_display(
        "query", project=entry.path, fmt="text"))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))