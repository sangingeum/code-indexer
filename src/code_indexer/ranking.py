"""Query-time ranking for semantic_search: one class order, then metadata
adjustments and hybrid fusion.

The store returns candidates ranked purely by vector cosine similarity (or by
FTS bm25 in lexical mode). On top of that candidate pool, ONE ordering runs in
every ranking mode (`--ranking vector`, `metadata`, `hybrid`) and every
retrieval mode (`--mode dense`, `hybrid`, `lexical`):

- **file class first** (`class_order`) — `ordering.file_class` is the only
  classifier: source, then examples, then tests, then docs, then
  generated/artifact, with `ordering.class_weight` as the sort position. Within
  one rung the order the mode produced is kept: cosine order in vector mode,
  adjusted-score order in metadata mode, fused-score order in `--ranking
  hybrid`, RRF order across projects. This is an ORDER, never a filter: every
  hit the store returned in the window is still returned, the demoted ones at
  the tail, and the shaping caps below decide only which hits fill a capped
  answer.
- **repeat coverage after the class order** (`demote_repeated_spans`) — the
  chunker gives one code region several vectors (a declaration and an
  anonymous line window over the same lines, a container and its methods), so
  two slots of one answer could show the same source twice. A hit whose span
  overlaps a span an earlier hit of the same file already shows moves behind
  the distinct spans of its own rung, so a region no hit has shown yet
  outranks a re-shown one. Still an ORDER: never a filter, never a merge.
- **two waivers**, stated here because a waiver nobody wrote down is folklore:
  1. a query that targets tests — a token in `TEST_QUERY_MARKERS` (`test`,
     `tests`, `testing`, `spec`, `specs`, `fixture`, `fixtures`, `unittest`,
     `unittests`) — waives the tests rung's demotion: test hits rank with
     source, and the mode's own order breaks ties among them.
  2. a query that names a data format — a token in `DATA_QUERY_MARKERS`
     (`json`, `yaml`, `yml`, `toml`) — waives the pure-data down-weight and
     the pure-data share cap. A data format is a chunk shape, not a path
     class, so the class order still breaks ties among what remains.
  Neither waiver touches docs or generated/artifact: those stay demoted.
- **metadata adjustments** (opt-in `--ranking metadata`) — small additive
  deltas from payload facts already stored at index time (symbol presence).
- **hybrid fusion** (opt-in `--ranking hybrid`) — weighted-sum fusion of the
  vector ranking with a lightweight lexical token-overlap score over symbol
  name, file path, and snippet text (`fused = vector_score + 0.25 * lexical`).
  Helps queries that mix natural language with exact identifiers, where
  embeddings alone under-rank the right chunk.
- **data-file mitigation** — always applied (all modes, including the
  default vector mode): pure-data payload chunks (json/yaml/toml) get a
  small down-weight and their share of the top-k window is capped, because
  their self-descriptive keys can crowd out the code that produces the data
  for analysis-shaped queries. Waived when the query names a data format.

The path marker this module used to carry is gone. It called `fixtures/`,
`vendor/` and `testdata/` non-source while `ordering.file_class` calls a
`fixtures/` file source and a vendored tree source too, so one hit could be
"tests" to semantic-search and "source" to find-definition. One classifier
serves every surface now.

`skill/references/semantic.md` (Ordering) states the same order for the reader of
the output. All functions are pure and unit-testable without Qdrant or Ollama.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from . import ordering

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Query tokens that mean the caller is deliberately looking at tests: the
# tests rung's demotion is waived (see class_order).
TEST_QUERY_MARKERS = frozenset({
    "fixture", "fixtures", "spec", "specs", "test", "tests", "testing",
    "unittest", "unittests",
})

# Metadata adjustment magnitudes (additive on the cosine score).
DEFINITION_BOOST = 0.02

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


def hit_identity(hit: dict[str, Any]) -> tuple[str, str, int, int, str]:
    """The hit's place in the index: a complete, order-independent key.

    Two chunks are the same member when this tuple matches. It is the final
    tie-break of every score sort in this module, so an ordering never falls
    back to the order the candidates happened to arrive in — that order is the
    store's (or the fusion's) business, not the answer's.
    """
    return (
        str(hit.get("project") or ""),
        str(hit.get("file") or ""),
        int(hit.get("start_line") or 0),
        int(hit.get("end_line") or 0),
        str(hit.get("symbol") or ""),
    )


def score_order(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort by score descending, ties broken by `hit_identity`.

    The key is TOTAL: equal scores always fall in index order, never in
    arrival order, so the same set of hits yields byte-identical output no
    matter how the candidate list was ordered upstream.
    """
    return sorted(hits, key=lambda h: (-h["score"], *hit_identity(h)))


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

    Chunks carrying a symbol (definition-shaped) get a small boost over bare
    reference/comment chunks. Paths are NOT judged here: file class is the one
    classifier, and `class_order` applies it after the mode has scored the
    pool, so a test file is demoted by its class and never by a second rule.
    """
    delta = 0.0
    if hit.get("symbol"):
        delta += DEFINITION_BOOST
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
    return score_order(fused)


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
    return score_order(out)


# -- class order (every retrieval and ranking mode) -------------------------

def targets_tests(qtokens: list[str] | None) -> bool:
    """The caller named tests or fixtures -- the tests rung is waived."""
    if not qtokens:
        return False
    return bool({t.lower() for t in qtokens} & TEST_QUERY_MARKERS)


def class_order(hits: list[dict[str, Any]],
                qtokens: list[str] | None = None,
                names: ordering.ClassNames
                | Callable[[str], ordering.ClassNames] | None = None
                ) -> list[dict[str, Any]]:
    """Reorder a scored pool by file class: source, examples, tests, docs,
    then generated/artifact.

    The class key is `ordering.class_weight` -- the SAME classifier the
    structural surfaces use, so a path cannot be "tests" here and "source"
    to `find-definition`. Python's sort is stable, so hits sharing a rung keep
    the order the mode produced (cosine, adjusted score, fused score, RRF):
    the class decides position and only position -- every input is still in
    the output, and the same input twice gives the same order.

    `names` is the LADDER the key reads, and it is the project's own table
    (``ordering.load_class_names``), never a bare default:

    * one ``ordering.ClassNames`` when every hit comes from one project;
    * a callable taking the hit's own ``project`` when the window mixes
      projects (``--all-projects``), so each hit is judged by the table of
      the tree it sits in instead of by the built-in names. A name a project
      adds under ``[tool.code-indexer.ordering]`` therefore carries the same
      rung here that it carries on the structural surfaces.

    Waivers (both stated in the module docstring):

    * a test-targeting query (`targets_tests`) sorts the tests rung WITH
      source, so a deliberately test-scoped search keeps its relevance order;
    * a data-format query changes nothing in the class ladder, because a data
      format is a chunk shape and not a path class: it waives the pure-data
      down-weight and share cap (`downweight_data_files`,
      `cap_data_file_share`), and the class order breaks the ties among what
      remains.
    """
    if not hits:
        return []
    waived_tests = targets_tests(qtokens)

    return sorted(hits, key=lambda h: _rung_position(h, names, waived_tests))


def demote_repeated_spans(hits: list[dict[str, Any]],
                          qtokens: list[str] | None = None,
                          names: ordering.ClassNames
                          | Callable[[str], ordering.ClassNames]
                          | None = None) -> list[dict[str, Any]]:
    """A chunk whose lines an earlier hit of the same file already shows does
    not take an answer slot ahead of a region no hit has shown yet.

    The chunker emits several vectors for one code region -- a declaration and
    an anonymous line window over the same lines, a class and its methods, a
    window and the same window shifted by a line. Those siblings overlap, so
    two slots of one answer can show the same source twice while a different
    region never appears. This pass keeps the first (best-ranked) hit of every
    span and moves later hits that overlap a span already shown, of the same
    file, behind the DISTINCT spans of their own rung.

    Rung-scoped on purpose: `class_order` already put source before docs, and
    demoting a source duplicate past a document would trade one bad slot for a
    worse one. Within the rung the relative order of the distinct spans is the
    one the mode produced; the repeated ones keep their own relative order
    behind them, so the result is a permutation of the input, never a filter
    and never a merge -- `hit_identity` replaces nothing, the answer just
    ranks a new region ahead of a re-shown one.

    Hits without a line range have no span to repeat and stay where they are.
    `names` resolves the ladder exactly as `class_order` does (one project's
    table, or one per hit for a mixed window).
    """
    if not hits:
        return []
    waived_tests = targets_tests(qtokens)
    out: list[dict[str, Any]] = []
    distinct: list[dict[str, Any]] = []
    repeated: list[dict[str, Any]] = []
    shown: dict[tuple[str, str], list[tuple[int, int]]] = {}
    current: int | None = None

    def flush() -> None:
        out.extend(distinct)
        out.extend(repeated)

    for hit in hits:
        rung = _rung_position(hit, names, waived_tests)
        if current is None or rung != current:
            flush()
            distinct, repeated = [], []
            shown = {}
            current = rung
        start, end = hit.get("start_line"), hit.get("end_line")
        if not isinstance(start, int) or not isinstance(end, int):
            distinct.append(hit)
            continue
        key = (str(hit.get("project") or ""), str(hit.get("file") or ""))
        spans = shown.setdefault(key, [])
        if any(start <= de and ds <= end for ds, de in spans):
            repeated.append(hit)
        else:
            distinct.append(hit)
            spans.append((start, end))
    flush()
    return out


def _ladder_for(hit: dict[str, Any],
                names: ordering.ClassNames
                | Callable[[str], ordering.ClassNames] | None
                ) -> ordering.ClassNames:
    if names is None:
        return ordering.DEFAULT_CLASS_NAMES
    if callable(names):
        return names(str(hit.get("project") or ""))
    return names


def _rung_position(hit: dict[str, Any],
                   names: ordering.ClassNames
                   | Callable[[str], ordering.ClassNames] | None,
                   waived_tests: bool) -> int:
    """The hit's class rung, with the test-targeting waiver applied.

    One expression for both `class_order` and `demote_repeated_spans`, so the
    two passes can never disagree about where a rung begins.
    """
    position = ordering.class_weight(hit.get("file") or "",
                                     _ladder_for(hit, names))
    if waived_tests and position == ordering.CLASS_WEIGHT[ordering.TESTS]:
        return ordering.CLASS_WEIGHT[ordering.SOURCE]
    return position


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
    return score_order(out)


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
    all contributing symbols in line order. Output is re-sorted by score with
    `hit_identity` as the tie-break.

    The walk runs in LINE order, not score order. A score-order walk
    chain-merges differently depending on which neighbour it meets first —
    the same candidate set then yields one union or two — so a deep-limit
    answer gained or lost a member between runs whose only difference was the
    order the store handed back equal scores. Walking the ranges makes the
    membership a function of the hit SET alone.
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
                       key=lambda h: (h["start_line"], h["end_line"],
                                      hit_identity(h)))
        run: list[dict[str, Any]] = []
        run_end = 0
        for hit in group:
            if run and hit["start_line"] > run_end + adjacency_gap + 1:
                merged.append(_merge_run(run))
                run = []
            run.append(hit)
            run_end = max(hit["end_line"], run_end)
        if run:
            merged.append(_merge_run(run))
    return score_order(merged + others)


def _merge_run(run: list[dict[str, Any]]) -> dict[str, Any]:
    """One line-ordered run of same-file hits collapsed into its union.

    The best-scoring member supplies the payload fields (snippet, provenance,
    score); the range is the union and the symbol fields list every member's
    in line order. Pure: the run is already in canonical order.
    """
    best = run[0]
    for hit in run[1:]:
        if (-hit["score"], hit_identity(hit)) < (-best["score"],
                                                 hit_identity(best)):
            best = hit
    out = dict(best)
    out["start_line"] = min(h["start_line"] for h in run)
    out["end_line"] = max(h["end_line"] for h in run)
    if len(run) > 1:
        for field in ("symbol", "symbol_type"):
            parts: list[str] = []
            for hit in run:
                value = hit.get(field)
                if value and value not in parts:
                    parts.append(value)
            out[field] = ", ".join(parts) or None
        out["merged_count"] = len(run)
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
    kept as vector_score for display. Ties break by `hit_identity` — a total
    key, so the answer does not depend on the order the lists were handed in.
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
    out.sort(key=lambda h: (-h["rrf_score"], *hit_identity(h)))
    return out[:max(1, limit)]
