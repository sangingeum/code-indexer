"""MCP server: a narrowed, read-only view of the code index.

Every tool is an adapter over exactly one CLI subcommand: it builds that
subcommand's argv, runs the CLI as a subprocess, and relays the result — so
the two surfaces can never diverge (same code path, same formatting, one
implementation). This module owns no registry, manifest or store access.

What the surface is
-------------------

The tools are the read-only query shapes: project listing, orientation,
structural lookup, reference and caller graphs, exact line reading, and
semantic search when the semantic extra is installed. Nothing here writes to
the index, registers a project or removes one. A tool's own description says
which CLI subcommand it wraps; `code-indexer-mcp --help` prints the
registered set and the removals, both generated from this module's registry
rather than from a hand-written count.

The relay (ADR-0007 section 2)
------------------------------

The CLI's two streams stay separate and its exit code is preserved:

- a successful call returns the CLI's **stdout** as its content;
- a failing call returns the CLI's **stderr** as its content, with
  ``isError`` set and the numeric code plus its ``ErrorType`` in the
  structured result;
- no message is synthesized over a nonzero exit — a failure is never
  reported as success, and an empty success stays empty.

Annotations
-----------
Every tool is a read: ``readOnlyHint=True`` is true of what a client sees
because reads no longer repair the index (ADR-0003) and this relay reports
failure as failure, so the hints are a claim that holds.
``idempotentHint=True`` (re-running converges), ``destructiveHint=False``,
``openWorldHint=False`` (the domain is the locally registered projects and
their derived index).

The CLI is resolved as: ``$CODE_INDEXER_BIN`` if set, else the
``code-indexer`` executable on ``PATH``, else ``python -m code_indexer.cli``.
The subprocess inherits the server's environment (``INDEX_ROOT``,
``OLLAMA_URL``, ``QDRANT_URL``, ``EMBED_MODEL``, ...).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from .config import load_config
from .extensions import semantic_available
from .mcp_usage import record_invocation

logger = logging.getLogger("code-indexer")

mcp = FastMCP("code-indexer")

# The tools this surface deliberately no longer carries. Named here so the
# help text can say what a caller lost and where to go instead, and so the
# list cannot drift from the removals a release note states.
REMOVED_TOOLS = (
    "add_project", "index_status", "lookup_project", "reindex_project",
    "remove_project", "changed_symbols", "deps", "find_callees",
    "find_definition", "find_symbols", "outline",
)

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True,
    openWorldHint=False,
)


@dataclass(frozen=True)
class CliOutcome:
    """One CLI invocation: the two streams kept apart, and the exit code."""

    stdout: str
    stderr: str
    returncode: int
    duration_ms: int


def _cli_argv(argv: list[str]) -> list[str]:
    """The command line for one CLI invocation (resolved lazily)."""
    override = os.environ.get("CODE_INDEXER_BIN")
    if override:
        return [override, *argv]
    exe = shutil.which("code-indexer")
    if exe:
        return [exe, *argv]
    return [sys.executable, "-m", "code_indexer.cli", *argv]


def _run_cli(argv: list[str]) -> CliOutcome:
    """Run one ``code-indexer`` subcommand; keep its streams and exit code."""
    cmd = _cli_argv(argv)
    logger.debug("mcp -> %s", cmd)
    started = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False,
                          env=os.environ.copy())
    return CliOutcome(stdout=proc.stdout, stderr=proc.stderr,
                      returncode=proc.returncode,
                      duration_ms=int((time.monotonic() - started) * 1000))


def _error_type(stderr: str) -> str:
    """The taxonomy name the CLI printed (``<ErrorType>: ...``), or Unknown."""
    head = stderr.strip().splitlines()[0] if stderr.strip() else ""
    name, sep, _rest = head.partition(":")
    if sep and name and " " not in name and name[:1].isupper():
        return name
    return "Unknown"


def _relay(tool: str, outcome: CliOutcome) -> CallToolResult:
    """One tool result from one CLI outcome: the streams never merge."""
    ok = outcome.returncode == 0
    body = (outcome.stdout if ok else outcome.stderr).strip()
    record_invocation(
        tool=tool,
        outcome="ok" if ok else "error",
        exit_code=outcome.returncode,
        response_bytes=len(body.encode("utf-8")),
        duration_ms=outcome.duration_ms,
        index_root=load_config().index_root,
    )
    structured: dict[str, object] = {
        "tool": tool,
        "outcome": "ok" if ok else "error",
        "exit_code": outcome.returncode,
        "response_bytes": len(body.encode("utf-8")),
        "duration_ms": outcome.duration_ms,
    }
    if not ok:
        structured["ErrorType"] = _error_type(outcome.stderr)
    return CallToolResult(
        content=[TextContent(type="text", text=body)],
        structuredContent=structured,
        isError=not ok,
    )


@mcp.tool(structured_output=False, annotations=_READ_ONLY)
def list_projects() -> CallToolResult:
    """List registered projects with their index summary (path, slug, state,
    files, chunks, last indexed, staleness). Reads the registry and the
    manifests only — no service contact. Wraps `code-indexer list-projects`."""
    return _relay("list_projects", _run_cli(["list-projects"]))


@mcp.tool(structured_output=False, annotations=_READ_ONLY)
def overview(project: str | None = None, path_prefix: str | None = None,
             max_lines: int = 60, format: str = "text") -> CallToolResult:
    """Project map for orientation: languages, directories, entry points,
    tests, config and hotspots, plus whether the index is fresh, stale or
    unknown. Wraps `code-indexer overview [--project P] [--path-prefix X]
    [--max-lines N] [--json]`. `format`: 'text' or 'json'."""
    argv = ["overview"]
    if project:
        argv += ["--project", project]
    if path_prefix:
        argv += ["--path-prefix", path_prefix]
    if max_lines != 60:
        argv += ["--max-lines", str(max_lines)]
    if format == "json":
        argv.append("--json")
    return _relay("overview", _run_cli(argv))


@mcp.tool(structured_output=False, annotations=_READ_ONLY)
def skeleton(project: str | None = None, path_prefix: str | None = None,
             limit: int | None = None, format: str = "text") -> CallToolResult:
    """Whole-project or per-subtree structural map from the manifest only:
    one file per group, one symbol per line with lines and the stored
    signature. Wraps `code-indexer skeleton [--project P] [PATH_PREFIX]
    [--limit N] [--json]`."""
    argv = ["skeleton"]
    if project:
        argv += ["--project", project]
    if path_prefix:
        argv.append(path_prefix)
    if limit is not None:
        argv += ["--limit", str(limit)]
    if format == "json":
        argv.append("--json")
    return _relay("skeleton", _run_cli(argv))


@mcp.tool(structured_output=False, annotations=_READ_ONLY)
def find_symbol(name: str, project: str | None = None,
                symbol_type: str | None = None,
                fresh: bool = False) -> CallToolResult:
    """Look up a symbol by name in the manifest symbol index (exact AST-first,
    capped substring fallback). Wraps `code-indexer find-symbol NAME
    [--project P] [--symbol-type T] [--fresh]`. `symbol_type`:
    function|method|class|struct|enum|namespace."""
    argv = ["find-symbol", name]
    if fresh:
        argv.append("--fresh")
    if project:
        argv += ["--project", project]
    if symbol_type:
        argv += ["--symbol-type", symbol_type]
    return _relay("find_symbol", _run_cli(argv))


@mcp.tool(structured_output=False, annotations=_READ_ONLY)
def find_references(name: str, project: str | None = None,
                    relationship: str | None = None, limit: int = 25,
                    fresh: bool = False) -> CallToolResult:
    """Find textual references TO a symbol: calls, inherits, includes (all
    confidence=heuristic — a navigation aid, not static analysis). Wraps
    `code-indexer find-references NAME [--project P] [--relationship R]
    [--limit N] [--fresh]`."""
    argv = ["find-references", name]
    if fresh:
        argv.append("--fresh")
    if project:
        argv += ["--project", project]
    if relationship:
        argv += ["--relationship", relationship]
    argv += ["--limit", str(limit)]
    return _relay("find_references", _run_cli(argv))


@mcp.tool(structured_output=False, annotations=_READ_ONLY)
def find_callers(name: str, project: str | None = None, depth: int = 2,
                 max_nodes: int = 40, format: str = "text") -> CallToolResult:
    """Who references this symbol — BFS over the reference tables (fan-in),
    depth- and node-capped, cycle-safe, with ast vs heuristic confidence
    markers. Wraps `code-indexer find-callers NAME [--project P] [--depth D]
    [--max-nodes N] [--json]`."""
    argv = ["find-callers", name, "--depth", str(depth),
            "--max-nodes", str(max_nodes)]
    if project:
        argv += ["--project", project]
    if format == "json":
        argv.append("--json")
    return _relay("find_callers", _run_cli(argv))


@mcp.tool(structured_output=False, annotations=_READ_ONLY)
def get_code_context(file: str, project: str | None = None,
                     start_line: int | None = None, end_line: int | None = None,
                     symbol: str | None = None, context_lines: int = 0,
                     fresh: bool = False) -> CallToolResult:
    """Retrieve ONLY the relevant source lines instead of reading whole
    files — by line range (`start_line`/`end_line`) or via `symbol`, padded by
    `context_lines`. Wraps `code-indexer get-code-context FILE [--project P]
    [--start-line N] [--end-line N] [--symbol S] [--context-lines N]
    [--fresh]`."""
    argv = ["get-code-context", file]
    if fresh:
        argv.append("--fresh")
    if project:
        argv += ["--project", project]
    if start_line is not None:
        argv += ["--start-line", str(start_line)]
    if end_line is not None:
        argv += ["--end-line", str(end_line)]
    if symbol:
        argv += ["--symbol", symbol]
    if context_lines:
        argv += ["--context-lines", str(context_lines)]
    return _relay("get_code_context", _run_cli(argv))


if semantic_available():
    @mcp.tool(structured_output=False, annotations=_READ_ONLY)
    def semantic_search(query: str, project: str | None = None,
                        limit: int = 8, file_filter: str | None = None,
                        symbol_type: str | None = None,
                        language: str | None = None,
                        ranking: str = "vector", format: str = "text",
                        mode: str = "hybrid") -> CallToolResult:
        """Semantic code search across indexed projects. Wraps
        `code-indexer semantic-search QUERY [--project P] [--limit N]
        [--file-filter GLOB] [--symbol-type T] [--language L]
        [--ranking M] [--mode D] [--json|--format compact]`.
        `ranking`: 'vector' | 'metadata' | 'hybrid';
        `mode`: 'hybrid' (dense + lexical) | 'dense' | 'lexical';
        `format`: 'text' | 'compact' | 'json'. A backend that cannot answer
        is an error with its exit code, never an empty success."""
        argv = ["semantic-search", query, "--limit", str(limit)]
        if project:
            argv += ["--project", project]
        if file_filter:
            argv += ["--file-filter", file_filter]
        if symbol_type:
            argv += ["--symbol-type", symbol_type]
        if language:
            argv += ["--language", language]
        if ranking and ranking != "vector":
            argv += ["--ranking", ranking]
        if mode != "dense":
            argv += ["--mode", mode]
        if format == "json":
            argv.append("--json")
        elif format == "compact":
            argv += ["--format", "compact"]
        return _relay("semantic_search", _run_cli(argv))


# ---------------------------------------------------------------------------
# generated help (ADR-0007 section 5)
# ---------------------------------------------------------------------------

def registered_tool_names() -> list[str]:
    """Every tool the registry currently holds, sorted."""
    return sorted(tool.name for tool in mcp._tool_manager.list_tools())


def help_text() -> str:
    """The surface as the registry holds it: no hand-written count to drift."""
    tools = sorted(mcp._tool_manager.list_tools(), key=lambda t: t.name)
    lines = [
        "code-indexer-mcp — MCP server over the code index (stdio transport).",
        "",
        f"registered tools ({len(tools)}):",
    ]
    for tool in tools:
        summary = (tool.description or "").strip().splitlines()
        lines.append(f"  {tool.name:<18} {summary[0] if summary else ''}")
    if not semantic_available():
        lines.append("")
        lines.append("semantic_search is not registered: the semantic extra "
                     "is not installed (code-indexer[semantic]).")
    lines.append("")
    lines.append("removed in this release — use the CLI subcommand of the "
                 "same name:")
    lines.append("  " + ", ".join(REMOVED_TOOLS))
    lines.append("")
    lines.append('install: uvx --from "code-indexer[mcp]" code-indexer-mcp')
    return "\n".join(lines)


def main() -> None:
    """Console-script entry point (``code-indexer-mcp``)."""
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        print(help_text())
        return
    from .logsetup import configure_logging

    configure_logging(verbose=os.environ.get("VERBOSE", "") not in ("", "0"))
    logger.info("code-indexer MCP starting (read-only tools over the CLI)")
    mcp.run(transport="stdio")
