"""Core operations for the code-indexer (design ruling §3).

All indexing/search/registry logic lives here; ``server.py`` (MCP adapters)
and ``cli.py`` (typer app) are thin surfaces over these functions, and every
tool/subcommand maps 1:1 to an op here. One-shot process model: no daemon,
no watcher on the default path — staleness is enforced via STALE_TTL probes
on search/status, guarded by the per-project flock.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import time
from typing import Any

from .config import Config, load_config
from .embedder import Embedder
from .indexer import Indexer
from .locks import project_lock
from .manifest import SCHEMA_VERSION, Manifest
from .registry import ProjectEntry, Registry
from .store import Store

logger = logging.getLogger("code-indexer.core")

STALE_TTL_DEFAULT = 60


class Core:
    """Stateful core: owns config, registry, embedder, store, indexer.

    One instance per process, shared by the MCP adapter and the CLI. All
    mutating passes run under the per-project flock, so concurrent one-shot
    invocations (4 parallel CLI searches, server + CLI together) serialize:
    exactly one indexer pass runs, others report 'indexing in progress'.
    """

    def __init__(self, cfg: Config | None = None, skip_stale_check: bool = False):
        self.cfg = cfg or load_config()
        os.makedirs(self.cfg.index_root, exist_ok=True)
        self.skip_stale_check = skip_stale_check
        self.registry = Registry(os.path.join(self.cfg.index_root, "registry.db"))
        self.embedder = Embedder(self.cfg.ollama_url, self.cfg.embed_model,
                                 batch_size=self.cfg.embed_batch)
        self.store = Store(self.cfg.qdrant_url, upsert_batch=self.cfg.upsert_batch)
        self.indexer = Indexer(self.cfg, self.embedder, self.store)
        # Per-process staleness cache: slug -> last-scan monotonic time.
        self._last_scan: dict[str, float] = {}
        # Indexing state per slug (state, error, last_result) for status.
        self._index_state: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # paths / manifests / state
    # ------------------------------------------------------------------

    def manifest_for(self, slug: str) -> Manifest:
        d = os.path.join(self.cfg.index_root, slug)
        os.makedirs(d, exist_ok=True)
        return Manifest(os.path.join(d, "manifest.db"))

    def _lock_path(self, slug: str) -> str:
        return os.path.join(self.cfg.index_root, f"{slug}.lock")

    def _set_state(self, slug: str, **kw: Any) -> None:
        self._index_state.setdefault(slug, {}).update(kw)

    def state_for(self, slug: str) -> dict[str, Any]:
        return dict(self._index_state.get(slug, {}))

    # ------------------------------------------------------------------
    # indexing
    # ------------------------------------------------------------------

    def run_index(self, slug: str, project_path: str, force: bool = False) -> dict[str, Any]:
        """Run one indexing pass under the per-project lock.

        Returns {'state': 'idle', 'result': IndexResult-dict} on success,
        {'state': 'indexing', 'detail': 'indexing in progress'} when another
        process holds the lock, or {'state': 'error', 'error': ...}.
        """
        with project_lock(self._lock_path(slug)) as acquired:
            if not acquired:
                return {"state": "indexing", "detail": "indexing in progress"}
            self._set_state(slug, state="indexing", error=None)
            manifest = self.manifest_for(slug)
            try:
                result = self.indexer.index_project(
                    project_path, slug, manifest, force_full=force)
                self._last_scan[slug] = time.monotonic()
                self._set_state(slug, state="idle", last_result=vars(result))
                return {"state": "idle", "result": vars(result)}
            except Exception as exc:  # noqa: BLE001
                logger.exception("indexing failed for %s", project_path)
                self._set_state(slug, state="error", error=str(exc))
                return {"state": "error", "error": str(exc)}
            finally:
                manifest.close()

    def watch_pass(self, slug: str, project_path: str) -> dict[str, Any]:
        """One watcher pass: staleness probe + incremental index under the flock.

        Same core op as maybe_refresh's terminal call (run_index with
        force=False), but unconditioned by the STALE_TTL cache — the watch
        loop calls this every tick. Cheap when nothing changed (hash diff
        only; embeddings happen only for real changes).
        """
        return self.run_index(slug, project_path, force=False)

    def maybe_refresh(self, slug: str, project_path: str) -> dict[str, Any]:
        """Staleness probe: incremental re-index if last check > stale_ttl ago.

        The scan (hashing) is cheap; embedding only happens on real changes.
        Skipped entirely when the core was built with skip_stale_check=True
        (--skip-stale-check on the CLI; the vetoed daemon is NOT used).
        """
        if self.skip_stale_check:
            return {"state": "fresh"}
        last = self._last_scan.get(slug, 0.0)
        if time.monotonic() - last < self.cfg.stale_ttl:
            return {"state": "fresh"}
        entry = self.registry.get_by_slug(slug)
        if entry is None or not os.path.isdir(entry.path):
            return {"state": "idle"}
        return self.run_index(slug, entry.path, force=False)

    # ------------------------------------------------------------------
    # project resolution / summaries
    # ------------------------------------------------------------------

    def resolve_entry(self, project: str | None) -> tuple[ProjectEntry | None, str]:
        """Resolve an optional project arg (path, slug, or custom name)."""
        if project is None:
            entries = self.registry.list_projects()
            if not entries:
                return None, "error: no projects registered"
            if len(entries) > 1:
                return None, ("error: multiple projects registered — pass project "
                              "(path, slug, or name): "
                              + ", ".join(e.path for e in entries))
            return entries[0], ""
        apath = os.path.abspath(os.path.expanduser(project))
        entry = (self.registry.get_by_path(apath) or self.registry.get_by_slug(project)
                 or self.registry.get_by_name(project))
        if entry is None:
            real = os.path.realpath(apath)
            if real != apath:
                entry = self.registry.get_by_path(real)
        if entry is None:
            return None, f"error: project not registered: {project}"
        return entry, ""

    def status_summary(self, entry: ProjectEntry) -> str:
        """Human-readable per-project summary (path, slug, counts, times)."""
        file_count = chunk_count = 0
        last_indexed = None
        mdir = os.path.join(self.cfg.index_root, entry.slug, "manifest.db")
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
        st = self.state_for(entry.slug).get("state", "idle")
        return (f"path={entry.path} slug={entry.slug} state={st} "
                f"files={file_count} chunks={chunk_count} "
                f"last_indexed={last_indexed or 'never'}")

    def schema_migrated(self, entry: ProjectEntry) -> bool:
        """True when this manifest was just upgraded from a pre-symbol schema."""
        m = self.manifest_for(entry.slug)
        try:
            return getattr(m, "_migrated_from", SCHEMA_VERSION) not in (None, SCHEMA_VERSION)
        finally:
            m.close()

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------

    def search_one(self, entry: ProjectEntry, query: str, limit: int,
                   file_filter: str | None) -> list[dict[str, Any]]:
        collection = f"idx_{entry.slug}"
        vector = self.embedder.embed([query])[0]
        hits = self.store.search(collection, vector, limit=limit, file_filter=file_filter)
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

    def search(self, query: str, project: str | None = None, limit: int = 8,
               file_filter: str | None = None) -> list[dict[str, Any]]:
        """Semantic search across one or all registered projects.

        Runs the staleness probe per project first (unless skipped). Raises
        ValueError with a user-facing message on resolution/search errors —
        the adapters turn that into their error surface.
        """
        if project:
            entry, err = self.resolve_entry(project)
            if entry is None:
                raise ValueError(err)
            entries = [entry]
        else:
            entries = self.registry.list_projects()
            if not entries:
                raise ValueError("error: no projects registered")
        all_hits: list[dict[str, Any]] = []
        for entry in entries:
            self.maybe_refresh(entry.slug, entry.path)
            try:
                all_hits.extend(self.search_one(entry, query, limit, file_filter))
            except Exception as exc:  # noqa: BLE001
                logger.exception("search failed for %s", entry.path)
                raise ValueError(f"error: search failed on {entry.path}: {exc}") from exc
        all_hits.sort(key=lambda h: h["score"], reverse=True)
        return all_hits[:max(1, limit)]

    def format_hits(self, hits: list[dict[str, Any]], fmt: str = "text") -> str:
        """Format search hits as text lines or JSON (stable field contract)."""
        if not hits:
            return "no results"
        if fmt == "json":
            return json.dumps(hits, ensure_ascii=False, indent=2)
        lines = ["search results (score desc):"]
        for h in hits:
            sym = f" ({h['symbol']}" if h["symbol"] else ""
            if sym:
                sym += f", {h['symbol_type']})" if h.get("symbol_type") else ")"
            lines.append(
                f"- [{h['score']:.4f}] {h['project']}::{h['file']}"
                f":{h['start_line']}-{h['end_line']}{sym}"
            )
            lines.append(f"    {h['snippet'][:200]}")
        return "\n".join(lines)

    def search_for_display(self, query: str, project: str | None = None,
                           limit: int = 8, file_filter: str | None = None,
                           fmt: str = "text") -> str:
        try:
            hits = self.search(query, project=project, limit=limit,
                               file_filter=file_filter)
        except ValueError as exc:
            return str(exc)
        return self.format_hits(hits, fmt)


MIGRATION_HINT = (
    "note: project manifest was upgraded from an older schema — run "
    "reindex_project once to populate the symbol index"
)
