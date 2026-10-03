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

# Ranking modes accepted by ``eval``. ``hybrid`` here names the retrieval
# pipeline (dense + lexical); the lexical index landed, so the values map
 # to distinct pipelines now.
EVAL_MODES = ("dense", "lexical", "hybrid")

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


def _symbol_range(manifest: Any, rel_file: str,
                  symbol: str) -> tuple[int, int] | None:
    """(start_line, end_line) for a symbol row, or None when absent."""
    for r in manifest.find_symbols(symbol, substring=False):
        if r.file == rel_file:
            return (r.start_line, r.end_line)
    return None


def _hit_correct(core: "Core", entry: Any, hit: dict[str, Any],
                 rel: Relevant) -> bool:
    """File must match; when the label names a symbol, the hit must point at
    it (same symbol, or a line-range overlap with its manifest row)."""
    if hit.get("file") != rel.file:
        return False
    if rel.symbol is None:
        return True
    if hit.get("symbol") == rel.symbol:
        return True
    rng = _symbol_range(core.manifest_for(entry.slug), rel.file, rel.symbol)
    if rng is None:
        return False
    s, e = rng
    hs = hit.get("start_line")
    he = hit.get("end_line")
    if hs is None or he is None:
        return False
    return hs <= e and s <= he  # inclusive overlap


def run_eval(core: "Core", project: str, queries_path: str | Path,
             ks: list[int] = list(DEFAULT_KS), mode: str = "dense",
             limit: int = 10, skip_refresh: bool = True,
             rerank: str | None = None) -> dict[str, Any]:
    """Run every query through Core.search and aggregate the metrics.

    ``skip_refresh`` defaults to True: the eval measures ranking, not the
    staleness pass, and a refresh mid-run would make latency non-comparable.
    """
    if mode not in EVAL_MODES:
        raise ValueError(
            f"error: unknown eval mode {mode!r} (expected one of {EVAL_MODES})")
    queries = load_queries(queries_path)
    entry, err = core.resolve_entry(project)
    if entry is None:
        raise ValueError(err)

    ranking_mode = "vector"  # dense == the current pipeline
    results: list[QueryResult] = []
    for q in queries:
        t0 = time.perf_counter()
        hits = core.search(q.query, project=project, limit=max(limit, max(ks)),
                           ranking_mode=ranking_mode, skip_refresh=skip_refresh,
                           merge=False, rerank=rerank,
                           mode=mode)["hits"]
        latency = time.perf_counter() - t0
        rank: int | None = None
        for i, hit in enumerate(hits, 1):
            if rank is None and any(
                    _hit_correct(core, entry, hit, rel) for rel in q.relevant):
                rank = i
        results.append(QueryResult(
            id=q.id, rank=rank,
            correct_at={k: rank is not None and rank <= k for k in ks},
            latency_s=latency, hits=hits))

    n = len(results)
    per_k: dict[str, float] = {}
    for k in ks:
        recall = sum(1 for r in results if r.correct_at[k]) / n
        per_k[f"recall@{k}"] = round(recall, 4)
    mrr = round(mean(
        1.0 / r.rank if r.rank is not None else 0.0 for r in results), 4)

    def _ndcg(rank: int | None, k: int) -> float:
        if rank is None or rank > k:
            return 0.0
        return 1.0 / math.log2(rank + 1)

    ndcg10 = round(mean(_ndcg(r.rank, 10) for r in results), 4)
    latencies = sorted(r.latency_s for r in results)
    p95_idx = max(0, math.ceil(0.95 * len(latencies)) - 1)
    return {
        "project": entry.path,
        "mode": mode + (f"+rerank-{rerank}" if rerank else ""),
        "queries": n,
        "metrics": {
            **per_k,
            "mrr": mrr,
            "ndcg@10": ndcg10,
            "mean_latency_s": round(mean(latencies), 4),
            "p95_latency_s": round(latencies[p95_idx], 4),
        },
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


def format_eval_report(report: dict[str, Any]) -> str:
    """One-line summary + per-query table (deterministic field order)."""
    m = report["metrics"]
    lines = [
        f"eval project={report['project']} mode={report['mode']} "
        f"queries={report['queries']}",
        "  " + "  ".join(f"{k}={v}" for k, v in m.items()),
    ]
    for r in report["results"]:
        rank = r["rank"] if r["rank"] is not None else "miss"
        lines.append(
            f"  {r['id']}: rank={rank} latency={r['latency_s']}s "
            f"~{r['approx_tokens']}tok")
    return "\n".join(lines)


def compare_reports(a: dict[str, Any], b: dict[str, Any]) -> str:
    """Delta table (b vs a) + per-query win/loss list. Deterministic."""
    ma, mb = a["metrics"], b["metrics"]
    keys = list(dict.fromkeys(list(ma) + list(mb)))
    lines = ["metric                    a          b         delta",
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