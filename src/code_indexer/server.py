"""FastMCP server: a thin wrapper over the ``code-indexer`` CLI.

Every MCP tool is an adapter over exactly one CLI subcommand. The tool builds
that subcommand's argv and runs it (subprocess), returning the CLI's own
output — so the MCP surface and the CLI can never diverge: same code path,
same formatting, same foreground semantics, one implementation.

Consequences of the wrapper model (deliberate, matching the CLI):

- **Foreground everywhere.** The CLI's ``add-project`` / ``reindex-project``
  run the index pass in the foreground; so do the tools here — the call
  returns when indexing finishes. No background threads, no hidden work.
- **No duplicated logic.** This module owns no registry/index/store access;
  it only builds argv and relays output.
- **No ``watch`` tool.** A watcher makes no sense inside a long-lived MCP
  server, and the CLI already refuses to be both; ``watch``/``unwatch`` are
  CLI-only.

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

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("code-indexer")

mcp = FastMCP("code-indexer")


def _cli_argv(argv: list[str]) -> list[str]:
    """The command line for one CLI invocation (resolved lazily)."""
    override = os.environ.get("CODE_INDEXER_BIN")
    if override:
        return [override, *argv]
    exe = shutil.which("code-indexer")
    if exe:
        return [exe, *argv]
    return [sys.executable, "-m", "code_indexer.cli", *argv]


def _run_cli(argv: list[str]) -> str:
    """Run one ``code-indexer`` subcommand and return its combined output.

    stdout and stderr are both relayed, so the CLI's own error messages
    (``error: ...``) reach the caller. A nonzero exit with no output is
    reported explicitly rather than silently.
    """
    cmd = _cli_argv(argv)
    logger.debug("mcp -> %s", cmd)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False,
                          env=os.environ.copy())
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        return out or f"error: command failed with exit code {proc.returncode}"
    return out or "ok"


@mcp.tool()
def lookup_project(path: str) -> str:
    """Check whether a project directory is registered — no side effects
    (does NOT register or index). Thin wrapper over
    `code-indexer lookup-project <path>`; one-shot, foreground. Paths are
    normalized (tilde, relative, trailing slash, symlink)."""
    return _run_cli(["lookup-project", path])


@mcp.tool()
def add_project(path: str, name: str | None = None) -> str:
    """Register a project directory for semantic indexing (idempotent) and run
    the initial index pass — FOREGROUND: unlike the old MCP tool, this call
    blocks until indexing finishes. Wraps
    `code-indexer add-project <path> [--name NAME]`; optional NAME picks the
    collection name (`idx_<name>`) instead of the auto hash slug."""
    argv = ["add-project", path]
    if name:
        argv += ["--name", name]
    return _run_cli(argv)


@mcp.tool()
def remove_project(path: str) -> str:
    """Deregister a project and DELETE its Qdrant collection, SQLite manifest,
    and registry entry entirely. Thin wrapper over
    `code-indexer remove-project <path>`; one-shot, foreground. DESTRUCTIVE."""
    return _run_cli(["remove-project", path])


@mcp.tool()
def list_projects() -> str:
    """List registered projects with per-project index summary (path, slug,
    state, files, chunks, last_indexed). Thin wrapper over
    `code-indexer list-projects`; one-shot, foreground."""
    return _run_cli(["list-projects"])


@mcp.tool()
def semantic_search(query: str, project: str | None = None, limit: int = 8,
                    file_filter: str | None = None,
                    symbol_type: str | None = None,
                    language: str | None = None,
                    ranking: str = "vector",
                    format: str = "text") -> str:
    """Semantic code search across indexed projects. Thin wrapper over
    `code-indexer semantic-search QUERY [--project P] [--limit N]
    [--file-filter GLOB] [--symbol-type T] [--language L] [--ranking M]
    [--json]`; one-shot, foreground.

    The staleness probe runs only when the index is actually stale (quietly).
    `project` accepts a path, slug, or registered custom name; omitted searches
    every registered project. `ranking`: 'vector' (pure cosine, default) |
    'metadata' (definition boost / test-path penalty) | 'hybrid' (cosine fused
    with lexical token overlap — better for exact-identifier queries).
    `format`: 'text' or 'json' (the JSON field contract is the CLI's:
    project, file, score, symbol, symbol_type, lang, start_line, end_line,
    snippet).
    """
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
    if format == "json":
        argv += ["--json"]
    return _run_cli(argv)


@mcp.tool()
def index_status(path: str) -> str:
    """Report indexing state for a project: idle | indexing | stale | error,
    plus last-pass progress. Thin wrapper over
    `code-indexer index-status <path>`; one-shot, foreground. Informational
    only — like the CLI, this does NOT trigger a re-index."""
    return _run_cli(["index-status", path])


@mcp.tool()
def reindex_project(path: str) -> str:
    """Force a full rebuild of a project's index — FOREGROUND: this call blocks
    until the rebuild finishes (no background thread). Thin wrapper over
    `code-indexer reindex-project <path>`."""
    return _run_cli(["reindex-project", path])


@mcp.tool()
def find_symbol(name: str, project: str | None = None,
                symbol_type: str | None = None) -> str:
    """Look up symbols by name in the manifest symbol index (no semantic
    search): exact AST-first, capped substring fallback. Thin wrapper over
    `code-indexer find-symbol NAME [--project P] [--symbol-type T]`;
    one-shot, foreground. `symbol_type`:
    function|method|class|struct|enum|namespace."""
    argv = ["find-symbol", name]
    if project:
        argv += ["--project", project]
    if symbol_type:
        argv += ["--symbol-type", symbol_type]
    return _run_cli(argv)


@mcp.tool()
def find_symbols(project: str | None = None, symbol_type: str | None = None,
                 file: str | None = None, limit: int = 25,
                 format: str = "text") -> str:
    """Browse-mode symbol listing: no name, filter the manifest symbol index by
    type/file, capped at `limit` (default 25). Thin wrapper over
    `code-indexer find-symbol [--project P] [--symbol-type T] [--file F]
    [--limit N] [--json]`; one-shot, foreground. Signatures included when
    stored (schema v3)."""
    argv = ["find-symbol"]
    if project:
        argv += ["--project", project]
    if symbol_type:
        argv += ["--symbol-type", symbol_type]
    if file:
        argv += ["--file", file]
    argv += ["--limit", str(limit)]
    if format == "json":
        argv += ["--json"]
    return _run_cli(argv)


@mcp.tool()
def skeleton(project: str | None = None, path_prefix: str | None = None,
             limit: int | None = None, format: str = "text") -> str:
    """Whole-project or per-subtree structural map from the manifest only: one
    file per group, one symbol per line with lines and the stored signature.
    Thin wrapper over `code-indexer skeleton [--project P] [PATH_PREFIX]
    [--limit N] [--json]`; one-shot, foreground. `path_prefix` restricts to
    files under a project-relative path."""
    argv = ["skeleton"]
    if project:
        argv += ["--project", project]
    if path_prefix:
        argv += [path_prefix]
    if limit is not None:
        argv += ["--limit", str(limit)]
    if format == "json":
        argv += ["--json"]
    return _run_cli(argv)


@mcp.tool()
def outline(file: str, project: str | None = None,
            docstrings: bool = False, format: str = "text") -> str:
    """One file: declarations, signatures, one-line docstrings. Thin wrapper
    over `code-indexer outline FILE [--project P] [--docstrings] [--json]`;
    one-shot, foreground. `file` is project-relative (or absolute — the
    project root prefix is stripped)."""
    argv = ["outline", file]
    if project:
        argv += ["--project", project]
    if docstrings:
        argv += ["--docstrings"]
    if format == "json":
        argv += ["--json"]
    return _run_cli(argv)


@mcp.tool()
def find_definition(name: str, project: str | None = None) -> str:
    """Find where a symbol is declared (exact name match only, no substring
    fallback). Thin wrapper over
    `code-indexer find-definition NAME [--project P]`; one-shot, foreground."""
    argv = ["find-definition", name]
    if project:
        argv += ["--project", project]
    return _run_cli(argv)


@mcp.tool()
def find_references(name: str, project: str | None = None,
                    relationship: str | None = None, limit: int = 25) -> str:
    """Find textual references TO a symbol: calls, inherits, includes (all
    confidence=heuristic — a navigation aid, not static analysis). Thin
    wrapper over `code-indexer find-references NAME [--project P]
    [--relationship R] [--limit N]`; one-shot, foreground."""
    argv = ["find-references", name]
    if project:
        argv += ["--project", project]
    if relationship:
        argv += ["--relationship", relationship]
    argv += ["--limit", str(limit)]
    return _run_cli(argv)


@mcp.tool()
def get_code_context(file: str, project: str | None = None,
                     start_line: int | None = None, end_line: int | None = None,
                     symbol: str | None = None, context_lines: int = 0) -> str:
    """Retrieve ONLY the relevant source lines instead of reading whole files —
    by line range (`start_line`/`end_line`) or via a symbol, padded by
    `context_lines`. Thin wrapper over `code-indexer get-code-context FILE
    [--project P] [--start-line N] [--end-line N] [--symbol S]
    [--context-lines N]`; one-shot, foreground."""
    argv = ["get-code-context", file]
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
    return _run_cli(argv)


def main() -> None:
    """Console-script entry point (``code-indexer-mcp``)."""
    from .logsetup import configure_logging

    configure_logging(verbose=os.environ.get("VERBOSE", "") not in ("", "0"))
    logger.info("code-indexer MCP starting (thin wrapper over the CLI)")
    mcp.run(transport="stdio")