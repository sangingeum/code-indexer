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
from .fingerprint import (ConfigError, current_fingerprint,
                          fingerprint_mismatches, mismatch_error,
                          read_fingerprint)
from .indexer import Indexer
from .locks import project_lock
from .manifest import SCHEMA_VERSION, Manifest, SymbolRow
from . import ranking
from . import ts_chunker
from .registry import ProjectEntry, Registry
from .store import Store

logger = logging.getLogger("code-indexer.core")

STALE_TTL_DEFAULT = 60

# State reported for a registered project whose manifest records no completed
# indexing pass (interrupted/killed initial add-project, or a pass that never
# ran). Registry registration alone is not evidence of an indexed project, so
# this must be distinguishable from a normal idle one.
NEVER_INDEXED = "never-indexed"

# State for a project whose manifest fingerprint does not match the current
# configuration (different embed model / dimension / text version / chunker).
# Queries refuse to run against such an index (ConfigError); reindex-project
# clears it by rebuilding.
NEEDS_REINDEX = "needs-reindex"


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
                                 batch_size=self.cfg.embed_batch,
                                 timeout=self.cfg.ollama_timeout)
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

    def has_successful_pass(self, slug: str) -> bool:
        """True when the manifest records at least one completed indexing pass.

        `last_indexed` is written only after a pass commits, so its absence on
        a registered project means the initial index never finished (an
        interrupted or killed `add-project`, or an index that never started).
        A manifest that does not exist yet is equally evidence-free. This is
        the persisted signal that keeps `list-projects` / `index-status`
        honest about projects that are registered but not indexed.
        """
        mdir = os.path.join(self.cfg.index_root, slug, "manifest.db")
        if not os.path.isfile(mdir):
            return False
        m = Manifest(mdir)
        try:
            return m.get_meta("last_indexed") is not None
        finally:
            m.close()

    def effective_state(self, slug: str, recorded: str | None = None) -> str:
        """The reported state: in-memory state refined by the persisted signal.

        An in-flight pass ('indexing') or a recorded failure ('error') is
        reported as is; an otherwise idle project with no completed pass is
        `never-indexed`, never a plain `idle`.
        """
        state = recorded if recorded is not None else \
            self.state_for(slug).get("state", "idle")
        if state in (None, "idle") and not self.has_successful_pass(slug):
            return NEVER_INDEXED
        return state or "idle"

    # ------------------------------------------------------------------
    # indexing
    # ------------------------------------------------------------------

    def run_index(self, slug: str, project_path: str, force: bool = False,
                  verbose: bool = False) -> dict[str, Any]:
        """Run one indexing pass under the per-project lock.

        Returns {'state': 'idle', 'result': IndexResult-dict} on success,
        {'state': 'indexing', 'detail': 'indexing in progress'} when another
        process holds the lock, or {'state': 'error', 'error': ...}.
        ``verbose=True`` (CLI --refresh) surfaces the pass summary; the
        default query-path pass is silent.
        """
        with project_lock(self._lock_path(slug)) as acquired:
            if not acquired:
                return {"state": "indexing", "detail": "indexing in progress"}
            self._set_state(slug, state="indexing", error=None)
            self.indexer.progress_cb = (
                lambda info, s=slug: self._set_state(s, progress=info))
            manifest = self.manifest_for(slug)
            try:
                result = self.indexer.index_project(
                    project_path, slug, manifest, force_full=force)
                self._last_scan[slug] = time.monotonic()
                self._set_state(slug, state="idle", last_result=vars(result))
                if verbose:
                    logger.info("index pass %s: %s", slug, vars(result))
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

    def maybe_refresh(self, slug: str, project_path: str,
                      force: bool = False) -> dict[str, Any]:
        """Staleness probe: incremental re-index if last check > stale_ttl ago.

        Default (quiet) mode: within TTL, or on a flock miss, this returns
        without indexing — a query-path re-index costs latency and noise.
        The pass is silent (no logging at INFO) and only runs when actually
        stale. Callers that want the pass regardless of TTL pass force=True
        (CLI --refresh). Skipped entirely when the core was built with
        skip_stale_check=True (--skip-stale-check on the CLI; the vetoed
        daemon is NOT used).
        """
        if self.skip_stale_check:
            return {"state": "fresh"}
        last = self._last_scan.get(slug, 0.0)
        if not force and time.monotonic() - last < self.cfg.stale_ttl:
            return {"state": "fresh"}
        entry = self.registry.get_by_slug(slug)
        if entry is None or not os.path.isdir(entry.path):
            return {"state": "idle"}
        return self.run_index(slug, entry.path, force=False, verbose=force)

    # ------------------------------------------------------------------
    # project resolution / summaries
    # ------------------------------------------------------------------

    def _containing_entry(self, target: str) -> ProjectEntry | None:
        """Most-specific registered project whose path contains ``target``."""
        candidates = [
            e for e in self.registry.list_projects()
            if target == e.path or target.startswith(e.path.rstrip("/") + "/")
        ]
        return max(candidates, key=lambda e: len(e.path)) if candidates else None

    def resolve_entry(self, project: str | None,
                      hint: str | None = None) -> tuple[ProjectEntry | None, str]:
        """Resolve an optional project arg (path, slug, or custom name).

        With ``project`` unset, try to infer the project from ``hint`` (e.g. a
        positional path prefix) and then the current working directory: the
        most-specific registered ancestor wins. Falls back to the legacy
        single-project/ambiguity behavior when nothing matches.
        """
        if project is None:
            for target in (hint, os.getcwd()):
                if not target:
                    continue
                apath = os.path.abspath(os.path.expanduser(target))
                entry = self._containing_entry(apath)
                if entry is not None:
                    return entry, ""
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
        # A registered project with no committed pass is never a plain idle
        # one (see effective_state): last_indexed is written only after a pass
        # commits, and the manifest read above already answers it.
        if st in (None, "idle") and last_indexed is None:
            st = NEVER_INDEXED
        fp = self.fingerprint_status(entry)
        suffix = ""
        if last_indexed is not None and not fp["ok"]:
            st = NEEDS_REINDEX
            suffix = f" reason={fp['reason']}"
        if fp["recorded"] is None and last_indexed is not None:
            suffix += " note=fingerprint backfills on next index pass"
        return (f"path={entry.path} slug={entry.slug} state={st} "
                f"files={file_count} chunks={chunk_count} "
                f"last_indexed={last_indexed or 'never'}{suffix}")

    def schema_migrated(self, entry: ProjectEntry) -> bool:
        """True when this manifest was just upgraded from a pre-symbol schema."""
        m = self.manifest_for(entry.slug)
        try:
            return getattr(m, "_migrated_from", SCHEMA_VERSION) not in (None, SCHEMA_VERSION)
        finally:
            m.close()

    # ------------------------------------------------------------------
    # index fingerprint (v4): mismatch protection
    # ------------------------------------------------------------------

    def _current_fingerprint_or_none(self):
        """The running configuration's fingerprint, or None when the embed
        dimension cannot be probed (Ollama unreachable) — absence of a probe
        must not turn every offline command into a hard failure, but query
        paths call this only right before they need the embedder anyway."""
        try:
            return current_fingerprint(self.cfg.embed_model,
                                       self.embedder.dimension())
        except Exception as exc:  # noqa: BLE001
            logger.debug("fingerprint probe failed: %s", exc)
            return None

    def fingerprint_status(self, entry: ProjectEntry) -> dict[str, Any]:
        """{'ok': bool, 'reason': str|None, 'recorded': str|None}.

        A manifest without a recorded fingerprint (legacy v3) counts as ok:
        the first indexing pass backfills it without forcing a reindex.
        """
        recorded = None
        mdir = os.path.join(self.cfg.index_root, entry.slug, "manifest.db")
        if os.path.isfile(mdir):
            m = Manifest(mdir)
            try:
                recorded = read_fingerprint(m)
            finally:
                m.close()
        current = self._current_fingerprint_or_none()
        if recorded is None:
            return {"ok": True, "reason": None,
                    "recorded": None if recorded is None else recorded.describe()}
        if current is None:
            # Cannot verify right now; do not block (queries will fail on
            # their own if the embedder is truly unreachable).
            return {"ok": True, "reason": None,
                    "recorded": recorded.describe()}
        diffs = fingerprint_mismatches(recorded, current)
        if diffs:
            return {"ok": False,
                    "reason": "; ".join(diffs),
                    "recorded": recorded.describe()}
        return {"ok": True, "reason": None, "recorded": recorded.describe()}

    def assert_fingerprint_ok(self, entry: ProjectEntry) -> None:
        """Raise ConfigError when the index predates the current config.

        Query paths call this before spending an embedding round trip on a
        mismatched index; --skip-stale-check does NOT bypass it (a wrong-model
        query is wrong regardless of staleness).
        """
        status = self.fingerprint_status(entry)
        if not status["ok"]:
            current = self._current_fingerprint_or_none()
            m = self.manifest_for(entry.slug)
            try:
                recorded = read_fingerprint(m)
            finally:
                m.close()
            if current is not None and recorded is not None:
                raise mismatch_error(entry.path, recorded, current)
            raise ConfigError(
                f"ConfigError: index for {entry.path} does not match the "
                f"current configuration ({status['reason']}); "
                f"run: code-indexer reindex-project {entry.path}")

    def reindex_with_swap(self, slug: str, project_path: str) -> dict[str, Any]:
        """Full rebuild into idx_<slug>__new, then swap.

        The old collection stays searchable until the rebuild finishes; the
        swap (drop old, rename new) is the only disruptive moment. A crash
        mid-rebuild leaves idx_<slug>__new behind — the next attempt reuses/
        recreates it. Clearing the manifest fingerprint before the pass makes
        index_project treat the legacy manifest as unknown (backfill) rather
        than mismatched (rebuild loop).
        """
        tmp = f"idx_{slug}__new"
        with project_lock(self._lock_path(slug)) as acquired:
            if not acquired:
                return {"state": "indexing", "detail": "indexing in progress"}
            self._set_state(slug, state="indexing", error=None)
            manifest = self.manifest_for(slug)
            try:
                if self.store.collection_exists(tmp):
                    self.store.drop_collection(tmp)
                result = self.indexer.index_project(
                    project_path, slug, manifest, force_full=True,
                    collection=tmp)
                self.store.swap_collection(tmp, f"idx_{slug}")
                self._last_scan[slug] = time.monotonic()
                self._set_state(slug, state="idle", last_result=vars(result))
                return {"state": "idle", "result": vars(result)}
            except Exception as exc:  # noqa: BLE001
                logger.exception("swap rebuild failed for %s", project_path)
                self._set_state(slug, state="error", error=str(exc))
                return {"state": "error", "error": str(exc)}
            finally:
                manifest.close()

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------

    def search_one(self, entry: ProjectEntry, query: str, limit: int,
                   file_filter: str | None,
                   symbol_type: str | None = None,
                   language: str | None = None,
                   ranking_mode: str = "vector",
                   rerank: str | None = None) -> list[dict[str, Any]]:
        """One project's search. `ranking_mode` selects the query-time
        ranking on the candidate pool: 'vector' (default, pure cosine),
        'metadata' (cosine + metadata adjustments), 'hybrid'
        (weighted-sum fusion of the vector score with lexical token
        overlap).

        ``rerank`` ('heuristic' | 'none' | None = off) applies a final
        re-scoring pass over the top RERANK_POOL candidates after the mode
        pipeline and the data-file mitigation — see reranker.py. Deterministic
        for fixed input.

        On top of the selected mode, every result set passes through the
        data-file mitigation (pure-data json/yaml/toml chunks are down-weighted
        and share-capped in the top-k window) so data files cannot crowd code
        out of the window. The pool is over-fetched so the mitigation has
        candidates to promote."""
        collection = f"idx_{entry.slug}"
        vector = self.embedder.embed([query])[0]
        # Over-fetch: re-ranking (and the data-file cap) operates on a wider
        # pool; the final truncate back to `limit` happens in search().
        fetch = max(limit * 3, 24)
        hits = self.store.search(
            collection, vector, limit=fetch, file_filter=file_filter,
            symbol_type=symbol_type, language=language)
        out = []
        for h in hits:
            p = h.payload or {}
            out.append({
                "project": entry.path,
                "file": p.get("file"),
                "score": round(h.score, 4),
                "symbol": p.get("symbol"),
                "symbol_type": p.get("symbol_type"),
                "lang": p.get("lang"),
                "start_line": p.get("start_line"),
                "end_line": p.get("end_line"),
                "snippet": (p.get("snippet") or "")[:500],
                # CI-03: staleness provenance for get_code_context.
                "file_hash": p.get("file_hash"),
                "indexed_at": p.get("indexed_at"),
            })
        qtokens = ranking.query_tokens(query)
        if ranking_mode == "metadata":
            out = ranking.metadata_rerank(out, qtokens)
        elif ranking_mode == "hybrid":
            out = ranking.hybrid_fuse(out, qtokens)
        elif ranking_mode != "vector":
            raise ValueError(f"error: unknown ranking mode: {ranking_mode}")
        out = ranking.downweight_data_files(out, qtokens)
        out = ranking.cap_data_file_share(out, limit, qtokens)
        if rerank:
            from .reranker import RERANK_POOL, select_reranker
            reranker = select_reranker(rerank)
            head = reranker.rerank(out[:RERANK_POOL], query)
            out = head + out[RERANK_POOL:]
        return out

    def search(self, query: str, project: str | None = None, limit: int = 8,
               file_filter: str | None = None,
               symbol_type: str | None = None,
               language: str | None = None,
               ranking_mode: str = "vector",
               skip_refresh: bool = False,
               per_file: int = 0,
               max_chars: int | None = None,
               max_tokens: int | None = None,
               merge: bool = True,
               rerank: str | None = None) -> dict[str, Any]:
        """Semantic search across one or all registered projects.

        Runs the staleness probe per project first (unless skipped). Raises
        ValueError with a user-facing message on resolution/search errors —
        the adapters turn that into their error surface.

        Result shaping (token-budget work item): same-file overlapping/adjacent
        hits are merged, at most ``per_file`` hits per file are kept (0 = no
        generic cap), and ``max_chars``/``max_tokens`` trim the lowest-ranked
        hits. Returns {'hits': [...], 'dropped': int} so adapters can report
        trimming. Multi-project (project=None) fuses per-collection lists with
        Reciprocal Rank Fusion — raw cosine is not comparable across
        collections.
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
        per_project: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            # Fingerprint gate BEFORE any embedding work: a query against a
            # wrong-model index is wrong regardless of staleness, and
            # --skip-stale-check must not bypass it.
            self.assert_fingerprint_ok(entry)
            if not skip_refresh:
                self.maybe_refresh(entry.slug, entry.path)
            try:
                per_project[entry.path] = self.search_one(
                    entry, query, limit, file_filter,
                    symbol_type=symbol_type, language=language,
                    ranking_mode=ranking_mode, rerank=rerank)
            except Exception as exc:  # noqa: BLE001
                logger.exception("search failed for %s", entry.path)
                raise ValueError(f"error: search failed on {entry.path}: {exc}") from exc

        if len(per_project) == 1:
            all_hits = next(iter(per_project.values()))
            all_hits.sort(key=lambda h: h["score"], reverse=True)
            # Single project: scores are comparable; keep the share cap.
            all_hits = ranking.cap_data_file_share(
                all_hits, max(1, limit), ranking.query_tokens(query))
        else:
            # Multi-project: raw cosine is not comparable across collections —
            # fuse per-project rank lists with RRF, then re-apply the data-file
            # share cap on the fused window.
            all_hits = ranking.rrf_fuse(per_project, limit * 3)
            all_hits = ranking.cap_data_file_share(
                all_hits, max(1, limit), ranking.query_tokens(query))

        # Result shaping: merge overlapping/adjacent same-file hits, generic
        # per-file cap, budget trim (lowest-ranked dropped first).
        if merge:
            all_hits = ranking.merge_overlapping(all_hits)
        all_hits = ranking.cap_per_file(all_hits, per_file)
        all_hits = ranking.truncate_snippets(all_hits, max_chars, max_tokens)
        all_hits, dropped = ranking.trim_to_budget(
            all_hits, max_chars=max_chars, max_tokens=max_tokens)
        return {"hits": all_hits[:max(1, limit)], "dropped": dropped}

    def format_hits(self, hits: list[dict[str, Any]], fmt: str = "text",
                dropped: int = 0, context_lines: int = 0) -> str:
        """Format search hits (stable field contract).

        ``text`` — the original verbose two-line-per-hit render (default;
        kept for compatibility with existing agent workflows).
        ``compact`` — one line per hit: ``path:start-end  symbol  score``;
        the token-budget agent loop (search compact -> get-code-context).
        ``json`` — the JSON contract.
        ``dropped`` > 0 appends one trailing truncation line in text/compact
        mode (the documented exception to single-line stderr reporting).
        ``context_lines`` expands each hit's snippet window by padding the
        snippet with surrounding lines up to N (0 = location+symbol only in
        compact; the snippet itself already carries the source window).
        """
        if not hits:
            return "no results"
        if fmt == "json":
            return json.dumps(
                {"hits": hits, "truncated": dropped > 0, "dropped": dropped},
                ensure_ascii=False, indent=2)
        lines: list[str] = []
        if fmt == "compact":
            for h in hits:
                sym = h.get("symbol") or "-"
                lines.append(
                    f"{h['project']}::{h['file']}:{h['start_line']}-"
                    f"{h['end_line']}  {sym}  {h['score']:.4f}")
        else:
            lines.append("search results (score desc):")
            for h in hits:
                sym = f" ({h['symbol']}" if h["symbol"] else ""
                if sym:
                    sym += f", {h['symbol_type']})" if h.get("symbol_type") else ")"
                lines.append(
                    f"- [{h['score']:.4f}] {h['project']}::{h['file']}"
                    f":{h['start_line']}-{h['end_line']}{sym}"
                )
                snippet = h.get("snippet") or ""
                if context_lines:
                    # Show the leading context_lines lines of the snippet
                    # (the snippet carries chunk text; expand up to N lines).
                    shown = "\n".join(snippet.splitlines()[:context_lines])
                else:
                    shown = snippet[:200]
                lines.append(f"    {shown}")
        if dropped:
            lines.append(
                f"({dropped} lower-ranked hit(s) dropped by the output budget; "
                f"raise --max-chars/--max-tokens to see more)")
        return "\n".join(lines)

    def search_for_display(self, query: str, project: str | None = None,
                           limit: int = 8, file_filter: str | None = None,
                           symbol_type: str | None = None,
                           language: str | None = None,
                           ranking_mode: str = "vector",
                           fmt: str = "text",
                           skip_refresh: bool = False,
                           per_file: int = 0,
                           max_chars: int | None = None,
                           max_tokens: int | None = None,
                           context_lines: int = 0,
                           rerank: str | None = None) -> str:
        try:
            result = self.search(query, project=project, limit=limit,
                               file_filter=file_filter,
                               symbol_type=symbol_type, language=language,
                               ranking_mode=ranking_mode,
                               skip_refresh=skip_refresh, per_file=per_file,
                               max_chars=max_chars, max_tokens=max_tokens,
                               rerank=rerank)
        except ValueError as exc:
            return str(exc)
        return self.format_hits(result["hits"], fmt, dropped=result["dropped"],
                                context_lines=context_lines)

    # ------------------------------------------------------------------
    # token reduction: skeleton / outline (subcommand design §2.1/§2.2)
    # ------------------------------------------------------------------

    def skeleton(self, entry: ProjectEntry, prefix: str | None = None,
                 tree_mode: bool = False, limit: int | None = None,
                 include_signatures: bool = True, fresh: bool = False) -> dict[str, Any]:
        """Whole-project or per-subtree structural map (design §2.1).

        Manifest only — files joined with symbols ordered by start_line.
        Zero re-parsing. Pre-v3 manifests simply have NULL signatures (and
        and the caller emits MIGRATION_HINT when a migration just happened).
        """
        if not self.skip_stale_check:
            self.maybe_refresh(entry.slug, entry.path, force=fresh)
        m = self.manifest_for(entry.slug)
        try:
            files, symbols = m.symbols_with_files()
        finally:
            m.close()
        if prefix:
            prefix = prefix.rstrip("/")
            symbols = [s for s in symbols if s.file == prefix
                       or s.file.startswith(prefix + "/")]
        files = {p: f for p, f in files.items()
                 if not prefix or p == prefix or p.startswith(prefix + "/")}
        by_file: dict[str, list[SymbolRow]] = {}
        for s in symbols:
            by_file.setdefault(s.file, []).append(s)
        if tree_mode:
            return self._tree_projection(entry, sorted(files), by_file)
        out_files: list[dict[str, Any]] = []
        for path in sorted(files):
            f = files[path]
            rows = by_file.get(path, [])
            if limit is not None:
                rows = rows[:limit]
            out_files.append({
                "file": path,
                "lang": _lang_from_path(path),
                "size": f.size,
                "total_symbols": len(by_file.get(path, [])),
                "symbols": [
                    {"name": s.name, "type": s.symbol_type,
                     "start_line": s.start_line, "end_line": s.end_line,
                     "signature": s.signature if include_signatures else None}
                    for s in rows
                ],
            })
        return {"project": entry.path, "files": out_files}

    @staticmethod
    def _tree_projection(
            entry: ProjectEntry,
            file_paths: list[str],
            by_file: dict[str, list[SymbolRow]]) -> dict[str, Any]:
        """`--tree` mode (design §2.1): directory tree with per-dir symbol
        count and dominant language. No symbols listed; --limit ignored."""
        dirs: dict[str, dict[str, Any]] = {}
        for path in file_paths:
            parent = os.path.dirname(path).replace(os.sep, "/") or "."
            d = dirs.setdefault(parent, {"symbols": 0, "langs": {}})
            d["symbols"] += len(by_file.get(path, []))
            lang = _lang_from_path(path)
            d["langs"][lang] = d["langs"].get(lang, 0) + 1
        out_dirs = [
            {"dir": path, "symbols": d["symbols"],
             "dominant": (max(sorted(d["langs"]),
                              key=lambda l: d["langs"][l])
                          if d["langs"] else None),
             "langs": dict(sorted(d["langs"].items()))}
            for path, d in sorted(dirs.items())]
        return {"project": entry.path, "dirs": out_dirs}

    def format_skeleton(self, data: dict[str, Any], fmt: str = "text") -> str:
        """Dense skeleton render (design §2.1): one file per group, one
        symbol per line, no prose, no blank lines. `--tree` renders the
        directory-tree projection (per-dir symbol count + dominant language)."""
        if fmt == "json":
            return json.dumps(data, ensure_ascii=False, indent=2)
        if "dirs" in data:
            lines = [f"{d['dir']}/  ({d['symbols']} symbols, "
                     f"{d['dominant'] or 'no code'})" for d in data["dirs"]]
            return "\n".join(lines) if lines else "(no files)"
        lines: list[str] = []
        for f in data["files"]:
            total = f.get("total_symbols", len(f["symbols"]))
            shown = len(f["symbols"])
            header = (f"{f['file']}  ({f['lang']}, {total} symbols)"
                      if shown == total else
                      f"{f['file']}  ({f['lang']}, {total} symbols, "
                      f"showing {shown})")
            lines.append(header)
            for s in f["symbols"]:
                sig = f"  {s['signature']}" if s.get("signature") else ""
                lines.append(
                    f"  {s['type']} {s['name']}:{s['start_line']}-"
                    f"{s['end_line']}{sig}")
        return "\n".join(lines) if lines else "(no files)"

    def outline(self, entry: ProjectEntry, file: str,
                include_docstrings: bool = False, fresh: bool = False) -> dict[str, Any]:
        """One file: declarations, signatures, optional docstrings (§2.2).

        FILE is relative to the project root (manifest path space); absolute
        paths are normalized by stripping the project root prefix. Data
        source: Manifest.symbols_for_file + schema-v3 signature. --docstrings
        reads the declaration's first lines from disk — the only on-demand
        source read in the design.
        """
        rel = os.path.relpath(
            os.path.normpath(file if os.path.isabs(file)
                             else os.path.join(entry.path, file.lstrip("/"))),
            entry.path).replace(os.sep, "/")
        if rel.startswith(".."):
            raise ValueError(f"error: path outside registered project: {file}")
        if not self.skip_stale_check:
            self.maybe_refresh(entry.slug, entry.path, force=fresh)
        m = self.manifest_for(entry.slug)
        try:
            rows = m.symbols_for_file(rel)
        finally:
            m.close()
        if not rows and not self.registry_has_file(entry, rel):
            raise ValueError(f"error: file not indexed: {file}")
        src_lines: list[str] = []
        if include_docstrings:
            abs_path = os.path.join(entry.path, rel)
            if os.path.isfile(abs_path):
                # Bounded read: docstrings live within decl+3 lines, so
                # cap at the last declaration's window, not the whole file.
                max_line = max((r.end_line for r in rows), default=0) + 4
                with open(abs_path, encoding="utf-8", errors="replace") as fh:
                    src_lines = []
                    for _ in range(max_line):
                        line = fh.readline()
                        if not line:
                            break
                        src_lines.append(line.rstrip("\n"))
        out = []
        for r in rows:
            d: dict[str, Any] = {
                "name": r.name, "type": r.symbol_type,
                "start_line": r.start_line, "end_line": r.end_line,
                "signature": r.signature,
            }
            if include_docstrings and src_lines:
                doc = self._first_docstring(src_lines, r.start_line)
                if doc:
                    d["doc"] = doc
            out.append(d)
        return {"file": rel, "declarations": out}

    @staticmethod
    def _first_docstring(lines: list[str], start_line: int) -> str | None:
        """First docstring/comment line from the decl line onward (bounded:
        decl line + 3), truncated to ~100 chars."""
        for i in range(start_line, min(start_line + 4, len(lines) + 1)):
            stripped = lines[i - 1].strip()
            if not stripped:
                continue
            if stripped.startswith(('"""', "'''", '"', "'", "#", "//", "/*")):
                cleaned = stripped.strip('"\'')
                cleaned = cleaned.lstrip("#/ ").strip()
                return cleaned[:100] or None
            if i == start_line:
                continue  # the declaration line itself
            return None  # body code before any docstring → none
        return None

    def registry_has_file(self, entry: ProjectEntry, rel: str) -> bool:
        m = self.manifest_for(entry.slug)
        try:
            return m.get_file(rel) is not None
        finally:
            m.close()

    # ------------------------------------------------------------------
    # stale-safe line ranges (CI-03)
    # ------------------------------------------------------------------

    def file_changed_since_index(self, entry: ProjectEntry, rel: str) -> bool:
        """True when the live file's content hash differs from the manifest.

        The stat fast-path (size/mtime_ns/inode) short-circuits the common
        unchanged case without hashing; PARANOID_HASH=1 disables it.
        """
        m = self.manifest_for(entry.slug)
        try:
            row = m.get_file(rel)
        finally:
            m.close()
        if row is None:
            return False  # unknown to the manifest; nothing to compare
        abs_path = os.path.join(entry.path, rel)
        try:
            st = os.stat(abs_path)
        except OSError:
            return True  # unreadable/missing -> treat as changed
        if (row.mtime_ns is not None and row.inode is not None
                and os.environ.get("PARANOID_HASH") not in ("1", "true")
                and (st.st_size, st.st_mtime_ns, st.st_ino)
                == (row.size, row.mtime_ns, row.inode)):
            return False
        import hashlib
        h = hashlib.sha256()
        try:
            with open(abs_path, "rb") as f:
                for block in iter(lambda: f.read(65536), b""):
                    h.update(block)
        except OSError:
            return True
        return "sha256:" + h.hexdigest() != row.content_hash

    def code_context(self, entry: ProjectEntry, file: str,
                     start_line: int | None = None,
                     end_line: int | None = None,
                     symbol: str | None = None,
                     context_lines: int = 0) -> dict[str, Any]:
        """Stale-safe get_code_context (CI-03).

        Symbol mode: when the file changed since indexing, re-resolve the
        symbol against the LIVE file with tree-sitter and return fresh line
        ranges (stale stays false — the answer is current). Line mode: the
        requested lines are returned as-is with ``stale: true`` so the
        caller knows the numbers may be shifted.
        """
        rel = file.lstrip("/")
        abs_path = os.path.normpath(os.path.join(entry.path, rel))
        if not abs_path.startswith(os.path.normpath(entry.path) + os.sep):
            raise ValueError(f"error: path outside registered project: {file}")
        if not os.path.isfile(abs_path):
            raise ValueError(f"error: file not found: {abs_path}")

        changed = self.file_changed_since_index(entry, rel)
        ranges: list[tuple[int, int]] = []
        re_resolved = False
        if symbol:
            if changed:
                # Re-resolve against the live file; the manifest rows are
                # stale by definition here.
                with open(abs_path, encoding="utf-8", errors="replace") as f:
                    live_text = f.read()
                live_chunks = ts_chunker.chunk_text(rel, live_text)
                fresh_rows = [
                    c for c in live_chunks
                    if c.symbol == symbol or
                    (c.symbol and c.symbol.endswith("." + symbol))]
                if fresh_rows:
                    ranges = [(max(1, c.start_line - context_lines),
                               c.end_line + context_lines)
                              for c in fresh_rows[:5]]
                    re_resolved = True
            if not ranges:
                core_manifest = self.manifest_for(entry.slug)
                try:
                    rows = [r for r in core_manifest.find_symbols(
                        symbol, substring=False) if r.file == rel]
                finally:
                    core_manifest.close()
                if not rows:
                    raise ValueError(
                        f"error: symbol {symbol!r} not indexed in {rel}")
                ranges = [(max(1, r.start_line - context_lines),
                           r.end_line + context_lines) for r in rows[:5]]
        elif start_line is not None:
            end = end_line if end_line is not None else start_line
            ranges.append((max(1, start_line - context_lines),
                           end + context_lines))
        else:
            raise ValueError(
                "error: provide --start-line/--end-line or --symbol")

        with open(abs_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        segments = []
        for s, e in ranges:
            if s > total:
                segments.append({"start_line": s, "end_line": e,
                                 "error": f"start_line {s} beyond end of "
                                          f"file ({total} lines)"})
                continue
            s2, e2 = max(1, s), min(total, e)
            segments.append({
                "start_line": s, "end_line": e,
                "lines": [f"{i:>5}| {lines[i - 1].rstrip()}"
                          for i in range(s2, e2 + 1)],
            })
        stale = changed and not re_resolved
        if stale:
            logger.warning(
                "file changed since last index; line numbers may be shifted")
        return {
            "file": rel,
            "stale": stale,
            "re_resolved": re_resolved,
            "segments": segments,
        }

    def format_outline(self, data: dict[str, Any], fmt: str = "text") -> str:
        """Dense outline render (§2.2): one declaration per line."""
        if fmt == "json":
            return json.dumps(data, ensure_ascii=False, indent=2)
        lines: list[str] = []
        for d in data["declarations"]:
            sig = f"  {d['signature']}" if d.get("signature") else ""
            lines.append(
                f"{d['type']} {d['name']}:{d['start_line']}-{d['end_line']}{sig}")
            if d.get("doc"):
                lines.append(f"  {d['doc']}")
        return "\n".join(lines) if lines else "(no declarations)"


MIGRATION_HINT = (
    "note: project manifest was upgraded from an older schema — run "
    "reindex_project once to populate the symbol index"
)


# ---------------------------------------------------------------------------
# Token-reduction ops (subcommand design §2.1/§2.2): manifest-only, no parsing
# at query time. Each op has a format_* twin following format_hits' pattern.
# ---------------------------------------------------------------------------

def _lang_from_path(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "py": "python", "js": "javascript", "ts": "typescript",
        "rs": "rust", "go": "go", "java": "java", "c": "c", "cpp": "cpp",
        "cc": "cpp", "h": "c", "hpp": "cpp", "cs": "csharp", "rb": "ruby",
        "php": "php", "sh": "bash", "md": "markdown", "json": "json",
        "yaml": "yaml", "yml": "yaml", "toml": "toml",
    }.get(ext, ext or "text")
