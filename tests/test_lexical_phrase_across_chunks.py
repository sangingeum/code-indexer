"""The lexical rail reads a phrase as evidence, not as a requirement.

Multi-word queries used to be quoted tokens joined by FTS5's implicit AND,
so every word had to sit inside ONE chunk and an ordinary phrase matched
nothing. The property pinned here: a phrase matches when its words occur in
DIFFERENT chunks of the same file, while a query of two unknown words still
matches nothing.
"""

from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

PROJECT_FILE = "receipts.py"

# The chunker emits ONE chunk for a file at or below its ~1000-character cap,
# so the filler keeps the two declarations in separate window chunks while
# sharing neither word between them.
FILLER = "\n".join(f"# padding line {n}" for n in range(90))

PROJECT_TEXT = (
    "def receipt_total(rows):\n"
    "    return sum(rows)\n"
    "\n" + FILLER + "\n\n"
    "def digest_store(payload):\n"
    "    return payload\n"
)


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
    (project / PROJECT_FILE).write_text(PROJECT_TEXT)
    core = Core(cfg)
    core.embedder = _StubEmbedder()  # type: ignore[assignment]
    core.store = _StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="phraseproj")
    core.run_index(entry.slug, str(project))
    yield core, entry
    reg.close()
    shutil.rmtree(cfg.index_root, ignore_errors=True)


def test_a_phrase_matches_words_in_different_chunks_of_one_file(rig):
    """Neither chunk holds both words; the phrase must still reach the file."""
    core, entry = rig
    manifest = core.manifest_for(entry.slug)
    try:
        chunks = [c for c in manifest.all_chunks()
                  if c.file == PROJECT_FILE]
    finally:
        manifest.close()
    receipt = {c.chunk_index for c in chunks if "receipt_total" in c.text}
    digest = {c.chunk_index for c in chunks if "digest_store" in c.text}
    assert receipt and digest, "both declarations are stored"
    assert receipt.isdisjoint(digest), "the words sit in different chunks"

    hits = core.search("receipt digest", project=entry.path,
                       mode="lexical")["hits"]
    assert [h["file"] for h in hits].count(PROJECT_FILE) == 1, \
        "a phrase whose words are split across chunks still matches the file"


def test_a_nonsense_two_word_query_matches_nothing(rig):
    """Widening the match construction must not make every query a hit.

    The nonsense tokens are assembled from pieces so this file does not put
    them into the corpus it is indexed into.
    """
    core, entry = rig
    nonsense = " ".join(("zz" + "qx", "bl" + "orf"))
    assert core.search(nonsense, project=entry.path,
                       mode="lexical")["hits"] == []
