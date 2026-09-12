"""FastMCP server: thin tool surface per design §4.

Six tools only. Agents never touch index internals — semantic_search
triggers the staleness check / incremental indexing transparently (req 1).
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
from .manifest import Manifest
from .registry import Registry
from .store import Store

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
            "start_line": p.get("start_line"),
            "end_line": p.get("end_line"),
            "snippet": (p.get("snippet") or "")[:500],
        })
    return out


# ---------------------------------------------------------------------------
# Tools (design §4 — six, no more)
# ---------------------------------------------------------------------------

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
    return f"registered {entry.path} (slug {entry.slug}); initial indexing started in background"


@mcp.tool()
def remove_project(path: str) -> str:
    """Deregister a project and DELETE its Qdrant collection + manifest +
    registry entry entirely."""
    path = os.path.abspath(os.path.expanduser(path))
    entry = REGISTRY.remove(path)
    if entry is None:
        return f"error: not registered: {path}"
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
                    file_filter: str | None = None) -> str:
    """Semantic code search across indexed projects. THE hot path.

    Automatically runs a staleness check first and incrementally re-indexes
    changed files, so results are always fresh (within one scan interval).
    Args: query (natural language); project (optional path, slug, or custom
    name — omit to search all registered projects); limit; file_filter
    (optional substring/glob on file path, e.g. '*.py').
    """
    entries = []
    if project:
        apath = os.path.abspath(os.path.expanduser(project))
        entry = REGISTRY.get_by_path(apath)
        if entry is None:
            # Also accept a registered slug or custom name.
            entry = REGISTRY.get_by_slug(project) or REGISTRY.get_by_name(project)
        if not entry:
            return f"error: project not registered: {project}"
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
    lines = ["search results (score desc):"]
    for h in all_hits:
        sym = f" ({h['symbol']})" if h["symbol"] else ""
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
    mcp.run(transport="stdio")