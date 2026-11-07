"""One output-format flag, one argument shape, across the read commands.

`--json` is an exact alias for `--format json`; `--format compact` exists
only where a one-line-per-record rendering is honest and is refused with the
formats that command does take everywhere else; giving both flags is a usage
error. The three symbol reads accept the symbol positionally AND through
`--symbol`, and `get-code-context --symbol NAME` with no file resolves the
symbol project-wide or refuses an ambiguous name with every candidate.

Every assertion here reads the real CLI through the same seam the survey
used, and the ten-invocation table pins the orientations that existed before
the round: none of them may change meaning.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.command_classes import (  # noqa: E402
    COMPACT_FORMAT_COMMANDS, READ_COMMANDS,
)
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()

MODULE = (
    "def helper(x):\n"
    '    """Add one."""\n'
    "    return x + 1\n"
    "\n"
    "\n"
    "def caller():\n"
    "    return helper(1)\n"
    "\n"
    "\n"
    "class Widget:\n"
    "    def render(self):\n"
    "        return helper(2)\n"
)

DUPLICATE = "def step():\n    return 1\n"

# A compact record: the file, the range, the name and the kind, on one line.
COMPACT_RECORD = re.compile(r"^[^ :]+:\d+-\d+ \S+ \(\w+\)$")


def _package_version() -> str | None:
    try:
        return importlib.metadata.version("code-indexer")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        return None


class StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]

    def embed_with_errors(self, texts):
        return [[0.1] * 4 for _ in texts], []

    def query_instruction_text(self, query: str) -> str:
        return query

    def close(self) -> None:
        pass


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

    def swap_collection(self, *a, **kw):
        return None


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    """One indexed project: a module with three symbols plus a duplicate."""
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    cli._core = None
    cli._core_skip = False
    cfg = Config(ollama_url="http://stub", qdrant_url="http://stub",
                 embed_model="stub", index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=1,
                 watch_sweep_interval=3600)
    core = Core(cfg)
    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    project = tmp_path / "proj"
    project.mkdir()
    (project / "mod.py").write_text(MODULE, encoding="utf-8")
    (project / "dup_a.py").write_text(DUPLICATE, encoding="utf-8")
    (project / "dup_b.py").write_text(DUPLICATE, encoding="utf-8")
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="proj")
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    yield core, reg, entry, project
    reg.close()
    cli._core = None
    cli._core_skip = False


def _run(project, *args):
    return runner.invoke(app, [*args, "--project", str(project)])


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------

def test_version_prints_the_installed_version_on_one_line():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert len(lines) == 1, lines
    expected = _package_version()
    if expected is None:  # pragma: no cover - installed in every test env
        assert lines[0].startswith("code-indexer ")
    else:
        assert lines[0] == f"code-indexer {expected}"


def test_version_needs_no_subcommand_and_no_project():
    # Eager: the callback runs before any command dispatch or project
    # resolution, so this cannot fail on a missing selector.
    result = runner.invoke(app, ["--version", "overview"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0].startswith("code-indexer ")


# ---------------------------------------------------------------------------
# --format json is --json, exactly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["overview"],
    ["index-status"],
    ["find-symbol", "helper"],
], ids=["overview", "index-status", "find-symbol"])
def test_format_json_equals_the_json_flag(rig, argv):
    _, _, _, project = rig
    by_flag = _run(project, *argv, "--json")
    by_format = _run(project, *argv, "--format", "json")
    assert by_flag.exit_code == 0, by_flag.output
    assert by_format.exit_code == 0, by_format.output
    assert by_format.stdout == by_flag.stdout


@pytest.mark.parametrize("argv", [
    ["overview"],
    ["find-symbol", "helper"],
    ["find-definition", "helper"],
    ["skeleton"],
], ids=["overview", "find-symbol", "find-definition", "skeleton"])
def test_plain_text_is_unchanged_by_the_flag_existing(rig, argv):
    _, _, _, project = rig
    plain = _run(project, *argv)
    explicit = _run(project, *argv, "--format", "text")
    assert plain.exit_code == explicit.exit_code == 0, plain.output
    assert explicit.stdout == plain.stdout


# ---------------------------------------------------------------------------
# compact, per command
# ---------------------------------------------------------------------------

def _compact(result) -> list[str]:
    assert result.exit_code == 0, result.output
    return result.stdout.splitlines()


def test_overview_compact_is_one_tagged_line_per_record(rig):
    _, _, _, project = rig
    lines = _compact(_run(project, "overview", "--format", "compact"))
    tags = {"project", "language", "directory", "entry-point", "tests",
            "test-file", "config", "hotspot-fan-in", "hotspot-size",
            "(truncated)"}
    first_tokens = {line.split()[0] for line in lines}
    assert first_tokens <= tags, first_tokens - tags
    assert lines[0].startswith("project ")
    # No header lines, no indentation: every line starts at column one.
    assert all(not line.startswith(" ") for line in lines)
    assert any(line.startswith("language python ") for line in lines)


def test_index_status_compact_is_its_own_single_line(rig):
    _, _, _, project = rig
    compact = _compact(_run(project, "index-status", "--format", "compact"))
    text = _compact(_run(project, "index-status"))
    assert len(text) == 1
    assert compact == text


def test_skeleton_compact_repeats_the_file_on_every_record(rig):
    _, _, _, project = rig
    lines = _compact(_run(project, "skeleton", "--format", "compact"))
    payload = json.loads(_run(project, "skeleton", "--json").stdout)
    expected = sum(len(f["symbols"]) for f in payload["files"])
    assert len(lines) == expected, lines
    assert all(COMPACT_RECORD.match(line) for line in lines), lines
    assert {line.split(":")[0] for line in lines} == {"mod.py", "dup_a.py",
                                                     "dup_b.py"}, lines
    assert "mod.py:1-3 helper (function)" in lines, lines


def test_outline_compact_is_one_record_per_declaration(rig):
    _, _, _, project = rig
    lines = _compact(_run(project, "outline", "mod.py",
                          "--format", "compact"))
    payload = json.loads(_run(project, "outline", "mod.py",
                              "--json").stdout)
    assert len(lines) == len(payload["declarations"])
    assert all(COMPACT_RECORD.match(line) for line in lines), lines
    assert lines == [
        "mod.py:1-3 helper (function)",
        "mod.py:6-7 caller (function)",
        "mod.py:10-12 Widget (class)",
        "mod.py:11-12 render (function)",
    ]


def test_find_symbol_compact_matches_its_text_records(rig):
    _, _, _, project = rig
    lines = _compact(_run(project, "find-symbol", "helper",
                          "--format", "compact"))
    assert lines == ["mod.py:1-3 helper (function)"]


def test_find_definition_compact_matches_its_text_records(rig):
    _, _, _, project = rig
    lines = _compact(_run(project, "find-definition", "helper",
                          "--format", "compact"))
    assert lines == ["mod.py:1-3 helper (function)"]


def test_skeleton_tree_compact_records_directories(rig):
    _, _, _, project = rig
    lines = _compact(_run(project, "skeleton", "--tree",
                          "--format", "compact"))
    assert lines, "the tree projection printed nothing"
    assert all(re.match(r"^\.?/?\S*/ symbols=\d+ dominant=\S+", line)
               for line in lines), lines


# ---------------------------------------------------------------------------
# the refusals: unknown format, unsupported compact, conflicting flags
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["find-references", "helper"],
    ["get-code-context", "mod.py"],
    ["changed-symbols"],
], ids=["find-references", "get-code-context", "changed-symbols"])
def test_compact_is_refused_where_there_is_no_compact_form(rig, argv):
    _, _, _, project = rig
    result = _run(project, *argv, "--format", "compact")
    assert result.exit_code == 2, result.output
    assert "no compact form" in result.stderr
    assert "supported formats: text|json" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("command", ["doctor", "list-projects"])
def test_compact_is_refused_on_a_command_with_no_project_selector(command):
    # The two reads that take no --project must refuse the same way, so the
    # refusal does not depend on the selector being present.
    result = runner.invoke(app, [command, "--format", "compact"])
    assert result.exit_code == 2, result.output
    assert "no compact form" in result.stderr
    assert result.stdout == ""


def test_unknown_format_value_is_a_usage_error(rig):
    _, _, _, project = rig
    result = _run(project, "overview", "--format", "yaml")
    assert result.exit_code == 2, result.output
    assert "unknown --format value" in result.stderr
    assert "text|json|compact" in result.stderr


@pytest.mark.parametrize("argv", [
    ["overview"],
    ["find-symbol", "helper"],
    ["semantic-search", "helper"],
], ids=["overview", "find-symbol", "semantic-search"])
def test_the_four_flag_combinations_agree_exactly_once(rig, argv):
    """One rule for the pair: accepted when it agrees, refused when it does not.

    `--json` alone, `--format json` alone and the agree-pair are one answer,
    byte for byte; `--format text` alone renders the other format; and
    `--json --format text|compact` is exit 2 naming the contradiction. A
    silent `--format` precedence turns the agreement into a different answer
    and the disagreement into exit 0, so both halves go red.
    """
    _, _, _, project = rig
    by_json = _run(project, *argv, "--json")
    by_format = _run(project, *argv, "--format", "json")
    agree = _run(project, *argv, "--json", "--format", "json")
    by_text = _run(project, *argv, "--format", "text")
    assert by_json.exit_code == 0, by_json.output
    assert by_json.stdout, "the stub rig produced no payload"
    assert by_format.exit_code == 0, by_format.output
    assert by_format.stdout == by_json.stdout
    assert agree.exit_code == 0, agree.output
    assert agree.stdout == by_json.stdout
    assert agree.stderr == by_json.stderr
    assert by_text.exit_code == 0, by_text.output
    assert by_text.stdout != by_json.stdout
    for other in ("text", "compact"):
        clash = _run(project, *argv, "--json", "--format", other)
        assert clash.exit_code == 2, clash.output
        assert f"--json and --format {other} disagree" in clash.stderr
        assert clash.stdout == ""


def test_the_compact_set_is_a_subset_of_the_read_commands():
    assert COMPACT_FORMAT_COMMANDS <= READ_COMMANDS


# ---------------------------------------------------------------------------
# one argument shape: get-code-context --symbol, no file
# ---------------------------------------------------------------------------

def test_symbol_only_context_resolves_the_symbol_project_wide(rig):
    _, _, _, project = rig
    result = _run(project, "get-code-context", "--symbol", "helper")
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == "--- mod.py:1-3 ---"
    assert "return x + 1" in result.stdout


def test_symbol_only_context_refuses_an_ambiguous_name_with_candidates(rig):
    _, _, _, project = rig
    result = _run(project, "get-code-context", "--symbol", "step")
    assert result.exit_code == 2, result.output
    assert "ArgumentError" in result.stderr
    # Every candidate is listed, not the first row that sorted.
    assert "dup_a.py:1-2" in result.stderr
    assert "dup_b.py:1-2" in result.stderr
    assert result.stdout == ""


def test_ambiguous_symbol_only_context_answers_json_with_the_candidates(rig):
    _, _, _, project = rig
    result = _run(project, "get-code-context", "--symbol", "step",
                  "--json")
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["ErrorType"] == "ArgumentError"
    assert {c["file"] for c in payload["candidates"]} == {"dup_a.py",
                                                          "dup_b.py"}


def test_context_with_neither_a_file_nor_a_symbol_is_a_usage_error(rig):
    _, _, _, project = rig
    result = _run(project, "get-code-context")
    assert result.exit_code == 2, result.output
    assert "FILE positional" in result.stderr


def test_the_file_positional_keeps_its_meaning(rig):
    _, _, _, project = rig
    result = _run(project, "get-code-context", "mod.py",
                  "--start-line", "1", "--end-line", "3")
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == "--- mod.py:1-3 ---"


@pytest.mark.parametrize("argv", [
    ["find-symbol", "--symbol", "helper"],
    ["find-definition", "--symbol", "helper"],
], ids=["find-symbol", "find-definition"])
def test_the_symbol_flag_is_the_positional_name(rig, argv):
    _, _, _, project = rig
    by_flag = _run(project, *argv)
    positional = _run(project, argv[0], "helper")
    assert by_flag.exit_code == 0, by_flag.output
    assert by_flag.stdout == positional.stdout


@pytest.mark.parametrize("command", ["find-symbol", "find-definition"])
def test_a_disagreeing_name_and_symbol_are_refused(rig, command):
    _, _, _, project = rig
    result = _run(project, command, "caller", "--symbol", "helper")
    assert result.exit_code == 2, result.output
    assert "disagree" in result.stderr


def test_find_definition_with_neither_a_name_nor_a_symbol_is_a_usage_error(
        rig):
    _, _, _, project = rig
    result = _run(project, "find-definition")
    assert result.exit_code == 2, result.output
    assert "NAME positional" in result.stderr


# ---------------------------------------------------------------------------
# the ten invocations: same answer as before the round
# ---------------------------------------------------------------------------

def _text_has(*needles):
    def check(result):
        assert result.exit_code == 0, result.output
        for needle in needles:
            assert needle in result.stdout, (needle, result.stdout)
    return check


def _json_has(*keys):
    def check(result):
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        for key in keys:
            assert key in payload, (key, sorted(payload))
    return check


@pytest.mark.parametrize("argv,check", [
    (["overview"], _text_has("project: ", "languages:", "hotspots")),
    (["overview", "--json"], _json_has("project", "files", "loc",
                                        "languages", "directories")),
    (["index-status"], _text_has("project=", "state=", "rows=", "schema=")),
    (["index-status", "--json"], _json_has("project", "state", "rows",
                                            "vectors", "schema")),
    (["skeleton"], _text_has("mod.py  (python, 4 symbols)",
                             "function helper:1-3")),
    (["outline", "mod.py"], _text_has("function helper:1-3",
                                       "class Widget:10-12")),
    (["find-symbol", "helper"],
     _text_has("mod.py:1-3 helper", "confidence=exact")),
    (["find-definition", "helper", "--json"],
     _json_has("schema", "name", "project", "definitions")),
    (["get-code-context", "mod.py", "--symbol", "helper"],
     _text_has("--- mod.py:1-3 ---", "return x + 1")),
    (["find-references", "helper", "--json"],
     _json_has("schema", "name", "references", "total")),
], ids=["overview", "overview-json", "index-status", "index-status-json",
        "skeleton", "outline", "find-symbol", "find-definition-json",
        "get-code-context", "find-references-json"])
def test_the_ten_invocations_keep_their_answer(rig, argv, check):
    _, _, _, project = rig
    check(_run(project, *argv))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
