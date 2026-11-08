"""A bad flag VALUE is a usage error, not a fault in the tool.

ADR-0003 section 5 and skill/references/failures.md put a bad flag value at
``ArgumentError``, exit 2: the request was wrong, so the caller fixes the
invocation. At the base revision (``e946911``) six surfaces fell through the
CLI boundary into the core and came back as something else entirely -- a bad
search mode as ``InternalError`` (exit 1), a bad search ``--ranking`` or
``--rerank`` value as a wrapped ``BackendError`` (exit 3, "search failed on
every project"), a bad eval ``--mode`` as ``InternalError`` (exit 1) and a
bad eval ``--rerank`` the same wrapped way (exit 3), and an unknown
``--relationship`` value as ``InternalError`` (exit 1). ``--format`` and
``--vectors`` WERE already refused at the boundary with exit 2 -- on
``semantic-search`` the format is resolved before the extension check too, so
a wrong format answers exit 2 even on a bare install. ``--visibility``
was refused with the right code but only AFTER the index read, so an
unregistered project answered ``NotFoundError`` (exit 1) for an invocation
that was merely wrong.

Eight command/flag surfaces now refuse at the boundary: ``--mode``,
``--ranking`` and ``--rerank`` on search; ``--mode`` and ``--rerank`` on
eval; ``--relationship``; ``--visibility``; and ``--vectors``, whose accepted
list moved onto the vectors policy's own tuple. This file tests the first
seven; ``--vectors`` is covered by its own tests elsewhere.

So every test here asserts the property that was missing: exit 2, ONE stderr
line naming the values the flag accepts, empty stdout -- and, for the flags
whose accepted list also lives in the core, that the CLI is reading that one
list rather than a second copy of it.

Two later arrivals are tested here for the same property. `get-code-context`'s
selector and `eval`'s `--k` used to be refused only after the index read, so an
unregistered project answered the read's class (``NotFoundError``, exit 1; and
for `--k` an ``InternalError`` from the old refusal helper) for an invocation
that was merely wrong. Both are now refused before the core is built, and both
have a test that passes an UNREGISTERED project.

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


class _StubEmbedder:
    def dimension(self):
        return 4

    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]


class _StubStoreBase:
    """The store contract a pass needs, WITHOUT the collection swap.

    ``swap_collection`` is deliberately absent here: this is the shape of the
    pre-fix rig, which logged a swallowed indexing error and still passed
    every test in this file, because nothing asserted that the pass reached
    its store contract at all. Only a subclass that adds the swap is a rig a
    passing test may use.
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

    def search(self, *a, **kw):
        class _Hit:
            def __init__(self, payload, score):
                self.payload, self.score = payload, score

        return [_Hit({"file": "core.py", "symbol": "maybe_refresh",
                      "symbol_type": "function", "lang": "python",
                      "start_line": 1, "end_line": 3,
                      "snippet": "def maybe_refresh(slug):"}, 0.9)]


class _RecordingStore(_StubStoreBase):
    """The tip rig: records the swap, so the acceptance half can assert it."""

    def __init__(self):
        self.swaps: list[tuple[str, str]] = []

    def swap_collection(self, tmp, target):
        self.swaps.append((tmp, target))


class _RefusingStore(_RecordingStore):
    """Control one: every other member works, the swap refuses."""

    def swap_collection(self, tmp, target):
        raise RuntimeError("the store refused the swap")


class _PreFixStore(_StubStoreBase):
    """Control two: the pre-fix rig -- no ``swap_collection`` at all."""


def _build_rig(tmp_path, monkeypatch, store):
    """A registered project indexed through ``store``, with the CLI rendered."""
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
    core.embedder = _StubEmbedder()  # type: ignore[assignment]
    core.store = store  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="flagproj")
    result = core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    return core, entry, project, reg, result


def _assert_index_effect(core, entry, result):
    """The acceptance effect of the store contract, not its absence.

    The rig's claim is "a registered, INDEXED project", and the pass reports
    itself through two observable facts: it returns ``state=idle`` and it asks
    the store to swap the identity-stamped collection onto the project's
    alias. Before this assertion existed, a store that could not swap left
    the manifest readable and every refusal test in this file passing.
    """
    assert result.get("state") == "idle", (
        f"the indexing pass did not finish for {entry.slug}: {result}")
    assert core.store.swaps, (
        "the indexing pass never swapped a collection onto the project alias")


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    """A registered, indexed project whose search runs without a service."""
    core, entry, project, reg, result = _build_rig(
        tmp_path, monkeypatch, _RecordingStore())
    _assert_index_effect(core, entry, result)
    yield core, entry, project
    reg.close()
    monkeypatch.undo()


def entry_path(rig) -> str:
    """The registered project's path, the selector every invocation passes."""
    return rig[1].path


@pytest.fixture()
def unregistered_project(tmp_path, monkeypatch):
    """A core over an empty root and a directory no registry knows.

    Nothing is registered, so resolving the project fails -- which is exactly
    the point: a value refused at the boundary must answer before the read
    that would fail, and the rig must not hide that by registering one.
    """
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    core = Core(cfg)
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    project = tmp_path / "unregistered"
    project.mkdir()
    return str(project)


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


def test_a_bad_format_is_refused_before_the_semantic_extension(monkeypatch):
    """The format is a request shape, not a fact about the install.

    Measured at the previous tip: exit 3 with the install message -- the
    extension line told the caller to install the extra for an invocation
    that only named a format the command does not have. The format is
    resolved before ``_require_semantic`` and ``_get_core``, so the answer
    is about the request.
    """
    monkeypatch.setattr(cli, "semantic_available", lambda: False)
    result = _refusal(["semantic-search", "x", "--format", "bogus"])
    assert "unknown --format value: 'bogus'" in result.stderr


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


def test_a_bad_visibility_value_is_a_usage_error(rig):
    result = _refusal(["find-symbol", "maybe_refresh", "--project",
                       entry_path(rig), "--visibility", "bogus"])
    assert "--visibility must be 'public' or 'private'" in result.stderr


def test_a_bad_visibility_is_refused_before_an_unregistered_project(rig,
                                                                    tmp_path):
    """The value is checked at the boundary, before any index read.

    Measured at the previous tip: exit 1, ``NotFoundError: project not
    registered`` -- a wrong invocation reported as a missing project, because
    the value was checked after ``_get_core``, ``resolve_entry``,
    ``_refuse_obsolete_rows`` and ``_read_staleness``.
    """
    unregistered = tmp_path / "unregistered"
    unregistered.mkdir()
    result = _refusal(["find-symbol", "maybe_refresh", "--project",
                       str(unregistered), "--visibility", "bogus"])
    assert "--visibility must be 'public' or 'private'" in result.stderr


def test_a_bad_context_selector_is_refused_before_an_unregistered_project(
        unregistered_project):
    """The selector is a request shape, not index state.

    Measured at the previous tip: exit 1, ``NotFoundError: project not
    registered`` -- the read's answer to an invocation that never named a
    usable range, because the shape was validated after ``_get_core``,
    ``_resolve``, ``_refuse_obsolete_rows`` and ``_read_staleness``.
    """
    result = _refusal(["get-code-context", "mod.py", "--project",
                       unregistered_project, "--start-line", "9",
                       "--end-line", "3"])
    assert "--end-line 3 is below --start-line 9" in result.stderr


def test_a_bad_eval_k_is_refused_before_an_unregistered_project(
        unregistered_project, tmp_path):
    """A malformed --k is the caller's mistake, refused before the core.

    Measured at the previous tip: exit 1 (``InternalError`` from the old
    helper, or ``NotFoundError`` once the project came first), and the
    parsing ran after ``_get_core`` and after a ``--refresh`` pass could run.
    """
    queries = tmp_path / "queries.jsonl"
    queries.write_text(QUERIES)
    result = _refusal(["eval", "--project", unregistered_project,
                       "--queries", str(queries), "--k", "zero"])
    assert "--k must be a comma-separated list of integers" in result.stderr


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


def test_a_refusing_store_makes_the_indexing_acceptance_fail(
        tmp_path, monkeypatch):
    """Control one: a store that refuses the swap must fail the rig.

    This passes only while ``_assert_index_effect`` is sensitive: the refusal
    leaves the pass in ``state=error`` with no recorded swap, so the very
    assertion the fixture makes fails. Before the assertion existed, this rig
    passed 16 of 16.
    """
    core, entry, _project, reg, result = _build_rig(
        tmp_path, monkeypatch, _RefusingStore())
    try:
        with pytest.raises(AssertionError, match="did not finish"):
            _assert_index_effect(core, entry, result)
    finally:
        reg.close()


def test_the_pre_fix_rig_fails_the_indexing_acceptance(tmp_path, monkeypatch):
    """Control two: the stub with no ``swap_collection`` must fail too.

    That is the rig the gate found passing 16 of 16 at the tip while logging
    a swallowed indexing error; under the effect assertion it cannot pass,
    which is what makes the acceptance half evidence rather than decoration.
    """
    core, entry, _project, reg, result = _build_rig(
        tmp_path, monkeypatch, _PreFixStore())
    try:
        with pytest.raises(AssertionError, match="did not finish"):
            _assert_index_effect(core, entry, result)
    finally:
        reg.close()
