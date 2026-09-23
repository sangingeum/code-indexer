"""Query-time ranking for semantic_search: metadata adjustments + hybrid fusion.

The store returns candidates ranked purely by vector cosine similarity. This
module layers optional, opt-in re-ranking on top of that candidate pool:

- **metadata adjustments** — small additive deltas from payload facts already
  stored at index time (symbol presence, test/vendor path heuristics).
- **hybrid fusion** — reciprocal rank fusion (RRF) of the vector ranking with
  a lightweight lexical token-overlap score over symbol name, file path, and
  snippet text. Helps queries that mix natural language with exact
  identifiers, where embeddings alone under-rank the right chunk.

All functions are pure and unit-testable without Qdrant or Ollama.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Path segments that mark non-primary source locations. Chunks under these
# get a mild penalty unless the query itself targets tests.
TEST_PATH_MARKERS = frozenset({
    "test", "tests", "spec", "specs", "fixtures", "fixture",
    "mocks", "mock", "vendor", "testdata",
})

# Query tokens that signal the caller is deliberately looking at tests.
TEST_QUERY_MARKERS = frozenset({"test", "tests", "testing", "spec", "fixture"})

# Metadata adjustment magnitudes (additive on the cosine score).
DEFINITION_BOOST = 0.02
TEST_PATH_PENALTY = 0.05

# Weighted-sum fusion default weight (hybrid mode).
LEXICAL_WEIGHT = 0.25


def tokenize(text: str | None) -> list[str]:
    """Lowercase identifier-ish tokens (camelCase/snake_case preserved whole;
    callers that want sub-token splitting can layer it, but whole-token
    overlap is enough for lexical re-ranking)."""
    return [t.lower() for t in TOKEN_RE.findall(text or "")]


def query_tokens(query: str) -> list[str]:
    return tokenize(query)


def lexical_score(hit: dict[str, Any], qtokens: list[str]) -> float:
    """Lightweight lexical relevance for one candidate hit.

    Score = fraction of distinct query tokens present in the hit's
    (symbol, file, snippet) text, plus a bonus when a query token appears
    inside the symbol name itself. Range [0, 1.5].
    """
    if not qtokens:
        return 0.0
    haystack_parts = [
        hit.get("symbol") or "",
        hit.get("file") or "",
        hit.get("snippet") or "",
    ]
    tokens: list[str] = []
    for part in haystack_parts:
        tokens.extend(tokenize(part))
    present = sum(1 for t in set(qtokens) if t in tokens)
    score = present / len(set(qtokens))
    symbol = (hit.get("symbol") or "").lower()
    if symbol and any(t in symbol for t in qtokens):
        score += 0.5
    return score


def metadata_adjustment(hit: dict[str, Any], qtokens: list[str]) -> float:
    """Additive delta on the vector score from payload metadata.

    - Chunks carrying a symbol (definition-shaped) get a small boost over
      bare reference/comment chunks.
    - Chunks under test/fixture/vendor paths get a mild penalty unless the
      query itself targets tests.
    """
    delta = 0.0
    if hit.get("symbol"):
        delta += DEFINITION_BOOST
    file_lower = (hit.get("file") or "").lower()
    segments = set(re.split(r"[\\/._-]+", file_lower))
    if segments & TEST_PATH_MARKERS and not (set(qtokens) & TEST_QUERY_MARKERS):
        delta -= TEST_PATH_PENALTY
    return delta


def hybrid_fuse(vector_hits: list[dict[str, Any]], qtokens: list[str],
                lexical_weight: float = 0.25) -> list[dict[str, Any]]:
    """Weighted-sum fusion of the vector score with the lexical score.

    `vector_hits` must be in vector-rank order (best first). Each hit gains
    `vector_score` (its original cosine) and `lexical_score`; the fused
    score is vector_score + lexical_weight * lexical_score. Weighted-sum
    (rather than reciprocal-rank fusion) was chosen deliberately: with the
    tight cosine band this project's embeddings produce (typically < 0.2
    from top-1 to tail), rank-based fusion damps a one-rank lexical gain
    into irrelevance, while an additive bonus of a few hundredths can move
    an exact-identifier match past generic lookalikes without drowning the
    vector signal. Returns hits sorted by fused score descending; `score`
    is replaced by the fused value.
    """
    qtokens = list(dict.fromkeys(qtokens))
    fused: list[dict[str, Any]] = []
    for hit in vector_hits:
        ls = lexical_score(hit, qtokens)
        out = dict(hit)
        out["vector_score"] = hit["score"]
        out["lexical_score"] = round(ls, 4)
        out["score"] = round(hit["score"] + lexical_weight * ls, 4)
        fused.append(out)
    fused.sort(key=lambda h: h["score"], reverse=True)
    return fused


def metadata_rerank(vector_hits: list[dict[str, Any]],
                    qtokens: list[str]) -> list[dict[str, Any]]:
    """Adjust scores by metadata deltas and re-sort. Keeps `score` as the
    adjusted value and records the untouched cosine in `vector_score`."""
    out: list[dict[str, Any]] = []
    for hit in vector_hits:
        delta = metadata_adjustment(hit, qtokens)
        h = dict(hit)
        h["vector_score"] = hit["score"]
        h["metadata_delta"] = round(delta, 4)
        h["score"] = round(hit["score"] + delta, 4)
        out.append(h)
    out.sort(key=lambda h: h["score"], reverse=True)
    return out
