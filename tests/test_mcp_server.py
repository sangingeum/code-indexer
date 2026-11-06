"""MCP surface tests: a narrowed, read-only view that relays honestly.

Every tool builds exactly one ``code-indexer`` subcommand's argv and relays
its result. No network: the runner seam (``server._run_cli``) is stubbed for
the mapping tests, and the end-to-end test drives the real CLI against a
throwaway ``INDEX_ROOT`` (registry-only — no Ollama/Qdrant traffic).

The surface is asserted as a SET, not as a count: the documented list is the
registry's, so a stale "all N tools" cannot recur.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import types

import pytest

from code_indexer import mcp_usage, server
from code_indexer.registry import Registry

# The documented read-only set. `semantic_search` is conditional on the
# semantic extra, which this environment has installed.
DOCUMENTED_TOOLS = frozenset({
    "list_projects", "overview", "skeleton", "find_symbol",
    "find_references", "find_callers", "get_code_context",
    "semantic_search",
})


@pytest.fixture()
def captured(monkeypatch, tmp_path):
    """Replace the runner seam: record argv, return a sentinel outcome."""
    calls: list[list[str]] = []
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))

    def fake(argv: list[str]) -> server.CliOutcome:
        calls.append(list(argv))
        return server.CliOutcome(stdout="SENTINEL", stderr="",
                                 returncode=0, duration_ms=1)

    monkeypatch.setattr(server, "_run_cli", fake)
    return calls


def _text(result) -> str:
    return result.content[0].text


# ---------------------------------------------------------------------------
# Tool -> CLI subcommand mapping (the whole point of the wrapper)
# ---------------------------------------------------------------------------

MAPPING_CASES: list[tuple[str, object, list[str]]] = [
    ("list_projects",
     lambda: server.list_projects(), ["list-projects"]),
    ("overview",
     lambda: server.overview(), ["overview"]),
    ("overview_full",
     lambda: server.overview(project="p", path_prefix="src", max_lines=20,
                             format="json"),
     ["overview", "--project", "p", "--path-prefix", "src",
      "--max-lines", "20", "--json"]),
    ("skeleton",
     lambda: server.skeleton(), ["skeleton"]),
    ("skeleton_full",
     lambda: server.skeleton(project="p", path_prefix="src", limit=3,
                             format="json"),
     ["skeleton", "--project", "p", "src", "--limit", "3", "--json"]),
    ("find_symbol",
     lambda: server.find_symbol("Core"), ["find-symbol", "Core"]),
    ("find_symbol_filtered",
     lambda: server.find_symbol("S", project="p", symbol_type="class",
                                fresh=True),
     ["find-symbol", "S", "--fresh", "--project", "p",
      "--symbol-type", "class"]),
    ("find_references",
     lambda: server.find_references("QTimer"),
     ["find-references", "QTimer", "--limit", "25"]),
    ("find_references_full",
     lambda: server.find_references("QTimer", project="p",
                                    relationship="calls", limit=7),
     ["find-references", "QTimer", "--project", "p",
      "--relationship", "calls", "--limit", "7"]),
    ("find_callers",
     lambda: server.find_callers("Core"),
     ["find-callers", "Core", "--depth", "2", "--max-nodes", "40"]),
    ("find_callers_json",
     lambda: server.find_callers("Core", project="p", depth=1, max_nodes=5,
                                 format="json"),
     ["find-callers", "Core", "--depth", "1", "--max-nodes", "5",
      "--project", "p", "--json"]),
    ("get_code_context_range",
     lambda: server.get_code_context("a.py", start_line=3, end_line=9),
     ["get-code-context", "a.py", "--start-line", "3", "--end-line", "9"]),
    ("get_code_context_symbol",
     lambda: server.get_code_context("a.py", project="p",
                                     symbol="Foo::bar", context_lines=2),
     ["get-code-context", "a.py", "--project", "p", "--symbol", "Foo::bar",
      "--context-lines", "2"]),
    ("semantic_search",
     lambda: server.semantic_search("auth refresh"),
     ["semantic-search", "auth refresh", "--limit", "8",
      "--mode", "hybrid"]),
    ("semantic_search_full",
     lambda: server.semantic_search(
         "auth", project="p", limit=3, file_filter="*.py",
         symbol_type="class", language="python", ranking="hybrid",
         format="json", mode="dense"),
     ["semantic-search", "auth", "--limit", "3", "--project", "p",
      "--file-filter", "*.py", "--symbol-type", "class",
      "--language", "python", "--ranking", "hybrid", "--mode", "dense",
      "--json"]),
]


@pytest.mark.parametrize("name,call,expected", MAPPING_CASES,
                         ids=[c[0] for c in MAPPING_CASES])
def test_tool_builds_cli_argv(captured, name, call, expected):
    result = call()
    assert captured == [expected], f"{name}: {captured}"
    assert _text(result) == "SENTINEL"
    assert result.isError is False


def test_dense_mode_reaches_the_cli(captured):
    """`mode="dense"` must reach argv, not fall back to the CLI's default.

    The wrapper's docstring advertises 'hybrid' | 'dense' | 'lexical', but the
    CLI's own default is hybrid: dropping `--mode` for one value made a client
    that asked for dense silently receive hybrid.
    """
    server.semantic_search("q", mode="dense")
    assert captured == [["semantic-search", "q", "--limit", "8",
                         "--mode", "dense"]]


def test_server_is_thread_free_and_core_free():
    """The wrapper model forbids hidden threads and its own core state."""
    assert not hasattr(server, "threading")
    assert not hasattr(server, "CORE")


# ---------------------------------------------------------------------------
# The documented set IS the registered set
# ---------------------------------------------------------------------------

def _registered() -> set[str]:
    return set(server.registered_tool_names())


def test_registered_tool_set_equals_the_documented_list():
    """The defect that produced "all 14 tools": a stale count in prose.

    This asserts the SET, both ways: nothing registered that is not
    documented, nothing documented that is not registered.
    """
    assert _registered() == DOCUMENTED_TOOLS


def test_removed_tools_are_absent_from_the_registry():
    assert _registered().isdisjoint(server.REMOVED_TOOLS)


def test_help_is_generated_from_the_registry():
    text = server.help_text()
    tools = asyncio.run(server.mcp.list_tools())
    assert f"registered tools ({len(tools)})" in text
    for tool in tools:
        assert tool.name in text
    for removed in server.REMOVED_TOOLS:
        assert removed in text
    assert "use the CLI subcommand of the same name" in text
    assert 'uvx --from "code-indexer[mcp]" code-indexer-mcp' in text


def test_without_the_semantic_extra_the_surface_has_seven_tools(tmp_path):
    """`semantic_search` is registered only when its extra is importable."""
    script = (
        "import importlib.util\n"
        "real = importlib.util.find_spec\n"
        "def fake(name, *a, **k):\n"
        "    if name in ('ollama', 'qdrant_client'):\n"
        "        return None\n"
        "    return real(name, *a, **k)\n"
        "importlib.util.find_spec = fake\n"
        "import code_indexer.server as s\n"
        "print(','.join(s.registered_tool_names()))\n"
        "print(s.help_text())\n"
    )
    proc = subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True, check=False,
                          cwd=str(tmp_path),
                          env={**os.environ, "INDEX_ROOT": str(tmp_path)})
    assert proc.returncode == 0, proc.stderr
    names = proc.stdout.splitlines()[0].split(",")
    assert set(names) == DOCUMENTED_TOOLS - {"semantic_search"}
    assert "semantic_search is not registered" in proc.stdout


# ---------------------------------------------------------------------------
# Annotations: reads only, and the hints are true of the relay
# ---------------------------------------------------------------------------

def test_every_tool_declares_all_four_annotation_hints_as_read_only():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == DOCUMENTED_TOOLS
    for tool in tools:
        annotations = tool.annotations
        assert annotations is not None, f"{tool.name}: no annotations"
        for field in ("readOnlyHint", "destructiveHint", "idempotentHint",
                      "openWorldHint"):
            value = getattr(annotations, field)
            assert isinstance(value, bool), (
                f"{tool.name}: {field} is {value!r}, not an explicit bool")
        assert annotations.readOnlyHint is True
        assert annotations.destructiveHint is False
        assert annotations.idempotentHint is True
        assert annotations.openWorldHint is False


# ---------------------------------------------------------------------------
# CLI resolution
# ---------------------------------------------------------------------------

def test_cli_argv_prefers_env_override(monkeypatch):
    monkeypatch.setenv("CODE_INDEXER_BIN", "/custom/code-indexer")
    assert server._cli_argv(["list-projects"]) == [
        "/custom/code-indexer", "list-projects"]


def test_cli_argv_uses_path_executable(monkeypatch):
    monkeypatch.delenv("CODE_INDEXER_BIN", raising=False)
    monkeypatch.setattr(server.shutil, "which", lambda name: "/bin/" + name)
    assert server._cli_argv(["list-projects"]) == [
        "/bin/code-indexer", "list-projects"]


def test_cli_argv_falls_back_to_module(monkeypatch):
    monkeypatch.delenv("CODE_INDEXER_BIN", raising=False)
    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    assert server._cli_argv(["list-projects"]) == [
        sys.executable, "-m", "code_indexer.cli", "list-projects"]


# ---------------------------------------------------------------------------
# The relay: two streams, one exit code
# ---------------------------------------------------------------------------

def _patch_run(monkeypatch, *, rc: int, out: str = "", err: str = ""):
    def fake_run(cmd, capture_output, text, check, env):
        assert capture_output and text and check is False
        assert isinstance(cmd, list)
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    monkeypatch.setattr(server.subprocess, "run", fake_run)


def test_success_returns_stdout_only(monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path))
    monkeypatch.setenv("CODE_INDEXER_BIN", "/custom/ci")
    _patch_run(monkeypatch, rc=0, out="registered path=/p\n",
               err="warning: stale\n")
    result = server.list_projects()
    assert _text(result) == "registered path=/p"
    assert result.isError is False
    assert result.structuredContent["exit_code"] == 0
    assert result.structuredContent["outcome"] == "ok"


def test_failure_returns_stderr_with_the_code_and_no_synthesis(
        monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path))
    _patch_run(monkeypatch, rc=3, out="stdout is not content on failure\n",
               err="BackendError: embedding service unavailable\n")
    result = server.find_symbol("Core")
    assert result.isError is True
    assert _text(result) == "BackendError: embedding service unavailable"
    assert result.structuredContent["exit_code"] == 3
    assert result.structuredContent["ErrorType"] == "BackendError"
    assert result.structuredContent["outcome"] == "error"


def test_silent_failure_stays_a_failure(monkeypatch, tmp_path):
    """No synthesized message replaces a nonzero exit (ADR-0007 section 2)."""
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path))
    _patch_run(monkeypatch, rc=4)
    result = server.list_projects()
    assert result.isError is True
    assert _text(result) == ""
    assert result.structuredContent["exit_code"] == 4


def test_silent_success_stays_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path))
    _patch_run(monkeypatch, rc=0)
    result = server.list_projects()
    assert result.isError is False
    assert _text(result) == ""


# ---------------------------------------------------------------------------
# Usage record: one line per invocation, never a reason to fail
# ---------------------------------------------------------------------------

def test_each_invocation_appends_one_line_without_arguments(
        monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "root"))
    _patch_run(monkeypatch, rc=0, out="ok\n")
    server.find_symbol("a_very_secret_identifier")
    server.find_symbol("another")
    lines = mcp_usage.read_invocations(str(tmp_path / "root"))
    assert len(lines) == 2
    assert [ln["tool"] for ln in lines] == ["find_symbol", "find_symbol"]
    for line in lines:
        assert set(line) == {"tool", "outcome", "exit_code",
                             "response_bytes", "duration_ms", "timestamp"}
    raw = (tmp_path / "root" / "mcp-usage.jsonl").read_text(encoding="utf-8")
    assert "a_very_secret_identifier" not in raw


def test_failed_record_write_does_not_fail_the_call(monkeypatch, tmp_path,
                                                    caplog):
    blocker = tmp_path / "root-file"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("INDEX_ROOT", str(blocker))
    _patch_run(monkeypatch, rc=0, out="answer\n")
    result = server.list_projects()
    assert result.isError is False
    assert _text(result) == "answer"


# ---------------------------------------------------------------------------
# End-to-end: the tool really runs the CLI (registry-only path, no network)
# ---------------------------------------------------------------------------

def test_list_projects_end_to_end_matches_cli(tmp_path, monkeypatch):
    index_root = tmp_path / "state"
    index_root.mkdir()
    monkeypatch.setenv("INDEX_ROOT", str(index_root))
    monkeypatch.delenv("CODE_INDEXER_BIN", raising=False)
    proj = tmp_path / "myproj"
    proj.mkdir()
    reg = Registry(str(index_root / "registry.db"))
    entry = reg.add(str(proj), name="myproj")
    reg.close()

    result = server.list_projects()
    assert result.isError is False
    assert f"path={proj}" in _text(result)
    assert f"slug={entry.slug}" in _text(result)
    # The record is written by the MCP process, not by the CLI read command.
    lines = mcp_usage.read_invocations(str(index_root))
    assert len(lines) == 1 and lines[0]["exit_code"] == 0


def test_missing_project_is_an_error_to_the_client(tmp_path, monkeypatch):
    """A real CLI failure arrives as isError with its code, not as success."""
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    monkeypatch.delenv("CODE_INDEXER_BIN", raising=False)
    result = server.get_code_context("nope.py", project="/not/registered")
    assert result.isError is True
    assert result.structuredContent["exit_code"] != 0
    assert result.structuredContent["ErrorType"] != "Unknown"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
