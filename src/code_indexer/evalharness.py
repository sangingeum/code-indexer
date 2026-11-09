"""Retrieval evaluation harness (improvement-plan work item: eval harness).

Measures ranking quality of ``Core.search`` against a labeled query set so
retrieval-quality changes (chunking, embedding text, fusion, reranking) can
only merge with a before/after metrics table.

Metrics per k in ``--k``: recall@k and MRR. Additionally nDCG@10, mean and
p95 per-query latency, and the approximate output size (chars/4 ≈ tokens).

A hit is correct when its project-relative file matches a relevant entry AND
(either that entry names no symbol, or the hit's symbol matches, or the hit's
line range overlaps the symbol's range in the manifest). Deterministic for a
fixed index: no timestamps in the per-query records, latency kept in a
separate summary block.

File format (``eval/queries.jsonl``, one JSON object per line)::

    {"id": "q1", "query": "where do we refresh auth tokens",
     "relevant": [{"file": "src/auth/session.py", "symbol": "refresh_token"}],
     "tags": ["conceptual"]}

``eval`` is a CLI-only dev utility, deliberately not exposed over MCP: it is
bench tooling for the maintainer, not an agent surface.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core import Core

from .core import SEARCH_MODES

# The modes ``eval`` accepts are the search modes, not a second list of the
# same words: the harness forwards its mode straight into ``Core.search``, so
# a value this module accepted and the search did not would be a refusal at
# the wrong layer. The names live in core and are imported from there.
EVAL_MODES = SEARCH_MODES

# The arms the harness reports. ``merged`` is what a shipped read answers
# (overlapping/adjacent same-file hits collapse into one union hit before the
# class key); ``unmerged`` is the same retrieval with that pass skipped and is
# the shaping the older tables were measured on; ``both`` reports the two side
# by side from one retrieval per query.
EVAL_ARMS = ("merged", "unmerged", "both")

DEFAULT_KS = (1, 3, 5, 10)


@dataclass
class Relevant:
    """One ground-truth target: a file, optionally a symbol in it."""

    file: str
    symbol: str | None = None


@dataclass
class EvalQuery:
    id: str
    query: str
    relevant: list[Relevant]
    tags: list[str] = field(default_factory=list)


@dataclass
class QueryResult:
    id: str
    rank: int | None          # 1-based best correct hit; None when missed
    correct_at: dict[int, bool]   # k -> any correct hit within k
    latency_s: float
    hits: list[dict[str, Any]]


def load_queries(path: str | Path) -> list[EvalQuery]:
    """Parse the queries.jsonl format; raise ValueError on bad rows."""
    queries: list[EvalQuery] = []
    for lineno, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"error: {path}:{lineno}: bad JSON: {exc}") from exc
        missing = [k for k in ("id", "query", "relevant") if k not in row]
        if missing:
            raise ValueError(
                f"error: {path}:{lineno}: missing field(s): {', '.join(missing)}")
        if not isinstance(row["relevant"], list) or not row["relevant"]:
            raise ValueError(
                f"error: {path}:{lineno}: 'relevant' must be a non-empty list")
        rels = [Relevant(file=r["file"],
                         symbol=r.get("symbol"))
                for r in row["relevant"]]
        queries.append(EvalQuery(id=row["id"], query=row["query"],
                                 relevant=rels, tags=row.get("tags", [])))
    if not queries:
        raise ValueError(f"error: {path}: no queries found")
    return queries


def _symbol_ranges(manifest: Any, rel_file: str,
                   symbol: str) -> list[tuple[int, int]]:
    """EVERY (start_line, end_line) row of one label in one file.

    A name can hold more than one row in the same file — a decorator
    double-row, an overload set, a redefinition, a wrapper — and the rows may
    not overlap at all. Scoring tests a hit against every one of them (any
    overlap wins), so no row is privileged over another and nothing is
    truncated.

    The name and the file are both filtered in SQL and the read is uncapped:
    a capped exact-name read filtered to a file afterwards silently returned
    nothing for every definition past the cap, mis-scoring the eval run that
    asked about it (issue #23, same shape as the get-code-context lookup).
    """
    rows = manifest.find_symbols(symbol, substring=False, file=rel_file,
                                 limit=None)
    return [(r.start_line, r.end_line) for r in rows]


def _hit_correct(core: "Core", entry: Any, hit: dict[str, Any],
                 rel: Relevant) -> bool:
    """File must match; when the label names a symbol, the hit must point at
    it (same symbol, or a line-range overlap with ANY manifest row of it)."""
    if hit.get("file") != rel.file:
        return False
    if rel.symbol is None:
        return True
    if hit.get("symbol") == rel.symbol:
        return True
    hs = hit.get("start_line")
    he = hit.get("end_line")
    if hs is None or he is None:
        return False
    ranges = _symbol_ranges(core.read_manifest(entry.slug), rel.file,
                            rel.symbol)
    return any(hs <= e and s <= he for s, e in ranges)  # inclusive overlap


def _query_result(core: "Core", entry: Any, q: EvalQuery,
                  hits: list[dict[str, Any]], latency_s: float,
                  ks: list[int]) -> QueryResult:
    """Score one answer: the 1-based rank of its best correct hit, or None."""
    rank: int | None = None
    for i, hit in enumerate(hits, 1):
        if rank is None and any(
                _hit_correct(core, entry, hit, rel) for rel in q.relevant):
            rank = i
    return QueryResult(id=q.id, rank=rank,
                       correct_at={k: rank is not None and rank <= k
                                   for k in ks},
                       latency_s=latency_s, hits=hits)


def _ndcg(rank: int | None, k: int) -> float:
    """One query's nDCG contribution: a hit within k scores by its rank."""
    if rank is None or rank > k:
        return 0.0
    return 1.0 / math.log2(rank + 1)


def _metrics(results: list[QueryResult], ks: list[int]) -> dict[str, float]:
    """recall@k, MRR, nDCG@10 and the latency summary for one arm."""
    per_k: dict[str, float] = {}
    for k in ks:
        recall = sum(1 for r in results if r.correct_at[k]) / len(results)
        per_k[f"recall@{k}"] = round(recall, 4)
    mrr = round(mean(
        1.0 / r.rank if r.rank is not None else 0.0 for r in results), 4)
    ndcg10 = round(mean(_ndcg(r.rank, 10) for r in results), 4)
    latencies = sorted(r.latency_s for r in results)
    p95_idx = max(0, math.ceil(0.95 * len(latencies)) - 1)
    return {
        **per_k,
        "mrr": mrr,
        "ndcg@10": ndcg10,
        "mean_latency_s": round(mean(latencies), 4),
        "p95_latency_s": round(latencies[p95_idx], 4),
    }


def _arm_report(results: list[QueryResult],
                ks: list[int]) -> dict[str, Any]:
    """One arm's metrics and per-query rows, in the report's field order."""
    return {
        "metrics": _metrics(results, ks),
        "results": [
            {
                "id": r.id,
                "rank": r.rank,
                "latency_s": round(r.latency_s, 4),
                "output_chars": len(json.dumps(r.hits, ensure_ascii=False)),
                "approx_tokens": len(json.dumps(r.hits, ensure_ascii=False)) // 4,
            } for r in results
        ],
    }


def run_eval(core: "Core", project: str, queries_path: str | Path,
             ks: list[int] = list(DEFAULT_KS), mode: str = "dense",
             limit: int = 10, skip_refresh: bool = True,
             rerank: str | None = None,
             arm: str = "unmerged") -> dict[str, Any]:
    """Run every query through the search pipeline and aggregate the metrics.

    ``arm`` selects the shaping the numbers describe (``EVAL_ARMS``):
    ``unmerged`` skips the merge of overlapping/adjacent same-file hits and is
    the shaping the older tables were measured on; ``merged`` is the shipped
    answer; ``both`` reports the two side by side from ONE retrieval per
    query -- the query is embedded and the store searched once, and only the
    shaping pass repeats, so a two-arm run costs one retrieval plus a second
    shaping pass, not two searches.

    ``skip_refresh`` defaults to True: the eval measures ranking, not the
    staleness pass, and a refresh mid-run would make latency non-comparable.
    """
    if mode not in EVAL_MODES:
        raise ValueError(
            f"error: unknown eval mode {mode!r} (expected one of {EVAL_MODES})")
    if arm not in EVAL_ARMS:
        raise ValueError(
            f"error: unknown eval arm {arm!r} (expected one of {EVAL_ARMS})")
    queries = load_queries(queries_path)
    entry, err = core.resolve_entry(project)
    if entry is None:
        raise ValueError(err)

    ranking_mode = "vector"  # dense == the current pipeline
    arms = ("merged", "unmerged") if arm == "both" else (arm,)
    per_arm: dict[str, list[QueryResult]] = {name: [] for name in arms}
    for q in queries:
        if arm == "both":
            shaped = core.search_both_shapings(
                q.query, project=project, limit=max(limit, max(ks)),
                ranking_mode=ranking_mode, skip_refresh=skip_refresh,
                rerank=rerank, mode=mode)
            for name in arms:
                # The retrieval is shared; each answer owns its shaping time.
                latency = shaped.retrieval_s + shaped.shaping_s[name]
                per_arm[name].append(_query_result(
                    core, entry, q, shaped.answers[name]["hits"], latency, ks))
            continue
        t0 = time.perf_counter()
        hits = core.search(q.query, project=project, limit=max(limit, max(ks)),
                           ranking_mode=ranking_mode, skip_refresh=skip_refresh,
                           merge=arm == "merged", rerank=rerank,
                           mode=mode)["hits"]
        latency = time.perf_counter() - t0
        per_arm[arm].append(_query_result(core, entry, q, hits, latency, ks))

    report: dict[str, Any] = {
        "project": entry.path,
        "mode": mode + (f"+rerank-{rerank}" if rerank else ""),
        "queries": len(queries),
        "arm": arm,
    }
    if arm == "both":
        report["arms"] = {name: _arm_report(per_arm[name], ks) for name in arms}
        return report
    report.update(_arm_report(per_arm[arm], ks))
    return report


def format_eval_report(report: dict[str, Any]) -> str:
    """One-line summary + per-query table (deterministic field order).

    An arm names itself on its own summary line, so a number can never be
    read as coming from the other path. A two-arm report prints one such
    block per arm.
    """
    if "arms" in report:
        return "\n".join(
            _format_one_arm(report, name, block)
            for name, block in report["arms"].items())
    return _format_one_arm(report, report.get("arm"), report)


def _format_one_arm(report: dict[str, Any], arm: str | None,
                    block: dict[str, Any]) -> str:
    m = block["metrics"]
    lines = [
        f"eval project={report['project']} mode={report['mode']} "
        f"queries={report['queries']} arm={arm}",
        "  " + "  ".join(f"{k}={v}" for k, v in m.items()),
    ]
    for r in block["results"]:
        rank = r["rank"] if r["rank"] is not None else "miss"
        lines.append(
            f"  {r['id']}: rank={rank} latency={r['latency_s']}s "
            f"~{r['approx_tokens']}tok")
    return "\n".join(lines)


def compare_reports(a: dict[str, Any], b: dict[str, Any]) -> str:
    """Delta table (b vs a) + per-query win/loss list. Deterministic.

    The header names each side's arm, so a delta can never be published
    without the shaping it measured. Two documents that name DIFFERENT arms
    are refused: a merged report's numbers and an unmerged one's differ by a
    whole pass, so a delta across them would read as a rule change rather
    than a measurement. The two-arm report already carries both arms from
    ONE retrieval, so the comparison across shapings lives inside it; a
    comparison across runs takes one report per arm. A report written before
    the ``arm`` field existed is labelled ``arm unrecorded`` rather than
    silently assumed to be either arm.
    """
    if "arms" in a or "arms" in b:
        raise ValueError(
            "error: eval-compare reads single-arm reports; a two-arm report "
            "carries both arms already -- write one report per arm "
            "(--arm merged / --arm unmerged) to compare across runs")
    arm_a, arm_b = a.get("arm"), b.get("arm")
    if arm_a is not None and arm_b is not None and arm_a != arm_b:
        raise ValueError(
            f"error: eval-compare compares one arm at a time: a is {arm_a!r} "
            f"and b is {arm_b!r}; a delta across arms reads as a rule change. "
            f"Compare two reports of the same arm (--arm), or read both "
            f"arms from one --arm both report")
    ma, mb = a["metrics"], b["metrics"]
    keys = list(dict.fromkeys(list(ma) + list(mb)))
    # The metric table keeps its original geometry byte for byte; the arm
    # line above it is what a reader now cannot miss.
    lines = [f"a ({arm_a or 'arm unrecorded'}) vs b ({arm_b or 'arm unrecorded'})",
             "metric                    a          b         delta",
             "----------------------------------------------------"]
    for k in keys:
        va, vb = ma.get(k, 0.0), mb.get(k, 0.0)
        lines.append(f"{k:<24} {va:>9.4f} {vb:>9.4f} {vb - va:>+10.4f}")
    ra = {r["id"]: r["rank"] for r in a["results"]}
    rb = {r["id"]: r["rank"] for r in b["results"]}
    wins, losses, ties = [], [], []
    for qid in sorted(ra):
        ra_r, rb_r = ra[qid], rb.get(qid)
        ra_miss = ra_r is None
        rb_miss = rb_r is None
        if ra_miss and rb_miss:
            ties.append(qid)
        elif rb_miss:
            losses.append(qid)
        elif ra_miss:
            wins.append(qid)
        elif rb_r < ra_r:
            wins.append(qid)
        elif rb_r > ra_r:
            losses.append(qid)
        else:
            ties.append(qid)
    lines.append("")
    lines.append(f"improved ({len(wins)}): " + (", ".join(wins) or "-"))
    lines.append(f"regressed ({len(losses)}): " + (", ".join(losses) or "-"))
    lines.append(f"unchanged ({len(ties)}): " + (", ".join(ties) or "-"))
    return "\n".join(lines)


def load_report(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))