"""FastMCP server: thin tool adapters over code_indexer.core (design ruling §3).

Each tool handler is a <=10-line adapter calling exactly one core op; the
core owns registry, indexer, manifest, embedder, store, and locks. Agents
never touch index internals — semantic_search triggers the staleness check /
incremental indexing transparently via the core (req 1).

The filesystem watcher is NOT part of the default path (design ruling: it
dies with a one-shot process and adds no value in the MCP lifecycle);
staleness is enforced by the core's STALE_TTL probe on search/status.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from .core import MIGRATION_HINT, Core

logger = logging.getLogger("code-indexer")

CORE = Core()
mcp = FastMCP("code-indexer")


@mcp.tool()
def lookup_project(path: str) -> str:
    """Check whether a project directory is registered, and report its
    collection name and index summary (path, slug, name, idx_<slug>,
    state, files, chunks, last_indexed). Does NOT register or index.
    """
    raw = os.path.abspath(os.path.expanduser(path))
    entry = CORE.registry.get_by_path(raw)
    if entry is None:
        real = os.path.realpath(raw)
        if real != raw:
            entry = CORE.registry.get_by_path(real)
    if entry is None:
        return f"not registered: {raw}"
    collection = f"idx_{entry.slug}"
    name = f" name={entry.name}" if entry.name else ""
    return f"registered:{name} {CORE.status_summary(entry)} collection={collection}"


@mcp.tool()
def add_project(path: str, name: str | None = None) -> str:
    """Register an absolute project directory for semantic indexing
    (idempotent; optional sanitized custom name -> idx_<name>) and start
    the initial indexing pass in the background.
    """
    path = os.path.abspath(os.path.expanduser(path))
    try:
        if not os.path.isdir(path):
            return f"error: path does not exist: {path}"
        entry = CORE.registry.get_by_path(path)
        if entry is not None:
            return f"already registered — index status: {CORE.status_summary(entry)}"
        entry = CORE.registry.add(path, name=name)
    except ValueError as exc:
        return f"error: {exc}"
    threading.Thread(
        target=CORE.run_index, args=(entry.slug, entry.path),
        daemon=True, name=f"index-{entry.slug}").start()
    return f"registered {entry.path} (slug {entry.slug}); initial indexing started in background"


@mcp.tool()
def remove_project(path: str) -> str:
    """Deregister a project and DELETE its Qdrant collection + manifest +
    registry entry entirely."""
    import glob as _glob
    import shutil as _shutil
    path = os.path.abspath(os.path.expanduser(path))
    entry = CORE.registry.remove(path)
    if entry is None:
        return f"error: not registered: {path}"
    collection = f"idx_{entry.slug}"
    if CORE.store.collection_exists(collection):
        CORE.store.drop_collection(collection)
    # Remove manifest dir and (only when unheld) the lock file. The lock is
    # flock-based and kernel-released on death, so deleting the file here is
    # safe from the unlink-while-held footgun only because a removed project
    # has no active indexers; see locks.py for the general footgun note.
    for f in _glob.glob(os.path.join(CORE.cfg.index_root, f"{entry.slug}*")):
        try:
            os.remove(f)
        except OSError:
            pass
    manifest_dir = os.path.join(CORE.cfg.index_root, entry.slug)
    if os.path.isdir(manifest_dir):
        _shutil.rmtree(manifest_dir, ignore_errors=True)
    CORE._index_state.pop(entry.slug, None)
    return f"removed {path}: collection {collection} dropped, registry entry deleted"


@mcp.tool()
def list_projects() -> str:
    """List registered projects with per-project index summary and staleness."""
    entries = CORE.registry.list_projects()
    if not entries:
        return "no projects registered"
    return "\n".join(
        f"- {CORE.status_summary(e)}" for e in entries)


@mcp.tool()
def semantic_search(query: str, project: str | None = None, limit: int = 8,
                    file_filter: str | None = None,
                    format: str = "text") -> str:
    """Semantic code search across indexed projects. THE hot path.

    Automatically runs a staleness check first and incrementally re-indexes
    changed files. format: 'text' or 'json' (stable field contract: project,
    file, score, symbol, symbol_type, start_line, end_line, snippet).
    """
    return CORE.search_for_display(query, project=project, limit=limit,
                                   file_filter=file_filter, fmt=format)


@mcp.tool()
def index_status(path: str) -> str:
    """Check indexing state for a project: idle | indexing | stale | error,
    plus last-pass progress. Also triggers the staleness check."""
    path = os.path.abspath(os.path.expanduser(path))
    entry = CORE.registry.get_by_path(path)
    if not entry:
        return f"error: not registered: {path}"
    refresh = CORE.maybe_refresh(entry.slug, entry.path)
    st = CORE.state_for(entry.slug)
    state = st.get("state", "idle")
    last = st.get("last_result")
    err = st.get("error")
    parts = [f"project={path}", f"state={state}"]
    if last:
        parts.append(f"last_pass={last}")
    if err:
        parts.append(f"error={err}")
    if refresh.get("state") == "indexing":
        parts.append("note=incremental pass ran/was held by another process")
    return " ".join(parts)


@mcp.tool()
def reindex_project(path: str) -> str:
    """Force a full rebuild of a project's index (chunker/model change,
    suspected corruption)."""
    path = os.path.abspath(os.path.expanduser(path))
    entry = CORE.registry.get_by_path(path)
    if not entry:
        return f"error: not registered: {path}"
    def _force() -> None:
        CORE.run_index(entry.slug, entry.path, force=True)
    threading.Thread(target=_force, daemon=True, name=f"reindex-{entry.slug}").start()
    return f"full reindex queued for {path}"


def _fmt_symbol_rows(rows, project_path: str, match_label: bool) -> str:
    out = []
    for r in rows:
        line = (f"- {project_path}::{r.file}:{r.start_line}-{r.end_line} "
                f"{r.name} ({r.symbol_type}, confidence="
                f"{'exact' if r.source == 'ast' else 'heuristic'}")
        if match_label:
            line += ", match=exact" if not getattr(r, "_substring", False) \
                else ", match=substring"
        line += ")"
        if getattr(r, "signature", None):
            line += f"  {r.signature}"
        out.append(line)
    return "\n".join(out)


@mcp.tool()
def find_symbol(name: str, project: str | None = None,
                symbol_type: str | None = None) -> str:
    """Look up symbols by name in the manifest symbol index (no semantic
    search). Exact AST-first, capped substring fallback; symbol_type filters
    function|method|class|struct|enum|namespace.
    """
    entry, err = CORE.resolve_entry(project)
    if entry is None:
        return err
    CORE.maybe_refresh(entry.slug, entry.path)
    m = CORE.manifest_for(entry.slug)
    try:
        rows = m.find_symbols(name, symbol_type=symbol_type, substring=False)
        match_label = False
        if not rows:
            rows = m.find_symbols(name, symbol_type=symbol_type, substring=True)
            for r in rows:
                r._substring = True  # type: ignore[attr-defined]
            match_label = True
        if not rows:
            msg = f"no symbols matching {name!r}"
            if CORE.schema_migrated(entry):
                msg += "\n" + MIGRATION_HINT
            return msg
        return _fmt_symbol_rows(rows, entry.path, match_label)
    finally:
        m.close()


@mcp.tool()
def find_symbols(project: str | None = None, symbol_type: str | None = None,
                 file: str | None = None, limit: int = 25,
                 format: str = "text") -> str:
    """Browse-mode symbol listing (design §2.3 — replaces list-symbols):
    optional symbol_type / file filters over the manifest symbol index,
    capped at limit (max 500). format: 'text' or 'json'. Signatures
    included when stored (schema v3)."""
    limit = max(1, min(int(limit), 500))
    entry, err = CORE.resolve_entry(project)
    if entry is None:
        return err
    CORE.maybe_refresh(entry.slug, entry.path)
    m = CORE.manifest_for(entry.slug)
    try:
        rows = m.find_symbols(None, symbol_type=symbol_type,
                              file=file, limit=limit)
        if not rows:
            msg = "no symbols matching the given filters"
            if CORE.schema_migrated(entry):
                msg += "\n" + MIGRATION_HINT
            return msg
        if format == "json":
            return json.dumps([
                {"file": r.file, "name": r.name, "type": r.symbol_type,
                 "start_line": r.start_line, "end_line": r.end_line,
                 "signature": r.signature}
                for r in rows], ensure_ascii=False, indent=2)
        return _fmt_symbol_rows(rows, entry.path, match_label=False)
    finally:
        m.close()


@mcp.tool()
def skeleton(project: str | None = None, path_prefix: str | None = None,
             limit: int | None = None, format: str = "text") -> str:
    """Whole-project or per-subtree structural map from the manifest only
    (design §2.1): one file per group, one symbol per line with lines and
    signature. path_prefix restricts to files under a project-relative path.
    format: 'text' or 'json'."""
    entry, err = CORE.resolve_entry(project)
    if entry is None:
        return err
    data = CORE.skeleton(entry, prefix=path_prefix, tree_mode=False,
                         limit=limit, include_signatures=True)
    text = CORE.format_skeleton(data, format)
    if CORE.schema_migrated(entry):
        text += "\n" + MIGRATION_HINT
    return text


@mcp.tool()
def outline(file: str, project: str | None = None,
            docstrings: bool = False, format: str = "text") -> str:
    """One file: declarations, signatures, one-line docstrings (design §2.2).
    file is project-relative (or absolute — the project root prefix is
    stripped). format: 'text' or 'json'."""
    entry, err = CORE.resolve_entry(project)
    if entry is None:
        return err
    try:
        data = CORE.outline(entry, file, include_docstrings=docstrings)
    except ValueError as exc:
        return str(exc)
    text = CORE.format_outline(data, format)
    if CORE.schema_migrated(entry):
        text += "\n" + MIGRATION_HINT
    return text


@mcp.tool()
def find_definition(name: str, project: str | None = None) -> str:
    """Find where a symbol is declared (exact name match only, no fallback)."""
    entry, err = CORE.resolve_entry(project)
    if entry is None:
        return err
    CORE.maybe_refresh(entry.slug, entry.path)
    m = CORE.manifest_for(entry.slug)
    try:
        rows = m.find_symbols(name, substring=False)
        if not rows:
            msg = f"no exact-match symbols named {name!r}"
            if CORE.schema_migrated(entry):
                msg += "\n" + MIGRATION_HINT
            return msg
        return _fmt_symbol_rows(rows, entry.path, match_label=False)
    finally:
        m.close()


def _read_range(abs_path: str, start: int, end: int) -> str:
    with open(abs_path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    total = len(lines)
    start = max(1, start)
    end = min(total, end)
    if start > total:
        return f"error: start_line {start} beyond end of file ({total} lines)"
    return "\n".join(
        f"{i:>5}| {lines[i - 1].rstrip()}" for i in range(start, end + 1))


@mcp.tool()
def get_code_context(file: str, project: str | None = None,
                     start_line: int | None = None, end_line: int | None = None,
                     symbol: str | None = None, context_lines: int = 0) -> str:
    """Retrieve ONLY the relevant source lines instead of reading whole files
    (by line range, or by symbol with context_lines padding)."""
    entry, err = CORE.resolve_entry(project)
    if entry is None:
        return err
    rel = file.lstrip("/")
    abs_path = os.path.normpath(os.path.join(entry.path, rel))
    if not abs_path.startswith(os.path.normpath(entry.path) + os.sep):
        return f"error: path outside registered project: {file}"
    if not os.path.isfile(abs_path):
        return f"error: file not found: {abs_path}"

    ranges: list[tuple[int, int]] = []
    if symbol:
        CORE.maybe_refresh(entry.slug, entry.path)
        m = CORE.manifest_for(entry.slug)
        try:
            rows = [r for r in m.find_symbols(symbol, substring=False)
                    if r.file == rel]
        finally:
            m.close()
        if not rows:
            return f"error: symbol {symbol!r} not indexed in {rel}"
        for r in rows[:5]:
            ranges.append((max(1, r.start_line - context_lines),
                           r.end_line + context_lines))
    elif start_line is not None:
        end = end_line if end_line is not None else start_line
        ranges.append((max(1, start_line - context_lines),
                       end + context_lines))
    else:
        return "error: provide start_line/end_line or symbol"

    blocks = []
    for s, e in ranges:
        blocks.append(f"--- {rel}:{s}-{e} ---\n" + _read_range(abs_path, s, e))
    return "\n".join(blocks)


@mcp.tool()
def find_references(name: str, project: str | None = None,
                    relationship: str | None = None, limit: int = 25) -> str:
    """Find textual references TO a symbol: calls, inherits, includes
    (all confidence=heuristic; navigation aid, not static analysis)."""
    entry, err = CORE.resolve_entry(project)
    if entry is None:
        return err
    CORE.maybe_refresh(entry.slug, entry.path)
    m = CORE.manifest_for(entry.slug)
    try:
        rows = m.find_refs(name, relationship=relationship, limit=limit)
        if not rows:
            return f"no references to {name!r}"
        known = m.all_symbol_names()
        lines = [f"references to {name!r} (all confidence=heuristic):"]
        for r in rows:
            exact = "exact" if r.target in known else "heuristic"
            src = f" in {r.src_symbol}" if r.src_symbol else ""
            lines.append(
                f"- {entry.path}::{r.file}:{r.line}{src} "
                f"{r.relationship} {r.target} (target_confidence={exact})")
        return "\n".join(lines)
    finally:
        m.close()


def main() -> None:
    from .logsetup import configure_logging

    configure_logging(verbose=os.environ.get("VERBOSE", "") not in ("", "0"))
    logger.info(
        "code-indexer MCP starting — ollama=%s qdrant=%s model=%s index_root=%s",
        CORE.cfg.ollama_url, CORE.cfg.qdrant_url, CORE.cfg.embed_model,
        CORE.cfg.index_root,
    )
    mcp.run(transport="stdio")
