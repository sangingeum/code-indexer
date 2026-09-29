"""MCP surface tests: the server is a thin wrapper over the CLI.

Every tool must build exactly one ``code-indexer`` subcommand's argv and relay
its output, so the MCP surface and the CLI can never diverge. No network: the
runner seam (``server._run_cli``) is stubbed for the mapping tests, and one
subprocess end-to-end test drives the real CLI against a throwaway
``INDEX_ROOT`` (registry-only — no Ollama/Qdrant traffic).
"""

from __future__ import annotations

import os
import sys
import types

import pytest

from code_indexer import server
from code_indexer.registry import Registry


@pytest.fixture()
def captured(monkeypatch):
    """Replace the runner seam: record argv, return a sentinel string."""
    calls: list[list[str]] = []

    def fake(argv: list[str]) -> str:
        calls.append(list(argv))
        return "SENTINEL"

    monkeypatch.setattr(server, "_run_cli", fake)
    return calls


# ---------------------------------------------------------------------------
# Tool -> CLI subcommand mapping (the whole point of the wrapper)
# ---------------------------------------------------------------------------

MAPPING_CASES: list[tuple[str, object, list[str]]] = [
    ("lookup_project",
     lambda: server.lookup_project("/p"), ["lookup-project", "/p"]),
    ("add_project",
     lambda: server.add_project("/p"), ["add-project", "/p"]),
    ("add_project_named",
     lambda: server.add_project("/p", name="myproj"),
     ["add-project", "/p", "--name", "myproj"]),
    ("add_project_name_ignored_when_empty",
     lambda: server.add_project("/p", name=""),
     ["add-project", "/p"]),
    ("remove_project",
     lambda: server.remove_project("/p"), ["remove-project", "/p"]),
    ("list_projects",
     lambda: server.list_projects(), ["list-projects"]),
    ("index_status",
     lambda: server.index_status("/p"), ["index-status", "/p"]),
    ("reindex_project",
     lambda: server.reindex_project("/p"), ["reindex-project", "/p"]),
    ("semantic_search_default",
     lambda: server.semantic_search("auth refresh"),
     ["semantic-search", "auth refresh", "--limit", "8"]),
    ("semantic_search_full",
     lambda: server.semantic_search(
         "auth", project="p", limit=3, file_filter="*.py",
         symbol_type="class", language="python", ranking="hybrid",
         format="json"),
     ["semantic-search", "auth", "--limit", "3", "--project", "p",
      "--file-filter", "*.py", "--symbol-type", "class",
      "--language", "python", "--ranking", "hybrid", "--json"]),
    ("semantic_search_vector_omits_ranking_flag",
     lambda: server.semantic_search("q", ranking="vector"),
     ["semantic-search", "q", "--limit", "8"]),
    ("find_symbol",
     lambda: server.find_symbol("FileTransferSession"),
     ["find-symbol", "FileTransferSession"]),
    ("find_symbol_filtered",
     lambda: server.find_symbol("S", project="p", symbol_type="class"),
     ["find-symbol", "S", "--project", "p", "--symbol-type", "class"]),
    ("find_symbols_browse",
     lambda: server.find_symbols(),
     ["find-symbol", "--limit", "25"]),
    ("find_symbols_filtered_json",
     lambda: server.find_symbols(project="p", symbol_type="method",
                                 file="a.py", limit=5, format="json"),
     ["find-symbol", "--project", "p", "--symbol-type", "method",
      "--file", "a.py", "--limit", "5", "--json"]),
    ("skeleton",
     lambda: server.skeleton(), ["skeleton"]),
    ("skeleton_full",
     lambda: server.skeleton(project="p", path_prefix="src", limit=3,
                             format="json"),
     ["skeleton", "--project", "p", "src", "--limit", "3", "--json"]),
    ("outline",
     lambda: server.outline("a.py"), ["outline", "a.py"]),
    ("outline_full",
     lambda: server.outline("a.py", project="p", docstrings=True,
                            format="json"),
     ["outline", "a.py", "--project", "p", "--docstrings", "--json"]),
    ("find_definition",
     lambda: server.find_definition("main"),
     ["find-definition", "main"]),
    ("find_definition_project",
     lambda: server.find_definition("main", project="p"),
     ["find-definition", "main", "--project", "p"]),
    ("find_references",
     lambda: server.find_references("QTimer"),
     ["find-references", "QTimer", "--limit", "25"]),
    ("find_references_full",
     lambda: server.find_references("QTimer", project="p",
                                    relationship="calls", limit=7),
     ["find-references", "QTimer", "--project", "p",
      "--relationship", "calls", "--limit", "7"]),
    ("get_code_context_range",
     lambda: server.get_code_context("a.py", start_line=3, end_line=9),
     ["get-code-context", "a.py", "--start-line", "3", "--end-line", "9"]),
    ("get_code_context_symbol",
     lambda: server.get_code_context("a.py", project="p",
                                     symbol="Foo::bar", context_lines=2),
     ["get-code-context", "a.py", "--project", "p", "--symbol", "Foo::bar",
      "--context-lines", "2"]),
]


@pytest.mark.parametrize("name,call,expected", MAPPING_CASES,
                         ids=[c[0] for c in MAPPING_CASES])
def test_tool_builds_cli_argv(captured, name, call, expected):
    assert call() == "SENTINEL"
    assert captured == [expected], f"{name}: {captured}"


def test_server_is_thread_free_and_core_free():
    """The wrapper model forbids hidden threads and its own core state."""
    assert not hasattr(server, "threading")
    assert not hasattr(server, "CORE")


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
# Output relay / exit codes (the _run_cli seam itself)
# ---------------------------------------------------------------------------

def _patch_run(monkeypatch, *, rc: int, out: str = "", err: str = ""):
    def fake_run(cmd, capture_output, text, check, env):
        assert capture_output and text and check is False
        assert isinstance(cmd, list)
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    monkeypatch.setattr(server.subprocess, "run", fake_run)


def test_run_cli_relays_stdout(monkeypatch):
    monkeypatch.setenv("CODE_INDEXER_BIN", "/custom/ci")
    _patch_run(monkeypatch, rc=0, out="registered path=/p\n")
    assert server._run_cli(["lookup-project", "/p"]) == "registered path=/p"


def test_run_cli_relays_stderr_on_failure(monkeypatch):
    monkeypatch.setenv("CODE_INDEXER_BIN", "/custom/ci")
    _patch_run(monkeypatch, rc=1, err="error: not registered: /p\n")
    assert server._run_cli(["lookup-project", "/p"]) == "error: not registered: /p"


def test_run_cli_reports_silent_failure(monkeypatch):
    monkeypatch.setenv("CODE_INDEXER_BIN", "/custom/ci")
    _patch_run(monkeypatch, rc=2)
    assert server._run_cli(["list-projects"]) == (
        "error: command failed with exit code 2")


def test_run_cli_ok_when_silent_success(monkeypatch):
    monkeypatch.setenv("CODE_INDEXER_BIN", "/custom/ci")
    _patch_run(monkeypatch, rc=0)
    assert server._run_cli(["list-projects"]) == "ok"


# ---------------------------------------------------------------------------
# End-to-end: the tool really runs the CLI (registry-only path, no network)
# ---------------------------------------------------------------------------

def test_lookup_project_end_to_end_matches_cli(tmp_path, monkeypatch):
    index_root = tmp_path / "state"
    index_root.mkdir()
    monkeypatch.setenv("INDEX_ROOT", str(index_root))
    monkeypatch.delenv("CODE_INDEXER_BIN", raising=False)
    proj = tmp_path / "myproj"
    proj.mkdir()
    reg = Registry(str(index_root / "registry.db"))
    entry = reg.add(str(proj), name="myproj")
    reg.close()

    out = server.lookup_project(str(proj))
    assert out.startswith("registered")
    assert f"path={proj}" in out
    assert f"slug={entry.slug}" in out
    assert f"collection=idx_{entry.slug}" in out
    assert "state=" in out and "files=" in out and "chunks=" in out
    assert "last_indexed=" in out

    nope = tmp_path / "nope"
    assert server.lookup_project(str(nope)) == f"not registered: {nope}"


def test_lookup_project_normalizes_symlink_and_trailing_slash(tmp_path,
                                                              monkeypatch):
    index_root = tmp_path / "state"
    index_root.mkdir()
    monkeypatch.setenv("INDEX_ROOT", str(index_root))
    monkeypatch.delenv("CODE_INDEXER_BIN", raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    reg = Registry(str(index_root / "registry.db"))
    reg.add(str(proj))
    reg.close()

    assert server.lookup_project(str(proj) + "/").startswith("registered")
    link = tmp_path / "link-to-proj"
    link.symlink_to(proj)
    assert server.lookup_project(str(link)).startswith("registered")


def test_run_cli_reports_unknown_subcommand_error(tmp_path, monkeypatch):
    """A real CLI failure surfaces as an ``error:`` string, never an exception."""
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    monkeypatch.delenv("CODE_INDEXER_BIN", raising=False)
    out = server.lookup_project("/definitely/not/registered")
    assert out.startswith("not registered: ") or out.startswith("error:")
    assert os.sep in out


# ---------------------------------------------------------------------------
# Tool annotations (audit finding: all four hints must be declared explicitly)
# ---------------------------------------------------------------------------

# The full expected surface: tool name -> the four hint booleans
# (readOnlyHint, destructiveHint, idempotentHint, openWorldHint).
# readOnlyHint is False wherever the wrapped CLI can write — including the
# staleness-triggered incremental index pass on the query path (see
# server.py's module docstring); it is True only for the three tools that
# never run that probe.
EXPECTED_ANNOTATIONS: dict[str, tuple[bool, bool, bool, bool]] = {
    "lookup_project": (True, False, True, False),
    "add_project": (False, False, True, False),
    "remove_project": (False, True, True, False),
    "list_projects": (True, False, True, False),
    "semantic_search": (False, False, True, False),
    "index_status": (True, False, True, False),
    "reindex_project": (False, False, True, False),
    "find_symbol": (False, False, True, False),
    "find_symbols": (False, False, True, False),
    "skeleton": (False, False, True, False),
    "outline": (False, False, True, False),
    "find_definition": (False, False, True, False),
    "find_references": (False, False, True, False),
    "get_code_context": (False, False, True, False),
}


def _list_tools():
    """Every registered tool (sync registry — works for mcp 1.x)."""
    return server.mcp._tool_manager.list_tools()


def test_every_tool_declares_all_four_annotation_hints():
    """No tool may leave a hint unset or non-boolean — OpenAI's directory
    rejects a tool with a missing hint."""
    tools = _list_tools()
    assert len(tools) == 14, f"expected 14 tools, got {len(tools)}"
    for tool in tools:
        annotations = tool.annotations
        assert annotations is not None, f"{tool.name}: no annotations"
        for field in ("readOnlyHint", "destructiveHint", "idempotentHint",
                      "openWorldHint"):
            value = getattr(annotations, field)
            assert isinstance(value, bool), (
                f"{tool.name}: {field} is {value!r}, not an explicit bool")


def test_tool_annotation_values_match_handler_behaviour():
    """The hint values must match the wrapped CLI semantics."""
    tools = {t.name: t for t in _list_tools()}
    assert set(tools) == set(EXPECTED_ANNOTATIONS)
    for name, expected in EXPECTED_ANNOTATIONS.items():
        annotations = tools[name].annotations
        assert annotations is not None, f"{name}: no annotations"
        got = (annotations.readOnlyHint, annotations.destructiveHint,
               annotations.idempotentHint, annotations.openWorldHint)
        assert got == expected, f"{name}: {got} != {expected}"


def test_only_remove_project_is_destructive():
    destructive = [t.name for t in _list_tools()
                   if t.annotations is not None and t.annotations.destructiveHint]
    assert destructive == ["remove_project"]


def test_list_tools_serializes_annotations():
    """Smoke check: the list_tools() payload carries the hints on the wire."""
    import asyncio
    import json

    tools = asyncio.run(server.mcp.list_tools())
    assert len(tools) == 14
    for tool in tools:
        payload = json.loads(tool.model_dump_json(exclude_none=False))
        annotations = payload["annotations"]
        for field in ("readOnlyHint", "destructiveHint", "idempotentHint",
                      "openWorldHint"):
            assert isinstance(annotations[field], bool), (
                f"{tool.name}: {field} missing from serialized payload")