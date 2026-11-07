"""Semantic search ranks by file class: source, examples, tests, docs, artifacts.

The policy is asserted through the real `Core.search` seam -- a stub store
returns a fixed cosine-ordered pool over an indexed tree, so every ranking
mode and retrieval mode goes through the one class order. Entirely offline.
"""

from __future__ import annotations

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core, _hit_identity  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402


FILES = {
    "src/app.py": "def alpha():\n    return 1\n",
    "src/module.py": "import os\n\nprint(os.getcwd())\n",
    "tests/test_app.py": "def test_alpha():\n    assert alpha()\n",
    "docs/guide.md": "# Guide\n\nAlpha is the entry point.\n",
    "examples/demo.py": "from app import alpha\n\nprint(alpha())\n",
    "telemetry/run.json": '{"alpha": 1, "run": "latest"}\n',
}


class _Point:
    def __init__(self, score: float, file: str, symbol: str | None) -> None:
        self.score = score
        self.payload = {
            "file": file, "chunk_index": 0, "symbol": symbol,
            "symbol_type": "function" if symbol else None,
            "lang": "python", "start_line": 1, "end_line": 2,
        }


class _StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 4 for _ in texts]

    def query_instruction_text(self, query: str) -> str:
        return query


class _StubStore:
    """One fixed pool in store (cosine) order, best first."""

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


def _pool() -> list[_Point]:
    """The test hit wins on cosine; the implementation is far below it."""
    return [
        _Point(0.93, "tests/test_app.py", "test_alpha"),
        _Point(0.88, "docs/guide.md", None),
        _Point(0.87, "telemetry/run.json", None),
        _Point(0.86, "src/app.py", "alpha"),
        _Point(0.60, "examples/demo.py", None),
        _Point(0.55, "src/module.py", None),
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
    core.store = _StubStore(_pool())  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="classproj")
    core.run_index(entry.slug, str(project))
    yield core, entry
    reg.close()
    shutil.rmtree(cfg.index_root, ignore_errors=True)


def _files(result) -> list[str]:
    return [h["file"] for h in result["hits"]]


def _position(files: list[str], name: str) -> int:
    return files.index(name)


def test_source_ranks_above_a_higher_cosine_test_hit(rig):
    core, entry = rig
    files = _files(core.search("where is alpha implemented", project=entry.path,
                               limit=10))
    assert files[0] == "src/app.py"
    assert _position(files, "tests/test_app.py") > _position(files, "src/app.py")


def test_every_hit_in_the_window_is_still_returned(rig):
    core, entry = rig
    files = _files(core.search("where is alpha implemented", project=entry.path,
                               limit=10))
    assert sorted(files) == sorted(p.payload["file"]
                                  for p in core.store.points)


def test_the_ladder_holds_across_the_pool(rig):
    core, entry = rig
    files = _files(core.search("where is alpha implemented", project=entry.path,
                               limit=10))
    assert (_position(files, "src/app.py")
            < _position(files, "examples/demo.py")
            < _position(files, "tests/test_app.py")
            < _position(files, "docs/guide.md"))


@pytest.mark.parametrize("ranking_mode", ["vector", "metadata", "hybrid"])
def test_every_ranking_mode_is_class_ordered(rig, ranking_mode):
    core, entry = rig
    result = core.search("where is alpha implemented", project=entry.path,
                         limit=10, ranking_mode=ranking_mode)
    assert _files(result)[0] == "src/app.py"


@pytest.mark.parametrize("mode", ["dense", "hybrid", "lexical"])
def test_every_retrieval_mode_is_class_ordered(rig, mode):
    core, entry = rig
    files = _files(core.search("alpha", project=entry.path, limit=10,
                               mode=mode))
    assert "src/app.py" in files
    assert files[0] == "src/app.py"


def test_a_test_targeting_query_waives_the_tests_demotion(rig):
    core, entry = rig
    files = _files(core.search("which tests cover alpha", project=entry.path,
                               limit=10))
    assert files[0] == "tests/test_app.py"
    # Docs are not waived: they stay below the tests they quote.
    assert (_position(files, "tests/test_app.py")
            < _position(files, "docs/guide.md"))


def test_a_data_format_query_waives_the_data_demotion(rig):
    core, entry = rig
    files = _files(core.search("the json run manifest", project=entry.path,
                               limit=10))
    assert files[0] == "telemetry/run.json"


def test_a_hit_without_a_symbol_names_what_matched(rig):
    core, entry = rig
    result = core.search("where is alpha implemented", project=entry.path,
                         limit=10)
    module_hit = next(h for h in result["hits"]
                      if h["file"] == "src/module.py")
    assert module_hit["symbol"] is None

    compact = core.format_hits(result["hits"], "compact")
    line = next(ln for ln in compact.splitlines() if "src/module.py" in ln)
    assert "import os" in line
    assert "  -  " not in line

    payload = json.loads(core.format_hits(result["hits"], "json"))
    json_hit = next(h for h in payload["hits"]
                    if h["file"] == "src/module.py")
    assert json_hit["identity"] == "import os"

    text = core.format_hits(result["hits"], "text")
    text_line = next(ln for ln in text.splitlines()
                     if "src/module.py" in ln)
    assert "(import os)" in text_line


def test_hit_identity_falls_back_to_the_file_qualifier():
    assert _hit_identity({"symbol": None, "snippet": ""}) == "file"
    assert _hit_identity({"symbol": None, "snippet": " \n\n\t\n"}) == "file"
    assert _hit_identity({"symbol": "alpha", "snippet": "x"}) == "alpha"


def test_the_same_query_twice_gives_the_same_order(rig):
    core, entry = rig
    first = core.search("where is alpha implemented", project=entry.path,
                        limit=10)["hits"]
    second = core.search("where is alpha implemented", project=entry.path,
                         limit=10)["hits"]
    assert [(h["file"], h["score"]) for h in first] == \
        [(h["file"], h["score"]) for h in second]


# -- the project's own ladder, through the search seam ----------------------
#
# `spec_e2e/` is not a built-in tests directory. A project that declares it
# under [tool.code-indexer.ordering] gets the tests rung for it on every
# surface, semantic-search included; a project that does not gets `source`.
LADDER_FILES = {
    "src/real.py": "def beta():\n    return 2\n",
    "spec_e2e/a.py": "def beta_case():\n    return 1\n",
}

LADDER_POOL = [
    _Point(0.93, "spec_e2e/a.py", "beta_case"),
    _Point(0.86, "src/real.py", "beta"),
]


def _project_with_a_ladder(core, reg, root, name, pyproject_text):
    """Index one project whose root carries `pyproject_text`."""
    project = root / name
    for rel, text in LADDER_FILES.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (project / "pyproject.toml").write_text(pyproject_text)
    entry = reg.add(str(project), name=name)
    core.run_index(entry.slug, str(project))
    return entry


def test_a_project_added_class_name_is_demoted_through_search(tmp_path):
    """The search key reads the project's table, not the built-in names."""
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    core = Core(cfg)
    core.embedder = _StubEmbedder()  # type: ignore[assignment]
    core.store = _StubStore(LADDER_POOL)  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    try:
        plain = _project_with_a_ladder(core, reg, tmp_path, "plain", "")
        declared = _project_with_a_ladder(
            core, reg, tmp_path, "declared",
            "[tool.code-indexer.ordering]\ntests = [\"spec_e2e\"]\n")
        query = "where is beta implemented"
        # No table: the path is source, so the cosine order stands.
        quiet = _files(core.search(query, project=plain.path, limit=10))
        assert quiet[0] == "spec_e2e/a.py"
        # Declared: the same path is the tests rung, so source leads.
        loud = _files(core.search(query, project=declared.path, limit=10))
        assert loud[0] == "src/real.py"
        assert loud.index("spec_e2e/a.py") > loud.index("src/real.py")
    finally:
        reg.close()
        shutil.rmtree(cfg.index_root, ignore_errors=True)
