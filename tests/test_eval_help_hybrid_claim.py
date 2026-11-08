"""`eval`'s mode is forwarded to the search pipeline, so its help must say so.

The command's help used to say the hybrid mode was "accepted for forward
compatibility but currently aliases the dense pipeline (the lexical index is
not built yet)". The lexical index does exist: `Core.search` dispatches
dense, lexical and hybrid to three different paths, and hybrid fuses the
dense ranking with the full-text ranking. A measurement round read the old
sentence, treated hybrid as dense, and then measured hybrid beating dense --
a help string that contradicts the code is a measurement hazard, not
cosmetics.

These tests pin the behaviour (the lexical arm really runs, so the answer is
not the dense answer) and the help text that describes it.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.evalharness import run_eval  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()

QUERY = "refresh token rotation"
DENSE_FILE = "dense_winner.py"
LEXICAL_FILE = "lexical_winner.py"


class StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]

    def embed_query_once(self, text):
        return [0.1] * 4

    def query_instruction_text(self, query: str) -> str:
        return query


class _Hit:
    def __init__(self, payload: dict, score: float) -> None:
        self.payload, self.score = payload, score


class StubStore:
    """The dense arm: it only ever answers DENSE_FILE.

    The lexical arm never asks the store, so a file the store cannot see can
    still reach a hybrid answer -- which is exactly the difference under test.
    """

    def collection_exists(self, name):
        return True

    def create_collection(self, name, dim):
        pass

    def upsert_points(self, name, points):
        pass

    def purge_file_points(self, *a, **kw):
        return 0

    def count_points(self, name):
        return 0

    def swap_collection(self, collection, alias):
        pass

    def search(self, *a, **kw):
        return [_Hit({"file": DENSE_FILE, "chunk_index": 0,
                      "symbol": "alpha_handler",
                      "symbol_type": "function", "lang": "python",
                      "start_line": 1, "end_line": 2}, 0.9)]


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    cfg = Config(ollama_url="http://stub", qdrant_url="http://stub",
                 embed_model="stub", index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=1, watch_sweep_interval=3600)
    project = tmp_path / "proj"
    project.mkdir()
    (project / DENSE_FILE).write_text(
        "def alpha_handler():\n    return 1\n")
    (project / LEXICAL_FILE).write_text(
        "def refresh_token_rotation(pool):\n"
        "    '''Rotate every refresh token in the pool.'''\n"
        "    return pool\n")
    core = Core(cfg)
    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="hybridproj")
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    yield core, entry, project
    reg.close()


def _files(hits):
    return [h["file"] for h in hits]


def test_hybrid_is_not_an_alias_of_dense(rig):
    """The lexical arm really runs: a file the store cannot see still ranks.

    Dense answers with the store's one hit. Hybrid fuses that ranking with
    the full-text ranking, so the file only the text index knows appears. An
    alias would produce the dense answer byte for byte.
    """
    core, entry, _ = rig
    dense = core.search(QUERY, project=entry.path, mode="dense")["hits"]
    hybrid = core.search(QUERY, project=entry.path, mode="hybrid")["hits"]
    assert DENSE_FILE in _files(dense)
    assert LEXICAL_FILE not in _files(dense), (
        "the stub store cannot answer with the lexical file, so dense must "
        "not name it")
    assert LEXICAL_FILE in _files(hybrid), (
        "hybrid must run the lexical arm; its absence means hybrid aliased "
        "dense")
    assert _files(hybrid) != _files(dense)


def test_the_eval_harness_forwards_the_mode_into_the_pipeline(rig, tmp_path):
    """`eval --mode hybrid` reaches Core.search as hybrid, not as dense."""
    core, entry, _ = rig
    queries = tmp_path / "queries.jsonl"
    queries.write_text(json.dumps({
        "id": "q1", "query": QUERY,
        "relevant": [{"file": LEXICAL_FILE}]}) + "\n", encoding="utf-8")
    seen: list[str] = []
    real_search = core.search

    def recording_search(*args, **kwargs):
        seen.append(kwargs.get("mode"))
        return real_search(*args, **kwargs)

    core.search = recording_search  # type: ignore[method-assign]
    report = run_eval(core, entry.path, str(queries), mode="hybrid")
    assert seen == ["hybrid"], seen
    assert report["mode"] == "hybrid"
    assert report["results"][0]["rank"] is not None, (
        "the lexical file is a hybrid hit, so the label must be found")


def test_the_eval_help_does_not_call_hybrid_a_dense_alias():
    """The documented sentence must agree with the pipeline above.

    A caller (and a measurement round) that reads "aliases the dense
    pipeline" constrains itself to dense and reports a difference it cannot
    explain. The help names the three pipelines instead.
    """
    result = runner.invoke(app, ["eval", "--help"])
    assert result.exit_code == 0, (result.exit_code, result.output)
    help_text = result.stdout.lower()
    assert "aliases the dense" not in help_text, (
        "the help still claims hybrid aliases the dense pipeline, which the "
        "pipeline in test_hybrid_is_not_an_alias_of_dense contradicts")
    assert "forward compatibility" not in help_text
    assert "hybrid" in help_text
    assert "lexical" in help_text, (
        "the help must name the lexical arm hybrid fuses with")
