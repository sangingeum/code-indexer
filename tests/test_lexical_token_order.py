"""The lexical prefix rule must not depend on token order.

The query builder used to put the prefix wildcard on the LAST token only, so
`vector zzqxx` (absent token trailing) answered LESS than `zzqxx vector`:
the real word lost its prefix expansion. The properties pinned here:

* the same two tokens in either order return the same row set;
* an absent trailing token does not narrow the answer the present word's
  prefix family answers;
* no FTS5 operator text from the query reaches the MATCH string.

The escaping property already held at base: base quoted every token and only
the last carried `*`, so its test is a passing neighbour here rather than a
regression guard -- escaping was never broken, only the prefix reach was.
"""

from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.fts import build_match_query  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

# Two files so the FTS table has two distinct rows: one carries the
# exact token vector, the other only a longer token in the same family.
EXACT_FILE = "vector.py"
PREFIX_FILE = "vectorspace.py"
EXACT_TEXT = "def vector():\n    return []\n"
PREFIX_TEXT = "def vectorspace():\n    return {}\n"

# An absent token. Assembled from pieces so this test file does not put it
# into the corpus it indexes.
ABSENT = "zz" + "qxx"


class _StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 4 for _ in texts]


class _StubStore:
    def collection_exists(self, name: str) -> bool:
        return True

    def create_collection(self, name: str, dim: int) -> None:
        pass

    def upsert_points(self, name: str, points: object) -> None:
        pass

    def purge_file_points(self, *args: object, **kwargs: object) -> int:
        return 0

    def swap_collection(self, alias: str, staging: str) -> None:
        pass

    def count_points(self, name: str) -> int:
        return 0


@pytest.fixture()
def rig(tmp_path):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    project.mkdir()
    (project / EXACT_FILE).write_text(EXACT_TEXT)
    (project / PREFIX_FILE).write_text(PREFIX_TEXT)
    core = Core(cfg)
    core.embedder = _StubEmbedder()  # type: ignore[assignment]
    core.store = _StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="tokenorder")
    core.run_index(entry.slug, str(project))
    yield core, entry
    reg.close()
    shutil.rmtree(cfg.index_root, ignore_errors=True)


def _files(core, entry, query) -> list[str]:
    return sorted(h["file"] for h in core.search(
        query, project=entry.path, mode="lexical")["hits"])


def test_the_same_two_tokens_in_either_order_return_the_same_row_set(rig):
    """`vector zzqxx` and `zzqxx vector` must answer the same files."""
    core, entry = rig
    trailing = _files(core, entry, f"vector {ABSENT}")
    leading = _files(core, entry, f"{ABSENT} vector")
    assert trailing == leading, (
        "token order changed the answer: "
        f"trailing={trailing} leading={leading}")


def test_an_absent_trailing_token_does_not_narrow_the_answer_the_word_finds(rig):
    """The absent token contributes nothing; the present word keeps its
    prefix family in both positions."""
    core, entry = rig
    trailing = _files(core, entry, f"vector {ABSENT}")
    assert PREFIX_FILE in trailing, (
        "an absent trailing token removed the present word's prefix match: "
        f"{trailing}")


def test_no_fts5_operator_from_the_query_reaches_the_match_string():
    """Every token is quoted; operator words survive only as literal tokens."""
    match = build_match_query('vector OR NEAR AND NOT " ( ) * - zzqxx')
    assert match is not None
    parts = match.split(" OR ")
    for part in parts:
        body = part[:-1] if part.endswith("*") else part
        assert body.startswith('"') and body.endswith('"'), part
        assert body[1:-1].isalnum(), part
    assert "NEAR" in [p.strip('"*') for p in parts]
