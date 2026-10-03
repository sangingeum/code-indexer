"""Pluggable reranker interface (retrieval-quality work item).

A Reranker re-orders the fused candidate pool AFTER retrieval: query-time
metadata adjustments, lexical fusion, and the data-file mitigation produce a
ranked pool; the reranker applies a final scoring pass over the top
``RERANK_POOL`` (default 30) candidates and re-sorts. The interface is
deliberately tiny — ``rerank(hits, query) -> hits`` — so a future
cross-encoder / model reranker behind RERANK_MODEL is a small change (check
availability in doctor, wrap the same interface, keep determinism).

The default HeuristicReranker builds on the existing ranking module (no
extra model): symbol-name and path/filename token overlap with the query,
test/vendor/generated path penalties, and a mild penalty for files already
represented >= 2 times in the window (generic per-file diversification —
distinct from CI-13's output-side --per-file cap, which trims AFTER ranking).

Deterministic for fixed input: no randomness, stable tie-breaking by
(file, start_line). Applied only when enabled (eval-gated default-off;
--rerank/--no-rerank selects explicitly).
"""

from __future__ import annotations

from typing import Any, Protocol

from . import ranking

# Candidate pool the reranker sees (over-fetch already covers this).
RERANK_POOL = 30

# Magnitudes (additive on the cosine/vector score, same scale as
# ranking.py's metadata adjustments).
SYMBOL_TOKEN_BOOST = 0.04     # query token appears in the symbol name
PATH_TOKEN_BOOST = 0.02       # query token appears in the path/filename
REPEAT_FILE_PENALTY = 0.03    # third+ hit from the same file in the window
FUZZY_SYMBOL_BOOST = 0.02     # sub-token (camelCase/snake_case) overlap


class Reranker(Protocol):
    """Minimal reranker contract: re-score and re-sort the candidate pool."""

    def rerank(self, hits: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
        ...


def _subtokens(token: str) -> set[str]:
    """Split an identifier into sub-tokens (camelCase/snake_case parts)."""
    import re
    parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|[0-9]+", token)
    return {p.lower() for p in parts if len(p) >= 3}


class HeuristicReranker:
    """Token-overlap reranker over the existing metadata pipeline."""

    def rerank(self, hits: list[dict[str, Any]],
               query: str) -> list[dict[str, Any]]:
        qtokens = ranking.query_tokens(query)
        qsubtokens: set[str] = set()
        for t in qtokens:
            qsubtokens |= _subtokens(t)
        if not qtokens:
            return hits

        file_counts: dict[str, int] = {}
        out: list[dict[str, Any]] = []
        for hit in hits:
            h = dict(hit)
            h.setdefault("vector_score", h["score"])
            boost = 0.0
            symbol = (h.get("symbol") or "")
            sym_tokens = {t.lower() for t in ranking.tokenize(symbol)}
            if sym_tokens & {t.lower() for t in qtokens}:
                boost += SYMBOL_TOKEN_BOOST
            elif sym_tokens and _subtokens(symbol) & qsubtokens:
                boost += FUZZY_SYMBOL_BOOST
            path = (h.get("file") or "").lower()
            path_tokens = {t.lower() for t in ranking.tokenize(path)}
            if path_tokens & {t.lower() for t in qtokens}:
                boost += PATH_TOKEN_BOOST
            # Generic per-file diversification: mild penalty from the third
            # hit of the same file onward (window is score-ordered input).
            file_key = str(h.get("file"))
            n = file_counts.get(file_key, 0)
            file_counts[file_key] = n + 1
            if n >= 2:
                boost -= REPEAT_FILE_PENALTY
            h["rerank_delta"] = round(boost, 4)
            h["score"] = round(h["score"] + boost, 4)
            out.append(h)
        out.sort(key=lambda h: (-h["score"], h.get("file") or "",
                                h.get("start_line") or 0))
        return out


class NoReranker:
    """Explicit no-op (--no-rerank / ranking_mode=vector)."""

    def rerank(self, hits: list[dict[str, Any]],
               query: str) -> list[dict[str, Any]]:
        return hits


def select_reranker(mode: str) -> Reranker:
    """Resolve the reranker from the mode string (CLI/MCP seam).

    'heuristic' -> HeuristicReranker; 'none' -> NoReranker; anything else
    raises ValueError (adapters surface it).
    """
    if mode == "heuristic":
        return HeuristicReranker()
    if mode == "none":
        return NoReranker()
    raise ValueError(f"error: unknown rerank mode: {mode}")