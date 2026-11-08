"""A bad flag VALUE is a usage error, not a fault in the tool.

ADR-0003 section 5 and skill/references/failures.md put a bad flag value at
``ArgumentError``, exit 2: the request was wrong, so the caller fixes the
invocation. Before this file, four enumerated flags fell through the CLI
boundary into the core and came back as something else entirely -- a bad
search mode as ``InternalError`` (exit 1), a bad ranking or rerank value as a
wrapped ``BackendError`` (exit 3, "search failed on every project"), a bad
eval mode as ``InternalError``. A caller reading that line would go looking
for a broken install.

So every test here asserts the property that was missing: exit 2, ONE stderr
line naming the values the flag accepts, empty stdout -- and, for the flags
whose accepted list also lives in the core, that the CLI is reading that one
list rather than a second copy of it.

Offline: stub embedder and store, a real SQLite manifest and FTS index.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer import core as core_mod  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()

# The accepted values each flag prints in its refusal. They are written out
# here rather than imported so that this file, replayed against a revision
# that does not have such names yet, still runs its assertions and fails on
# the exit codes instead of failing to import. The last test pins these words
# to the tuples the pipeline actually declares.
SEARCH_MODE_VALUES = ("dense", "lexical", "hybrid")
RANKING_VALUES = ("vector", "metadata", "hybrid")
RERANK_VALUES = ("heuristic", "none")
REFERENCE_FILTER_VALUES = ("calls", "includes", "inherits", "references")

QUERIES = ('{"id": "q1", "query": "refresh the token", '
           '"relevant": [{"file": "core.py", '
           '"symbol": "maybe_refresh"}], "tags": ["conceptual"]}\n')


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    """A registered, indexed project whose search runs without a service."""
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "core.py").write_text(
        "def maybe_refresh(slug):\n"
        "    '''Staleness probe for one slug.'''\n"
        "    return slug\n")
    core = Core(cfg)

    class StubEmbedder:
        def dimension(self):
            return 4

        def embed(self, texts):
            return [[0.1] * 4 for _ in texts]

    class StubStore:
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

        def search(self, *a, **kw):
            class _Hit:
                def __init__(self, payload, score):
                    self.payload, self.score = payload, score
            return [_Hit({"file": "core.py", "symbol": "maybe_refresh",
                          "symbol_type": "function", "lang": "python",
                          "start_line": 1, "end_line": 3,
                          "snippet": "def maybe_refresh(slug):"}, 0.9)]

    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="flagproj")
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    yield core, entry, project
    reg.close()
    monkeypatch.undo()


def entry_path(rig) -> str:
    """The registered project's path, the selector every invocation passes."""
    return rig[1].path


def _refusal(args: list[str]):
    """Invoke the CLI and return the result, having asserted the one shape.

    Every refusal in this file is the same contract: exit 2, nothing on
    stdout, exactly one stderr line starting ``ArgumentError:``.
    """
    result = runner.invoke(app, args, catch_exceptions=False)
    assert result.exit_code == 2, (
        f"expected a usage error (exit 2) for {args}, got "
        f"exit {result.exit_code}: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}")
    assert result.stdout == "", f"a refusal prints no answer: {result.stdout!r}"
    lines = result.stderr.splitlines()
    assert len(lines) == 1, f"exactly one stderr line: {lines!r}"
    assert lines[0].startswith("ArgumentError: "), lines[0]
    return result


def test_a_bad_search_mode_is_a_usage_error_not_an_internal_error(rig):
    """Measured before: rc=1, ``InternalError: unknown search mode: bogus``."""
    _refusal(["semantic-search", "query validation", "--project",
              entry_path(rig), "--mode", "bogus"])


def test_a_bad_ranking_value_does_not_become_a_backend_failure(rig):
    """Measured before: rc=3, ``BackendError: search failed on every project``."""
    result = _refusal(["semantic-search", "x", "--project", entry_path(rig),
                       "--ranking", "bogus"])
    assert "unknown --ranking value: 'bogus'" in result.stderr
    assert "expected vector|metadata|hybrid" in result.stderr


def test_a_bad_rerank_value_is_a_usage_error(rig):
    result = _refusal(["semantic-search", "x", "--project", entry_path(rig),
                       "--rerank", "bogus"])
    assert "unknown --rerank value: 'bogus'" in result.stderr
    assert "expected heuristic|none" in result.stderr


def test_a_bad_eval_mode_is_a_usage_error_not_an_internal_error(rig, tmp_path):
    """Measured before: rc=1, ``InternalError: unknown eval mode 'bogus'``."""
    queries = tmp_path / "queries.jsonl"
    queries.write_text(QUERIES)
    _refusal(["eval", "--project", entry_path(rig), "--queries", str(queries),
              "--mode", "bogus"])


def test_a_bad_eval_rerank_value_is_a_usage_error(rig, tmp_path):
    queries = tmp_path / "queries.jsonl"
    queries.write_text(QUERIES)
    _refusal(["eval", "--project", entry_path(rig), "--queries", str(queries),
              "--rerank", "bogus"])


def test_a_bad_relationship_filter_is_a_usage_error(rig):
    result = _refusal(["find-references", "maybe_refresh", "--project",
                       entry_path(rig), "--relationship", "bogus"])
    assert ("unknown --relationship value: 'bogus'" in result.stderr)
    assert "expected calls|includes|inherits|references" in result.stderr


def test_the_refusal_names_the_values_the_flag_accepts(rig):
    """The line is actionable: it prints the list, not only the rejection."""
    result = _refusal(["semantic-search", "x", "--project", entry_path(rig),
                       "--mode", "bogus"])
    assert result.stderr.strip() == (
        "ArgumentError: unknown --mode value: 'bogus' "
        "(expected dense|lexical|hybrid)")


@pytest.mark.parametrize("mode", SEARCH_MODE_VALUES)
def test_every_listed_search_mode_is_accepted(rig, mode):
    """A refusal list that is one value too short is a defect of its own."""
    result = runner.invoke(app, ["semantic-search", "refresh", "--project",
                                 entry_path(rig), "--mode", mode],
                           catch_exceptions=False)
    assert result.exit_code == 0, (mode, result.stdout, result.stderr)


@pytest.mark.parametrize("ranking", RANKING_VALUES)
def test_every_listed_ranking_is_accepted(rig, ranking):
    result = runner.invoke(app, ["semantic-search", "refresh", "--project",
                                 entry_path(rig), "--ranking", ranking],
                           catch_exceptions=False)
    assert result.exit_code == 0, (ranking, result.stdout, result.stderr)


@pytest.mark.parametrize("rerank", RERANK_VALUES)
def test_every_listed_rerank_value_is_accepted(rig, rerank):
    result = runner.invoke(app, ["semantic-search", "refresh", "--project",
                                 entry_path(rig), "--rerank", rerank],
                           catch_exceptions=False)
    assert result.exit_code == 0, (rerank, result.stdout, result.stderr)




def test_the_lists_the_cli_refuses_are_the_lists_the_pipeline_runs():
    """ONE source of truth: the CLI reads the tuples the pipeline declares.

    A copy of the words in the CLI would drift the first time a mode is added:
    the CLI would accept a value the pipeline rejects (or refuse one it runs).
    """
    from code_indexer import evalharness, reranker

    rerank_modes = getattr(reranker, "RERANK_MODES", None)
    assert rerank_modes is not None, (
        "the reranker must declare the values it accepts")
    assert getattr(cli, "SEARCH_MODES", None) is core_mod.SEARCH_MODES, (
        "the CLI must read the search mode list, not carry a copy of it")
    assert getattr(cli, "RANKING_MODES", None) is core_mod.RANKING_MODES, (
        "the CLI must read the ranking list, not carry a copy of it")
    assert getattr(cli, "RERANK_MODES", None) is rerank_modes, (
        "the CLI must read the rerank list, not carry a copy of it")
    assert evalharness.EVAL_MODES is core_mod.SEARCH_MODES, (
        "the eval harness forwards its mode into Core.search, so its accepted "
        "list IS the search mode list")
    assert tuple(getattr(Core, "REFERENCE_FILTERS", ())) == (
        REFERENCE_FILTER_VALUES)
