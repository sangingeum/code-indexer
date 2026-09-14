"""FastMCP server: thin tool surface per design §4.

Core navigation tools; agents never touch index internals — semantic_search
triggers the staleness check / incremental indexing transparently (req 1).
Plan v2 adds code-intelligence tools (find_symbol, find_definition,
get_code_context, find_references) on top of the same manifest.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import Config, load_config
from .embedder import Embedder
from .indexer import Indexer
from .locks import project_lock
from .manifest import SCHEMA_VERSION, Manifest
from .registry import Registry
from .store import Store
from .watcher import FileWatcher

logger = logging.getLogger("mcp-code-indexer")

CFG: Config = load_config()
os.makedirs(CFG.index_root, exist_ok=True)

REGISTRY = Registry(os.path.join(CFG.index_root, "registry.db"))
EMBEDDER = Embedder(CFG.ollama_url, CFG.embed_model, batch_size=CFG.embed_batch)
STORE = Store(CFG.qdrant_url, upsert_batch=CFG.upsert_batch)
INDEXER = Indexer(CFG, EMBEDDER, STORE)

# Per-process staleness cache: slug -> last-scan monotonic time.
_LAST_SCAN: dict[str, float] = {}
_LAST_SCAN_LOCK = threading.Lock()

# Track indexing state for index_status.
_INDEX_STATE: dict[str, dict[str, Any]] = {}
_STATE_LOCK = threading.Lock()

mcp = FastMCP("mcp-code-indexer")

# Filesystem watcher (design addendum §8): edits on registered roots trigger
# the same incremental _run_index without waiting for a tool call. One
# Observer thread total; project_lock serializes against tool-triggered
# passes; failures are contained inside the watcher (supervised restart).
def _watch_runner(slug: str, path: str) -> None:
    """Watcher entry point: same incremental pass as tool-triggered refresh."""
    _run_index(slug, path, force=False)


WATCHER = FileWatcher(_watch_runner, debounce_seconds=CFG.watch_debounce)


def _manifest_for(slug: str) -> Manifest:
    d = os.path.join(CFG.index_root, slug)
    os.makedirs(d, exist_ok=True)
    return Manifest(os.path.join(d, "manifest.db"))


def _lock_path(slug: str) -> str:
    return os.path.join(CFG.index_root, f"{slug}.lock")


def _set_state(slug: str, **kw: Any) -> None:
    with _STATE_LOCK:
        _INDEX_STATE.setdefault(slug, {}).update(kw)


def _run_index(slug: str, project_path: str, force: bool = False) -> dict[str, Any]:
    """Run one indexing pass under the per-project lock. Returns summary."""
    lock = _lock_path(slug)
    with _STATE_LOCK:
        _INDEX_STATE.setdefault(slug, {}).update({"state": "indexing", "error": None})
    with project_lock(lock) as acquired:
        if not acquired:
            return {"state": "indexing", "detail": "another process holds the lock"}
        manifest = _manifest_for(slug)
        try:
            result = INDEXER.index_project(project_path, slug, manifest, force_full=force)
            with _LAST_SCAN_LOCK:
                _LAST_SCAN[slug] = time.monotonic()
            _set_state(slug, state="idle", last_result=vars(result))
            return {"state": "idle", "result": vars(result)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("indexing failed for %s", project_path)
            _set_state(slug, state="error", error=str(exc))
            return {"state": "error", "error": str(exc)}
        finally:
            manifest.close()


def _maybe_refresh(slug: str, project_path: str) -> dict[str, Any]:
    """Staleness probe: re-scan if last check > stale_ttl seconds ago.

    This is the correctness backbone (design §7a): semantic_search never
    serves from a stale index by more than one scan interval. The scan
    (hashing) is cheap; embedding only happens when content changed.
    """
    with _LAST_SCAN_LOCK:
        last = _LAST_SCAN.get(slug, 0.0)
    if time.monotonic() - last < CFG.stale_ttl:
        return {"state": "fresh"}
    entry = REGISTRY.get_by_slug(slug)
    if entry is None or not os.path.isdir(entry.path):
        return {"state": "idle"}
    return _run_index(slug, entry.path, force=False)


def _spawn_background_index(slug: str, project_path: str) -> None:
    """Initial full index on a worker thread (design §7b)."""
    def worker() -> None:
        try:
            _run_index(slug, project_path, force=False)
        except Exception as exc:  # noqa: BLE001
            logger.exception("background index failed: %s", exc)
    t = threading.Thread(target=worker, daemon=True, name=f"index-{slug}")
    t.start()


def _status_summary(entry: Any) -> str:
    """Human-readable status summary for a registered project.

    state, file/chunk counts from the manifest, and last-indexed time —
    the same summary ``list_projects`` reports per project.
    """
    file_count = chunk_count = 0
    last_indexed = None
    mdir = os.path.join(CFG.index_root, entry.slug, "manifest.db")
    if os.path.isfile(mdir):
        m = Manifest(mdir)
        try:
            rows = m.all_files()
            file_count = len(rows)
            chunk_count = sum(r.chunk_count for r in rows.values())
            li = m.get_meta("last_indexed")
            if li:
                last_indexed = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(float(li)))
        finally:
            m.close()
    with _STATE_LOCK:
        st = _INDEX_STATE.get(entry.slug, {}).get("state", "idle")
    return (f"path={entry.path} slug={entry.slug} state={st} "
            f"files={file_count} chunks={chunk_count} "
            f"last_indexed={last_indexed or 'never'}")


def _search_one(entry: Any, query: str, limit: int,
                file_filter: str | None) -> list[dict[str, Any]]:
    collection = f"idx_{entry.slug}"
    vector = EMBEDDER.embed([query])[0]
    hits = STORE.search(collection, vector, limit=limit, file_filter=file_filter)
    out = []
    for h in hits:
        p = h.payload or {}
        out.append({
            "project": entry.path,
            "file": p.get("file"),
            "score": round(h.score, 4),
            "symbol": p.get("symbol"),
            "symbol_type": p.get("symbol_type"),
            "start_line": p.get("start_line"),
            "end_line": p.get("end_line"),
            "snippet": (p.get("snippet") or "")[:500],
        })
    return out


def _resolve_entry(project: str | None) -> tuple[Any | None, str]:
    """Resolve an optional project arg (path, slug, or custom name)."""
    if project is None:
        entries = REGISTRY.list_projects()
        if not entries:
            return None, "error: no projects registered"
        if len(entries) > 1:
            return None, ("error: multiple projects registered — pass project "
                          "(path, slug, or name): "
                          + ", ".join(e.path for e in entries))
        return entries[0], ""
    apath = os.path.abspath(os.path.expanduser(project))
    entry = REGISTRY.get_by_path(apath) or REGISTRY.get_by_slug(project) \
        or REGISTRY.get_by_name(project)
    if entry is None:
        # Symlinked path fallback.
        real = os.path.realpath(apath)
        if real != apath:
            entry = REGISTRY.get_by_path(real)
    if entry is None:
        return None, f"error: project not registered: {project}"
    return entry, ""


def _manifest_open(entry: Any) -> Manifest:
    return _manifest_for(entry.slug)


def _schema_migrated(entry: Any) -> bool:
    """True when this manifest was just upgraded from a pre-symbol schema.

    v1 manifests contain files but no symbol rows for their unchanged files;
    a full reindex is needed once before find_symbol/get_code_context can
    see anything. Detected via the migration marker set in Manifest init.
    """
    m = _manifest_for(entry.slug)
    try:
        return getattr(m, "_migrated_from", SCHEMA_VERSION) not in (None, SCHEMA_VERSION)
    finally:
        m.close()


_MIGRATION_HINT = (
    "note: project manifest was upgraded from an older schema — run "
    "reindex_project once to populate the symbol index"
)


# ---------------------------------------------------------------------------
# Tools (design §4 — core navigation; plan v2 adds code intelligence)
# ---------------------------------------------------------------------------

@mcp.tool()
def lookup_project(path: str) -> str:
    """Check whether a project directory is registered, and report its
    collection name and index summary.

    Returns one line for a registered project: path, slug, custom name,
    Qdrant collection name (idx_<slug>), state, files, chunks, and
    last_indexed — or 'not registered: <path>' if absent. Does NOT register
    the project or trigger any indexing. Path is normalized (~ expanded,
    relative resolved); a symlinked path is matched via its real path as a
    fallback.
    """
    raw = os.path.abspath(os.path.expanduser(path))
    entry = REGISTRY.get_by_path(raw)
    if entry is None:
        real = os.path.realpath(raw)
        if real != raw:
            entry = REGISTRY.get_by_path(real)
    if entry is None:
        return f"not registered: {raw}"
    collection = f"idx_{entry.slug}"
    name = f" name={entry.name}" if entry.name else ""
    return f"registered:{name} {_status_summary(entry)} collection={collection}"


@mcp.tool()
def add_project(path: str, name: str | None = None) -> str:
    """Register an absolute project directory for semantic indexing.

    Idempotent: if the path is already registered, no re-index is spawned —
    the current index_status summary (state, files, chunks, last_indexed) is
    returned instead. Errors only for paths that do not exist.

    name (optional): caller-chosen collection name. Sanitized to
    [A-Za-z0-9_-] (1-64 chars); the collection becomes idx_<name> instead of
    the auto hash slug idx_{hash}. Collision-checked against the registry.
    Omit for the default auto slug.
    """
    try:
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            return f"error: path does not exist: {path}"
        entry = REGISTRY.get_by_path(path)
        if entry is not None:
            return (f"already registered — index status: "
                    f"{_status_summary(entry)}")
        entry = REGISTRY.add(path, name=name)
    except ValueError as exc:
        return f"error: {exc}"
    _spawn_background_index(entry.slug, entry.path)
    WATCHER.watch(entry.slug, entry.path)
    return f"registered {entry.path} (slug {entry.slug}); initial indexing started in background"


@mcp.tool()
def remove_project(path: str) -> str:
    """Deregister a project and DELETE its Qdrant collection + manifest +
    registry entry entirely."""
    path = os.path.abspath(os.path.expanduser(path))
    entry = REGISTRY.remove(path)
    if entry is None:
        return f"error: not registered: {path}"
    WATCHER.unwatch(entry.slug)
    collection = f"idx_{entry.slug}"
    if STORE.collection_exists(collection):
        STORE.drop_collection(collection)
    # Manifest files + lock file removal.
    import glob as _glob
    for f in _glob.glob(os.path.join(CFG.index_root, f"{entry.slug}*")):
        try:
            os.remove(f)
        except OSError:
            pass
    import shutil as _shutil
    manifest_dir = os.path.join(CFG.index_root, entry.slug)
    if os.path.isdir(manifest_dir):
        _shutil.rmtree(manifest_dir, ignore_errors=True)
    with _LAST_SCAN_LOCK:
        _LAST_SCAN.pop(entry.slug, None)
    with _STATE_LOCK:
        _INDEX_STATE.pop(entry.slug, None)
    return f"removed {path}: collection {collection} dropped, registry entry deleted"


@mcp.tool()
def list_projects() -> str:
    """List registered projects with per-project index summary and staleness."""
    entries = REGISTRY.list_projects()
    if not entries:
        return "no projects registered"
    lines = []
    for e in entries:
        file_count = chunk_count = 0
        last_indexed = None
        mdir = os.path.join(CFG.index_root, e.slug, "manifest.db")
        if os.path.isfile(mdir):
            m = Manifest(mdir)
            try:
                rows = m.all_files()
                file_count = len(rows)
                chunk_count = sum(r.chunk_count for r in rows.values())
                li = m.get_meta("last_indexed")
                if li:
                    last_indexed = time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(float(li)))
            finally:
                m.close()
        with _STATE_LOCK:
            st = _INDEX_STATE.get(e.slug, {}).get("state", "idle")
        lines.append(
            f"- {e.path} (slug {e.slug}): files={file_count} chunks={chunk_count} "
            f"last_indexed={last_indexed or 'never'} state={st}"
        )
    return "\n".join(lines)


@mcp.tool()
def semantic_search(query: str, project: str | None = None, limit: int = 8,
                    file_filter: str | None = None,
                    format: str = "text") -> str:
    """Semantic code search across indexed projects. THE hot path.

    Automatically runs a staleness check first and incrementally re-indexes
    changed files, so results are always fresh (within one scan interval).
    Args: query (natural language); project (optional path, slug, or custom
    name — omit to search all registered projects); limit; file_filter
    (optional substring/glob on file path, e.g. '*.py'); format ('text'
    default, or 'json' — stable field contract: project, file, score,
    symbol, symbol_type, start_line, end_line, snippet).
    """
    entries = []
    if project:
        entry, err = _resolve_entry(project)
        if entry is None:
            return err
        entries = [entry]
    else:
        entries = REGISTRY.list_projects()
        if not entries:
            return "error: no projects registered"

    import fnmatch
    all_hits: list[dict[str, Any]] = []
    for entry in entries:
        # Staleness check + incremental indexing before searching (req 1).
        _maybe_refresh(entry.slug, entry.path)
        eff_filter = file_filter
        try:
            all_hits.extend(_search_one(entry, query, limit, eff_filter))
        except Exception as exc:  # noqa: BLE001
            logger.exception("search failed for %s", entry.path)
            return f"error: search failed on {entry.path}: {exc}"

    all_hits.sort(key=lambda h: h["score"], reverse=True)
    all_hits = all_hits[:max(1, limit)]
    if not all_hits:
        return "no results"
    if format == "json":
        import json as _json
        return _json.dumps(all_hits, ensure_ascii=False, indent=2)
    lines = ["search results (score desc):"]
    for h in all_hits:
        sym = f" ({h['symbol']}" if h["symbol"] else ""
        if sym:
            sym += f", {h['symbol_type']})" if h.get("symbol_type") else ")"
        lines.append(
            f"- [{h['score']:.4f}] {h['project']}::{h['file']}"
            f":{h['start_line']}-{h['end_line']}{sym}"
        )
        lines.append(f"    {h['snippet'][:200]}")
    return "\n".join(lines)


@mcp.tool()
def index_status(path: str) -> str:
    """Check indexing state for a project: idle | indexing | stale | error,
    plus last-pass progress. Also triggers the staleness check."""
    path = os.path.abspath(os.path.expanduser(path))
    entry = REGISTRY.get_by_path(path)
    if not entry:
        return f"error: not registered: {path}"
    refresh = _maybe_refresh(entry.slug, entry.path)
    with _STATE_LOCK:
        st = dict(_INDEX_STATE.get(entry.slug, {}))
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
    entry = REGISTRY.get_by_path(path)
    if not entry:
        return f"error: not registered: {path}"
    _set_state(entry.slug, state="indexing")
    def _force() -> None:
        _run_index(entry.slug, entry.path, force=True)
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
        out.append(line + ")")
    return "\n".join(out)


@mcp.tool()
def find_symbol(name: str, project: str | None = None,
                symbol_type: str | None = None) -> str:
    """Look up symbols by name in the manifest symbol index (no semantic search).

    Exact match first (AST-extracted rows ranked above regex-extracted);
    falls back to a capped (25) labelled substring tier when nothing matches
    exactly. Every row reports file:line range, type, and confidence
    (exact = from a tree-sitter AST; heuristic = regex-chunked file).
    symbol_type filters: function|method|class|struct|enum|namespace.
    """
    entry, err = _resolve_entry(project)
    if entry is None:
        return err
    _maybe_refresh(entry.slug, entry.path)
    m = _manifest_open(entry)
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
            if _schema_migrated(entry):
                msg += "\n" + _MIGRATION_HINT
            return msg
        return _fmt_symbol_rows(rows, entry.path, match_label)
    finally:
        m.close()


@mcp.tool()
def find_definition(name: str, project: str | None = None) -> str:
    """Find where a symbol is declared (exact name match only, no fallback).

    Returns all declaration-like sites. NOTE: for C/C++ a header declaration
    and a .cpp definition are both symbol_type='function' from tree-sitter's
    view — declaration vs definition is not distinguished.
    """
    entry, err = _resolve_entry(project)
    if entry is None:
        return err
    _maybe_refresh(entry.slug, entry.path)
    m = _manifest_open(entry)
    try:
        rows = m.find_symbols(name, substring=False)
        if not rows:
            msg = f"no exact-match symbols named {name!r}"
            if _schema_migrated(entry):
                msg += "\n" + _MIGRATION_HINT
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
    """Retrieve ONLY the relevant source lines instead of reading whole files.

    Two forms:
    - line range: get_code_context(file, start_line, end_line)
    - by symbol:  get_code_context(file, symbol='Foo::bar') — resolves the
      symbol via the index and returns each matching site's line range
      (all matches, capped at 5; decl in .hpp and def in .cpp are both shown).
    context_lines pads each range by that many lines on both sides.
    Serves raw disk content; line numbers reflect the last index pass, so
    they can drift from disk right after an unindexed edit.
    File is resolved against the registered project root; paths outside the
    project are rejected.
    """
    entry, err = _resolve_entry(project)
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
        _maybe_refresh(entry.slug, entry.path)
        m = _manifest_open(entry)
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
    """Find textual references TO a symbol: calls, inherits, includes.

    One relationship representation; relationship optionally filters
    (calls|inherits|includes|references). All edges are textual/unbound —
    every result is labeled confidence='heuristic' (navigation aid, not
    static analysis). Regex-chunked files contribute no ref edges.
    """
    entry, err = _resolve_entry(project)
    if entry is None:
        return err
    _maybe_refresh(entry.slug, entry.path)
    m = _manifest_open(entry)
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logger.info(
        "mcp-code-indexer starting — ollama=%s qdrant=%s model=%s index_root=%s",
        CFG.ollama_url, CFG.qdrant_url, CFG.embed_model, CFG.index_root,
    )
    try:
        for entry in REGISTRY.list_projects():
            WATCHER.watch(entry.slug, entry.path)
        WATCHER.start()
    except Exception:  # noqa: BLE001
        logger.exception("file watcher startup failed — continuing without it")
    mcp.run(transport="stdio")