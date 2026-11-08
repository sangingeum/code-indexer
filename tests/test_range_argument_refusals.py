"""The range selector of `get-code-context` is part of the taxonomy.

A caller who names a file but no range, a half range, a reversed range, a
symbol beside a range, or a range past the file's end has made a wrong
request: after the shared taxonomy that is `ArgumentError`, exit 2, one
stderr line and nothing on stdout. Before this file the same refusals fell
through the classifier's "everything else" branch and rendered
`InternalError`, so a bad invocation read exactly like a tool fault.

A file that is not there, and a path outside the project, are ruled the same
way in the module docstring of the command: not-found (exit 1) and usage
error (exit 2) respectively. The cases this file deliberately does NOT move
are listed in the commit that introduced it.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()

SOURCE = "def target_fn():\n    return 1\n\n\ndef other_fn():\n    return 2\n"


class StubEmbedder:
    def dimension(self) -> int:
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
        return []


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    import code_indexer.cli as cli

    cfg = Config(ollama_url="http://stub", qdrant_url="http://stub",
                 embed_model="stub", index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=1, watch_sweep_interval=3600)
    core = Core(cfg)
    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text(SOURCE)
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="rangeproj")
    core.run_index(entry.slug, str(project))
    cli._core = core
    cli._core_skip = False
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    yield cli, core, entry, project
    cli._core = None
    cli._core_skip = False
    reg.close()


def _usage_refusal(result) -> str:
    """A usage refusal: exit 2, no stdout, exactly one ArgumentError line."""
    assert result.exit_code == 2, (result.exit_code, result.output)
    assert result.stdout == "", result.stdout
    lines = [ln for ln in result.stderr.splitlines() if ln.strip()]
    assert len(lines) == 1, lines
    assert lines[0].startswith("ArgumentError: "), lines[0]
    assert not lines[0].startswith("InternalError"), lines[0]
    return lines[0]


def test_a_file_with_no_range_and_no_symbol_is_a_usage_error(rig):
    """The reproduced defect: this exact invocation exited 1, InternalError.

    A missing selector is the caller's mistake, so the coarse branch is 2 and
    the discriminator says ArgumentError -- never the class that means "the
    tool broke".
    """
    result = runner.invoke(app, ["get-code-context", "a.py",
                                 "--project", "rangeproj"])
    line = _usage_refusal(result)
    assert "provide --start-line/--end-line or --symbol" in line


def test_an_end_line_with_no_start_line_is_a_usage_error(rig):
    """Half a range names no start; the request is wrong, not the tool."""
    result = runner.invoke(app, ["get-code-context", "a.py",
                                 "--project", "rangeproj",
                                 "--end-line", "3"])
    line = _usage_refusal(result)
    assert "--end-line needs --start-line" in line


def test_an_end_line_below_its_start_line_is_a_usage_error(rig):
    """A reversed range answers nothing, so it must not read as success."""
    result = runner.invoke(app, ["get-code-context", "a.py",
                                 "--project", "rangeproj",
                                 "--start-line", "4", "--end-line", "2"])
    line = _usage_refusal(result)
    assert "below --start-line" in line


def test_a_symbol_given_beside_a_range_is_a_usage_error(rig):
    """Two selectors name two ranges; guessing one would answer silently."""
    result = runner.invoke(app, ["get-code-context", "a.py",
                                 "--project", "rangeproj",
                                 "--start-line", "1", "--end-line", "2",
                                 "--symbol", "target_fn"])
    line = _usage_refusal(result)
    assert "pass one selector" in line


def test_a_range_starting_past_the_end_of_the_file_is_a_usage_error(rig):
    """Six lines exist; asking for line 5000 is a wrong request."""
    result = runner.invoke(app, ["get-code-context", "a.py",
                                 "--project", "rangeproj",
                                 "--start-line", "5000",
                                 "--end-line", "5010"])
    line = _usage_refusal(result)
    assert "past the end of" in line


def test_a_path_outside_the_project_is_a_usage_error(rig):
    """The request named a file this project does not own."""
    result = runner.invoke(app, ["get-code-context", "../escape.py",
                                 "--project", "rangeproj",
                                 "--start-line", "1"])
    _usage_refusal(result)


def test_a_file_that_is_not_there_is_not_found_not_a_usage_error(rig):
    """Not-found is the documented exit 1 class, and it names itself."""
    result = runner.invoke(app, ["get-code-context", "nope.py",
                                 "--project", "rangeproj",
                                 "--start-line", "1"])
    assert result.exit_code == 1, (result.exit_code, result.output)
    assert result.stdout == "", result.stdout
    lines = [ln for ln in result.stderr.splitlines() if ln.strip()]
    assert len(lines) == 1, lines
    assert lines[0].startswith("NotFoundError: "), lines[0]


def test_a_lone_start_line_reads_that_one_line(rig):
    """The one accepted shorthand: --start-line alone is a one-line range.

    It is a complete request, so it stays a success -- the help says so.
    """
    result = runner.invoke(app, ["get-code-context", "a.py",
                                 "--project", "rangeproj",
                                 "--start-line", "2"])
    assert result.exit_code == 0, (result.exit_code, result.output)
    assert "--- a.py:2-2 ---" in result.stdout
    assert "return 1" in result.stdout
