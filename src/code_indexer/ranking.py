"""Query-time ranking for semantic_search: metadata adjustments + hybrid fusion.

The store returns candidates ranked purely by vector cosine similarity. This
module layers optional, opt-in re-ranking on top of that candidate pool:

- **metadata adjustments** — small additive deltas from payload facts already
  stored at index time (symbol presence, test/vendor path heuristics).
- **hybrid fusion** — weighted-sum fusion of the vector ranking with a
  lightweight lexical token-overlap score over symbol name, file path, and
  snippet text (`fused = vector_score + 0.25 * lexical`). Helps queries
  that mix natural language with exact identifiers, where embeddings
  alone under-rank the right chunk.
- **data-file mitigation** — always applied (all modes, including the
  default vector mode): pure-data payload chunks (json/yaml/toml) get a
  small down-weight and their share of the top-k window is capped, because
  their self-descriptive keys can crowd out the code that produces the data
  for analysis-shaped queries. Waived when the query names a data format.

All functions are pure and unit-testable without Qdrant or Ollama.
"""

from __future__ import annotations

import re
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

# Pure-data payload files carry no code. Their self-descriptive keys match
# analysis-shaped query vocabulary better than the code that produces the data,
# so they can crowd every top-k slot for queries like "summarize the metrics
# recorded in the latest run". They stay searchable — only down-weighted and
# share-capped in the top-k window, never excluded.
DATA_FILE_LANGS = frozenset({"json", "yaml", "toml"})
DATA_FILE_EXTENSIONS = frozenset({".json", ".yaml", ".yml", ".toml"})
DATA_FILE_PENALTY = 0.03
# Fraction of the top-k window pure-data chunks may occupy (floor; at least 1).
DATA_FILE_TOP_K_SHARE = 0.4
# Query tokens that mean the caller is deliberately looking at a data file, in
# which case the data-file mitigation is waived entirely.
DATA_QUERY_MARKERS = frozenset({"json", "yaml", "yml", "toml"})


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
        out["score"] = round(hit["score"] + lexical_weight * round(ls, 4), 4)
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


# -- data-file dominance mitigation (all ranking modes) ---------------------


def is_data_payload(hit: dict[str, Any]) -> bool:
    """True for a pure-data payload chunk (no code): json/yaml/toml.

    Uses the stored `lang` when present (the index-time language payload,
    set from the file extension) and falls back to the file extension.
    """
    lang = (hit.get("lang") or "").lower()
    if lang in DATA_FILE_LANGS:
        return True
    file = (hit.get("file") or "").lower()
    return any(file.endswith(ext) for ext in DATA_FILE_EXTENSIONS)


def _targets_data(qtokens: list[str] | None) -> bool:
    """The caller named a data format — the mitigation is waived."""
    if not qtokens:
        return False
    return bool({t.lower() for t in qtokens} & DATA_QUERY_MARKERS)


def downweight_data_files(hits: list[dict[str, Any]],
                          qtokens: list[str] | None = None) -> list[dict[str, Any]]:
    """Subtract a small delta from pure-data chunks and re-sort.

    Data files stay in the result set (still searchable, never excluded);
    their scores only move, so code chunks within the delta overtake them.
    Records `data_penalty` per hit and keeps the pre-adjustment score in
    `vector_score` when the caller has not already set it. Waived when the
    query explicitly targets a data format.
    """
    if _targets_data(qtokens):
        return hits
    out: list[dict[str, Any]] = []
    for hit in hits:
        h = dict(hit)
        h.setdefault("vector_score", hit["score"])
        if is_data_payload(hit):
            h["data_penalty"] = DATA_FILE_PENALTY
            h["score"] = round(hit["score"] - DATA_FILE_PENALTY, 4)
        else:
            h["data_penalty"] = 0.0
        out.append(h)
    out.sort(key=lambda h: h["score"], reverse=True)
    return out


def cap_data_file_share(hits: list[dict[str, Any]], limit: int,
                        qtokens: list[str] | None = None,
                        max_share: float = DATA_FILE_TOP_K_SHARE
                        ) -> list[dict[str, Any]]:
    """Bound how many pure-data chunks may occupy the top-`limit` window.

    `hits` must be score-ordered (best first). The highest-scoring data-file
    chunks up to the cap stay in place; any further data-file chunks move
    behind the non-data hits, so a code chunk is always preferred inside the
    window when one is competitive. No hit is dropped and data files remain
    searchable — they are only prevented from dominating the window. Waived
    when the query targets a data format, or when the window is too small to
    cap meaningfully.
    """
    if limit <= 0 or _targets_data(qtokens):
        return hits
    max_data = max(1, int(limit * max_share))
    head: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    seen_data = 0
    for hit in hits:
        if is_data_payload(hit):
            if seen_data < max_data:
                head.append(hit)
                seen_data += 1
            else:
                overflow.append(hit)
        else:
            head.append(hit)
    return head + overflow


# ---------------------------------------------------------------------------
# result shaping (token-budget work item): merge, per-file caps, budgets, RRF
# ---------------------------------------------------------------------------

def merge_overlapping(hits: list[dict[str, Any]],
                      adjacency_gap: int = 2
                      ) -> list[dict[str, Any]]:
    """Merge overlapping/adjacent hits from the same file into one.

    Ranges that overlap or sit within ``adjacency_gap`` lines of each other
    collapse into their union; the best score wins, and the merged hit lists
    all contributing symbols. Deterministic: same-file groups are merged in
    score order, output re-sorted by score.
    """
    by_file: dict[tuple[str, str], list[dict[str, Any]]] = {}
    others: list[dict[str, Any]] = []
    for hit in hits:
        key = (hit.get("project") or "", hit.get("file") or "")
        if hit.get("start_line") is None or hit.get("end_line") is None:
            others.append(hit)
            continue
        by_file.setdefault(key, []).append(hit)
    merged: list[dict[str, Any]] = []
    for key in by_file:
        group = sorted(by_file[key],
                       key=lambda h: (-h["score"], h["start_line"]))
        current: dict[str, Any] | None = None
        for hit in group:
            if current is None:
                current = dict(hit)
                continue
            if hit["start_line"] <= current["end_line"] + adjacency_gap + 1:
                current["end_line"] = max(current["end_line"],
                                          hit["end_line"])
                current["start_line"] = min(current["start_line"],
                                            hit["start_line"])
                for field in ("symbol", "symbol_type"):
                    extra = hit.get(field)
                    if extra and extra != current.get(field):
                        symbols = [current.get(field), extra]
                        current[field] = ", ".join(s for s in symbols if s)
                current["merged_count"] = current.get("merged_count", 1) + 1
            else:
                merged.append(current)
                current = dict(hit)
        if current is not None:
            merged.append(current)
    out = merged + others
    out.sort(key=lambda h: h["score"], reverse=True)
    return out


def cap_per_file(hits: list[dict[str, Any]], per_file: int) -> list[dict[str, Any]]:
    """Keep at most ``per_file`` hits per file (score-ordered input).

    Diversifies the result across files; the data-file share cap above stays
    independent of this generic cap. ``per_file <= 0`` disables the cap.
    """
    if per_file <= 0:
        return hits
    seen: dict[tuple[str, str], int] = {}
    kept: list[dict[str, Any]] = []
    for hit in hits:
        key = (hit.get("project") or "", hit.get("file") or "")
        n = seen.get(key, 0)
        if n < per_file:
            seen[key] = n + 1
            kept.append(hit)
        # Overflowing same-file hits are dropped outright: the caller
        # over-fetched, so nothing competitive is lost that another file's
        # hit should not replace.
    return kept


def trim_to_budget(hits: list[dict[str, Any]],
                   max_chars: int | None = None,
                   max_tokens: int | None = None,
                   header_overhead: int = 80) -> tuple[list[dict[str, Any]], int]:
    """Trim lowest-ranked hits until the output fits the budget.

    A hit costs its snippet (capped by ``max_chars`` inside format layer) plus
    ``header_overhead`` chars of location/symbol header. ``max_tokens`` is
    converted at 4 chars/token. Returns ``(kept_hits, dropped_count)``. The
    budget is never exceeded by more than one hit's header (the last hit is
    admitted if ANY budget remains, and a hit that alone exceeds the budget
    is still kept when it is the only candidate — an empty result is worse).
    """
    if max_chars is None and max_tokens is None:
        return hits, 0
    budget: int = max_chars if max_chars is not None else (max_tokens or 0) * 4
    kept: list[dict[str, Any]] = []
    used = 0
    dropped = 0
    for hit in hits:
        cost = header_overhead + min(len(hit.get("snippet") or ""), 500)
        if used + cost > budget and kept:
            dropped += 1
            continue
        kept.append(hit)
        used += cost
    return kept, dropped


def truncate_snippets(hits: list[dict[str, Any]], max_chars: int | None,
                      max_tokens: int | None) -> list[dict[str, Any]]:
    """Cap each hit's snippet so the whole set can fit the budget."""
    if max_chars is None and max_tokens is None:
        return hits
    budget: int = max_chars if max_chars is not None else (max_tokens or 0) * 4
    # Leave room for headers: roughly half the budget for snippets, min 100.
    snippet_cap = max(100, budget // max(1, len(hits)) - 80)
    out = []
    for hit in hits:
        h = dict(hit)
        h["snippet"] = (h.get("snippet") or "")[:snippet_cap]
        out.append(h)
    return out


def rrf_fuse(per_project: dict[str, list[dict[str, Any]]], limit: int,
             k: int = 60) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion across per-project result lists.

    Cross-collection cosine scores are NOT comparable (different model runs,
    different content pools); RRF compares ranks instead. A hit's fused score
    is sum(1 / (k + rank)) over the lists it appears in; the original score is
    kept as vector_score for display. Ties break by (file, start_line).
    """
    fused: dict[tuple, dict[str, Any]] = {}
    for project, hits in per_project.items():
        for rank, hit in enumerate(hits, 1):
            key = (project, hit.get("file"), hit.get("start_line"))
            entry = fused.get(key)
            if entry is None:
                entry = dict(hit)
                entry["vector_score"] = hit.get("score")
                entry["rrf_score"] = 0.0
                entry["rank_positions"] = []
                fused[key] = entry
            entry["rrf_score"] += 1.0 / (k + rank)
            entry["rank_positions"].append(rank)
    out = list(fused.values())
    out.sort(key=lambda h: (-h["rrf_score"], h.get("file") or "",
                            h.get("start_line") or 0))
    return out[:max(1, limit)]
