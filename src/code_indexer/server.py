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

Tool annotation policy (audit finding: OpenAI's directory rejects a tool
whose hints are not all four declared explicitly)
-----------------------------------------------------------------------
Every tool declares all four MCP hints as explicit booleans via
``ToolAnnotations`` (``mcp.types``): ``readOnlyHint``, ``destructiveHint``,
``idempotentHint``, ``openWorldHint``. The values below track the *actual
handler behaviour* of the wrapped CLI subcommand, not the intuition that a
"search" tool must be read-only. Two behaviours matter:

1. **The staleness probe writes.** Unless ``--skip-stale-check`` is passed,
   the query path runs ``Core.maybe_refresh`` — an *incremental index pass*
   that re-embeds changed files and rewrites the Qdrant collection and the
   SQLite manifest — whenever the index is past ``STALE_TTL``. In the CLI
   this fires for more than just search: ``core.search_for_display``
   (semantic-search), ``core.skeleton``, ``core.outline``, and the explicit
   ``core.maybe_refresh`` calls in the ``find-symbol`` / ``find-definition``
   / ``find-references`` / ``get-code-context --symbol`` command bodies. So
   those tools *can modify the environment* (the derived index), and are
   therefore ``readOnlyHint=False`` — the flag means "does not modify its
   environment", and the index is part of it. The write is not lossy and not
   answer-changing: it is ``destructiveHint=False`` (a content-identical
   rebuild of a derived cache; the registered source tree is never touched)
   and ``idempotentHint=True`` (re-running converges — a second call on a
   now-fresh index performs no work).
   ``lookup-project``, ``list-projects``, and ``index-status`` never run the
   probe (the MCP ``index_status`` tool passes no ``--refresh``, so it is
   informational only, exactly as its CLI docstring states) and are the only
   ``readOnlyHint=True`` tools.
2. **One ``openWorldHint`` call for the whole surface:
   ``openWorldHint=False``.** The tools' interaction domain is the set of
   *locally registered projects and their derived index* — a closed, known
   domain, like a memory tool rather than a web-search tool. The Ollama
   embedder and Qdrant store are the tool's own persistence/embedding
   infrastructure (configurable endpoints, the same way a database-backed
   tool is configured), not an open-ended universe of external entities: no
   tool reaches arbitrary external systems or the internet. Subprocess /
   env reachability was considered and rejected as a reason to mark the
   surface open, so that the call stays consistent across all 14 tools.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

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


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True,
    openWorldHint=False))
def lookup_project(path: str) -> str:
    """Check whether a project directory is registered — no side effects
    (does NOT register or index). Thin wrapper over
    `code-indexer lookup-project <path>`; one-shot, foreground. Paths are
    normalized (tilde, relative, trailing slash, symlink).

    readOnlyHint=True: reads only the registry; never runs the staleness
    probe, so it cannot write."""
    return _run_cli(["lookup-project", path])


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True,
    openWorldHint=False))
def add_project(path: str, name: str | None = None,
                allow_sensitive: bool = False) -> str:
    """Register a project directory for semantic indexing (idempotent) and run
    the initial index pass — FOREGROUND: unlike the old MCP tool, this call
    blocks until indexing finishes. Wraps
    `code-indexer add-project <path> [--name NAME] [--allow-sensitive]`;
    optional NAME picks the collection name (`idx_<name>`) instead of the
    auto hash slug. Secret-bearing files (.env*, keys, credentials*, and
    files matching high-confidence secret content patterns) are skipped by
    default; allow_sensitive=True indexes them too (stored per project).

    readOnlyHint=False: registers and indexes (writes). destructiveHint=False
    (adds, never deletes). idempotentHint=True: re-adding a registered path is
    a no-op ("already registered")."""
    argv = ["add-project", path]
    if name:
        argv += ["--name", name]
    if allow_sensitive:
        argv += ["--allow-sensitive"]
    return _run_cli(argv)


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True,
    openWorldHint=False))
def remove_project(path: str) -> str:
    """Deregister a project and DELETE its Qdrant collection, SQLite manifest,
    and registry entry entirely. Thin wrapper over
    `code-indexer remove-project <path>`; one-shot, foreground. DESTRUCTIVE.

    readOnlyHint=False; destructiveHint=True (drops the collection, manifest,
    and registry entry). idempotentHint=True: removing an already-removed
    project leaves the state unchanged (no further side effect)."""
    return _run_cli(["remove-project", path])


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True,
    openWorldHint=False))
def list_projects() -> str:
    """List registered projects with per-project index summary (path, slug,
    state, files, chunks, last_indexed). Thin wrapper over
    `code-indexer list-projects`; one-shot, foreground.

    readOnlyHint=True: reads the registry only; never runs the staleness
    probe, so it cannot write."""
    return _run_cli(["list-projects"])


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True,
    openWorldHint=False))
def semantic_search(query: str, project: str | None = None, limit: int = 8,
                    file_filter: str | None = None,
                    symbol_type: str | None = None,
                    language: str | None = None,
                    ranking: str = "vector",
                    format: str = "text", fresh: bool = False,
                    per_file: int = 0, max_tokens: int | None = None) -> str:
    """Semantic code search across indexed projects. Thin wrapper over
    `code-indexer semantic-search QUERY [--project P] [--limit N]
    [--file-filter GLOB] [--symbol-type T] [--language L] [--ranking M]
    [--json]`; one-shot, foreground.

    The staleness probe runs only when the index is actually stale (quietly).
    `project` accepts a path, slug, or registered custom name; omitted searches
    every registered project. `ranking`: 'vector' (pure cosine, default) |
    'metadata' (definition boost / test-path penalty) | 'hybrid' (cosine fused
    with lexical token overlap — better for exact-identifier queries).
    `format`: 'text' (verbose), 'compact' (one line per hit:
    path:start-end  symbol  score — the token-budget default for agents), or
    'json' (the JSON contract: {"hits": [...], "truncated": bool,
    "dropped": int}). `per_file` caps hits per file (0 = no generic cap);
    `max_tokens` trims the lowest-ranked hits to an approximate token budget
    (chars/4). Multi-project searches fuse per-collection lists with RRF.

    readOnlyHint=False: a stale index triggers `Core.maybe_refresh`, an
    incremental re-embed that writes the derived index. destructiveHint=False
    (content-identical rebuild, source untouched). idempotentHint=True."""
    argv = ["semantic-search", query, "--limit", str(limit)]
    if fresh:
        argv += ["--fresh"]
    if per_file:
        argv += ["--per-file", str(per_file)]
    if max_tokens is not None:
        argv += ["--max-tokens", str(max_tokens)]
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
    elif format == "compact":
        argv += ["--format", "compact"]
    return _run_cli(argv)


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True,
    openWorldHint=False))
def index_status(path: str) -> str:
    """Report indexing state for a project: idle | indexing | stale | error,
    plus last-pass progress. Thin wrapper over
    `code-indexer index-status <path>`; one-shot, foreground. Informational
    only — like the CLI, this does NOT trigger a re-index.

    readOnlyHint=True: the CLI runs the staleness pass only under `--refresh`,
    which this tool never passes, so it reads state and writes nothing."""
    return _run_cli(["index-status", path])


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True,
    openWorldHint=False))
def reindex_project(path: str) -> str:
    """Force a full rebuild of a project's index — FOREGROUND: this call blocks
    until the rebuild finishes (no background thread). Thin wrapper over
    `code-indexer reindex-project <path>`.

    readOnlyHint=False (writes the index). destructiveHint=False: the rebuild
    is content-identical, not lossy. idempotentHint=True: rebuilding an
    unchanged project converges to the same index."""
    return _run_cli(["reindex-project", path])


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True,
    openWorldHint=False))
def find_symbol(name: str, project: str | None = None,
                symbol_type: str | None = None,
                fresh: bool = False) -> str:
    """Look up symbols by name in the manifest symbol index (no semantic
    search): exact AST-first, capped substring fallback. Thin wrapper over
    `code-indexer find-symbol NAME [--project P] [--symbol-type T]`;
    one-shot, foreground. `symbol_type`:
    function|method|class|struct|enum|namespace.

    readOnlyHint=False: the CLI body calls `Core.maybe_refresh` before the
    manifest read, so a stale index is incrementally re-indexed (writes).
    destructiveHint=False; idempotentHint=True."""
    argv = ["find-symbol", name]
    if fresh:
        argv += ["--fresh"]
    if project:
        argv += ["--project", project]
    if symbol_type:
        argv += ["--symbol-type", symbol_type]
    return _run_cli(argv)


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True,
    openWorldHint=False))
def find_symbols(project: str | None = None, symbol_type: str | None = None,
                 file: str | None = None, limit: int = 25,
                 format: str = "text", fresh: bool = False) -> str:
    """Browse-mode symbol listing: no name, filter the manifest symbol index by
    type/file, capped at `limit` (default 25). Thin wrapper over
    `code-indexer find-symbol [--project P] [--symbol-type T] [--file F]
    [--limit N] [--json]`; one-shot, foreground. Signatures included when
    stored (schema v3).

    readOnlyHint=False: same `find-symbol` CLI body as `find_symbol`, which
    calls `Core.maybe_refresh` (a stale index is incrementally re-indexed).
    destructiveHint=False; idempotentHint=True."""
    argv = ["find-symbol"]
    if fresh:
        argv += ["--fresh"]
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


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True,
    openWorldHint=False))
def skeleton(project: str | None = None, path_prefix: str | None = None,
             limit: int | None = None, format: str = "text",
             fresh: bool = False) -> str:
    """Whole-project or per-subtree structural map from the manifest only: one
    file per group, one symbol per line with lines and the stored signature.
    Thin wrapper over `code-indexer skeleton [--project P] [PATH_PREFIX]
    [--limit N] [--json]`; one-shot, foreground. `path_prefix` restricts to
    files under a project-relative path.

    readOnlyHint=False: `Core.skeleton` runs `Core.maybe_refresh` first, so a
    stale index is incrementally re-indexed (writes). destructiveHint=False;
    idempotentHint=True."""
    argv = ["skeleton"]
    if fresh:
        argv += ["--fresh"]
    if project:
        argv += ["--project", project]
    if path_prefix:
        argv += [path_prefix]
    if limit is not None:
        argv += ["--limit", str(limit)]
    if format == "json":
        argv += ["--json"]
    return _run_cli(argv)


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True,
    openWorldHint=False))
def outline(file: str, project: str | None = None,
            docstrings: bool = False, format: str = "text",
            fresh: bool = False) -> str:
    """One file: declarations, signatures, one-line docstrings. Thin wrapper
    over `code-indexer outline FILE [--project P] [--docstrings] [--json]`;
    one-shot, foreground. `file` is project-relative (or absolute — the
    project root prefix is stripped).

    readOnlyHint=False: `Core.outline` runs `Core.maybe_refresh` first, so a
    stale index is incrementally re-indexed (writes). destructiveHint=False;
    idempotentHint=True."""
    argv = ["outline", file]
    if fresh:
        argv += ["--fresh"]
    if project:
        argv += ["--project", project]
    if docstrings:
        argv += ["--docstrings"]
    if format == "json":
        argv += ["--json"]
    return _run_cli(argv)


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True,
    openWorldHint=False))
def find_definition(name: str, project: str | None = None,
                    fresh: bool = False) -> str:
    """Find where a symbol is declared (exact name match only, no substring
    fallback). Thin wrapper over
    `code-indexer find-definition NAME [--project P]`; one-shot, foreground.

    readOnlyHint=False: the CLI body calls `Core.maybe_refresh` before the
    manifest read, so a stale index is incrementally re-indexed (writes).
    destructiveHint=False; idempotentHint=True."""
    argv = ["find-definition", name]
    if fresh:
        argv += ["--fresh"]
    if project:
        argv += ["--project", project]
    return _run_cli(argv)


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True,
    openWorldHint=False))
def find_references(name: str, project: str | None = None,
                    relationship: str | None = None, limit: int = 25,
                    fresh: bool = False) -> str:
    """Find textual references TO a symbol: calls, inherits, includes (all
    confidence=heuristic — a navigation aid, not static analysis). Thin
    wrapper over `code-indexer find-references NAME [--project P]
    [--relationship R] [--limit N]`; one-shot, foreground.

    readOnlyHint=False: the CLI body calls `Core.maybe_refresh` before the
    manifest read, so a stale index is incrementally re-indexed (writes).
    destructiveHint=False; idempotentHint=True."""
    argv = ["find-references", name]
    if fresh:
        argv += ["--fresh"]
    if project:
        argv += ["--project", project]
    if relationship:
        argv += ["--relationship", relationship]
    argv += ["--limit", str(limit)]
    return _run_cli(argv)


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True,
    openWorldHint=False))
def get_code_context(file: str, project: str | None = None,
                     start_line: int | None = None, end_line: int | None = None,
                     symbol: str | None = None, context_lines: int = 0,
                     fresh: bool = False) -> str:
    """Retrieve ONLY the relevant source lines instead of reading whole files —
    by line range (`start_line`/`end_line`) or via a symbol, padded by
    `context_lines`. Stale-safe: when the file changed since indexing,
    symbol mode re-resolves against the live file; line mode returns the
    requested lines with one stderr warning and exit 0. `fresh` ignores
    STALE_TTL for this call. Thin wrapper over `code-indexer get-code-context
    FILE [--project P] [--start-line N] [--end-line N] [--symbol S]
    [--context-lines N] [--fresh]`; one-shot, foreground.

    readOnlyHint=False: the `--symbol` branch calls `Core.maybe_refresh`, so a
    stale index is incrementally re-indexed (writes); the line-range branch
    does not. The tool can write, so the hint is false. destructiveHint=False;
    idempotentHint=True."""
    argv = ["get-code-context", file]
    if fresh:
        argv += ["--fresh"]
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
