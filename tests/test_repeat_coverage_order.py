"""Repeat coverage: one answer never spends two slots on the same lines.

The chunker gives one code region several vectors -- a declaration and an
anonymous line window over the same lines, a window and the same window
shifted by one -- so sibling chunks overlap. The ordering asserted here keeps
the best-ranked hit of every span and moves later overlapping siblings behind
the distinct spans of their own rung, so a region no hit has shown yet ranks
above a re-shown one.

Both levels are pinned: the pure ordering (`demote_repeated_spans`) and the
`Core.search` seam that runs it, offline against a stub store.
"""

from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer import ordering  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.ranking import class_order, query_tokens  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

FILES = {
    "src/region.py": "def region(a, b):\n    return a + b\n",
    "src/elsewhere.py": "def other(x):\n    return x * 2\n",
    "docs/guide.md": "# Guide\n\nRegion is the entry point.\n",
}


def hit(score: float, file: str, start: int, end: int,
        symbol: str | None = None, project: str = "") -> dict:
    return {
        "score": score, "file": file, "symbol": symbol,
        "symbol_type": "function" if symbol else None, "project": project,
        "lang": "python", "start_line": start, "end_line": end,
        "snippet": "body",
    }


# -- the pure ordering -----------------------------------------------------


def test_a_repeated_span_yields_its_slot_to_a_new_region():
    """A window and a declaration over the same lines are one region shown
    twice: the second one moves behind a span no hit has shown."""
    from code_indexer.ranking import demote_repeated_spans

    window = hit(0.90, "src/region.py", 10, 30)
    declaration = hit(0.89, "src/region.py", 10, 40, symbol="region")
    elsewhere = hit(0.70, "src/elsewhere.py", 5, 12, symbol="other")

    out = demote_repeated_spans([window, declaration, elsewhere])

    assert [h["file"] for h in out] == [
        "src/region.py", "src/elsewhere.py", "src/region.py"]
    assert out[0] is window
    assert out[1] is elsewhere
    assert out[2] is declaration


def test_a_partial_overlap_counts_as_the_same_region():
    """One line of overlap is enough: the shifted window shows lines the
    earlier hit already showed."""
    from code_indexer.ranking import demote_repeated_spans

    first = hit(0.80, "src/region.py", 40, 60)
    shifted = hit(0.79, "src/region.py", 59, 70)
    fresh = hit(0.60, "src/region.py", 1, 20)

    out = demote_repeated_spans([first, shifted, fresh])

    assert [h["start_line"] for h in out] == [40, 1, 59]


def test_distinct_spans_of_one_file_keep_the_modes_order():
    from code_indexer.ranking import demote_repeated_spans

    a = hit(0.9, "src/region.py", 1, 10)
    b = hit(0.8, "src/region.py", 30, 40)
    c = hit(0.7, "src/region.py", 60, 70)
    assert demote_repeated_spans([a, b, c]) == [a, b, c]


def test_the_demotion_stays_inside_the_hits_own_rung():
    """A source duplicate never falls behind a document: the class order is
    the outer key, and this pass only decides which member of a rung takes
    which slot."""
    from code_indexer.ranking import demote_repeated_spans

    source_first = hit(0.95, "src/region.py", 10, 30)
    source_repeat = hit(0.94, "src/region.py", 10, 40, symbol="region")
    document = hit(0.40, "docs/guide.md", 1, 3)

    pool = class_order([source_first, source_repeat, document],
                       query_tokens("how is a region added"))
    out = demote_repeated_spans(pool, query_tokens("how is a region added"))

    assert [h["file"] for h in out] == [
        "src/region.py", "src/region.py", "docs/guide.md"]


def test_the_answer_is_a_permutation_of_the_pool():
    """An order, never a filter and never a merge: nothing is dropped, and
    no two hits become one."""
    from code_indexer.ranking import demote_repeated_spans

    pool = [hit(0.9, "src/region.py", 10, 30),
            hit(0.89, "src/region.py", 10, 40, symbol="region"),
            hit(0.7, "src/elsewhere.py", 5, 12, symbol="other"),
            hit(0.6, "docs/guide.md", 1, 3)]
    out = demote_repeated_spans(pool)
    assert len(out) == len(pool)
    assert {id(h) for h in out} == {id(h) for h in pool}


def test_applying_the_pass_twice_is_the_identity():
    from code_indexer.ranking import demote_repeated_spans

    pool = [hit(0.9, "src/region.py", 10, 30),
            hit(0.89, "src/region.py", 10, 40, symbol="region"),
            hit(0.7, "src/elsewhere.py", 5, 12, symbol="other")]
    once = demote_repeated_spans(pool)
    assert demote_repeated_spans(once) == once


def test_a_hit_without_a_range_keeps_its_place():
    """A lexical hit with no line range has no span to repeat."""
    from code_indexer.ranking import demote_repeated_spans

    first = hit(0.9, "src/region.py", 10, 30)
    rangeless = hit(0.85, "src/region.py", 0, 0, symbol="region")
    rangeless.pop("start_line")
    rangeless.pop("end_line")
    repeat = hit(0.84, "src/region.py", 10, 40, symbol="region")

    out = demote_repeated_spans([first, rangeless, repeat])

    assert out[0] is first
    assert out[1] is rangeless
    assert out[2] is repeat


def test_the_rung_reads_each_hits_own_project_ladder():
    """The same file is tests in a project that declares `spec_e2e`, source
    elsewhere -- so the demotion groups them exactly as the class order did."""
    from code_indexer.ranking import demote_repeated_spans

    declared = ordering.ClassNames(
        tests=ordering.DEFAULT_CLASS_NAMES.tests | {"spec_e2e"})

    def names_for(project: str) -> ordering.ClassNames:
        return declared if project == "/tree/declared" else \
            ordering.DEFAULT_CLASS_NAMES

    pool = [
        hit(0.90, "src/app.py", 10, 20, symbol="app",
            project="/tree/declared"),
        hit(0.88, "src/app.py", 10, 30, symbol="app",
            project="/tree/declared"),
        hit(0.86, "spec_e2e/a.py", 1, 5, symbol="beta_case",
            project="/tree/declared"),
    ]
    qtokens = query_tokens("where is beta implemented")
    ordered = class_order(pool, qtokens, names_for)
    out = demote_repeated_spans(ordered, qtokens, names_for)
    assert [h["file"] for h in out] == [
        "src/app.py", "src/app.py", "spec_e2e/a.py"]


# -- the Core.search seam --------------------------------------------------


class _Point:
    def __init__(self, score: float, file: str, chunk_index: int,
                 symbol: str | None, start: int, end: int) -> None:
        self.score = score
        self.payload = {
            "file": file, "chunk_index": chunk_index, "symbol": symbol,
            "symbol_type": "function" if symbol else None,
            "lang": "python", "start_line": start, "end_line": end,
        }


class _StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 4 for _ in texts]

    def query_instruction_text(self, query: str) -> str:
        return query


class _StubStore:
    def __init__(self, points: list[_Point]) -> None:
        self.points = points

    def collection_exists(self, name: str) -> bool:
        return True

    def create_collection(self, name: str, dim: int) -> None:
        pass

    def upsert_points(self, name: str, points: object) -> None:
        pass

    def purge_file_points(self, *args: object, **kwargs: object) -> int:
        return 0

    def count_points(self, name: str) -> int:
        return 0

    def swap_collection(self, alias: str, staging: str) -> None:
        pass

    def search(self, name: str, vector: list[float], limit: int = 8,
               file_filter: str | None = None,
               symbol_type: str | None = None,
               language: str | None = None) -> list[_Point]:
        return self.points[:limit]


def _sibling_pool() -> list[_Point]:
    """Two chunk rows over the same lines of one file outrank a second file.

    Chunk indexes past the file's real rows keep the payload's own ranges, so
    the fixture states the spans the chunker would have stored.
    """
    return [
        _Point(0.90, "src/region.py", 900, None, 10, 30),
        _Point(0.89, "src/region.py", 901, "region", 10, 40),
        _Point(0.70, "src/elsewhere.py", 902, "other", 5, 12),
    ]


@pytest.fixture()
def rig(tmp_path):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    for rel, text in FILES.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    core = Core(cfg)
    core.embedder = _StubEmbedder()  # type: ignore[assignment]
    core.store = _StubStore(_sibling_pool())  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="siblingproj")
    core.run_index(entry.slug, str(project))
    yield core, entry
    reg.close()
    shutil.rmtree(cfg.index_root, ignore_errors=True)


def test_a_duplicate_span_does_not_hold_a_slot_the_answer_needs(rig):
    """The window and the declaration show the same lines; the second file's
    chunk takes the slot the duplicate used to hold."""
    core, entry = rig
    hits = core.search("where is the region handled", project=entry.path,
                       limit=3, merge=False)["hits"]
    assert [h["file"] for h in hits] == [
        "src/region.py", "src/elsewhere.py", "src/region.py"]
    assert [h["start_line"] for h in hits] == [10, 5, 10]
    assert hits[2]["symbol"] == "region"


def test_the_merge_path_still_shows_both_regions(rig):
    """With overlap merging on (the default read), the two siblings collapse
    into one union and the other file is still in the answer."""
    core, entry = rig
    hits = core.search("where is the region handled", project=entry.path,
                       limit=3)["hits"]
    files = [h["file"] for h in hits]
    assert "src/elsewhere.py" in files
    assert files.count("src/region.py") == 1
