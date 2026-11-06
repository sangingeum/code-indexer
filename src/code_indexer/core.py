"""Core operations for the code-indexer (design ruling §3).

All indexing/search/registry logic lives here; ``server.py`` (MCP adapters)
and ``cli.py`` (typer app) are thin surfaces over these functions, and every
tool/subcommand maps 1:1 to an op here. One-shot process model: no daemon.
Reads detect staleness, never repair it: an answer describes the index as of
``meta.last_indexed`` and reports ``fresh``, ``stale`` or ``unknown``, the
last when the check cannot run. Only an explicit refresh writes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any

from .config import Config, load_config
from .errors import (BackendError, ExtensionUnavailableError, InternalError,
                     LockedError)
from .extensions import SEMANTIC_EXTENSION_MESSAGE
from .fingerprint import (ConfigError, configured_values, probe_free_mismatches,
                          current_version_stamp, read_fingerprint,
                          rows_staleness_warning)
from .locks import project_lock
from .manifest import SCHEMA_VERSION, Manifest, SymbolRow
from . import ranking
from . import staleness
from . import ts_chunker
from .registry import ProjectEntry, Registry
from .scanner import (ScanBudget, ScanBudgetExceeded, ScannedFile,
                      scan_project)

if TYPE_CHECKING:  # annotations only: no client library is imported here
    from .embedder import Embedder
    from .indexer import Indexer
    from .store import Store

logger = logging.getLogger("code-indexer.core")

# Wall budget for one unscoped query invocation (ADR-0003 section 6): the
# fan-out stops at this wall and reports the remaining projects as skipped
# with reason=budget instead of running for minutes.
QUERY_BUDGET_DEFAULT_S = 30.0

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

# The third value of the same vocabulary: the verdict could not be reached
# (the running configuration needs an embedder dimension probe and the
# embedder is unreachable). "Cannot verify" is its own value — never folded
# into the pass state and never into the ok state, so an operator can tell
# "the index is fine" from "I could not tell".
UNVERIFIED = "unverified"
# The reason a read path gives: the recorded dimension has no configured
# counterpart to compare against without dialling the embedding service, and
# a read never dials (ADR-0001 section 1, last rule).
UNVERIFIED_REASON = "embedding-dimension-not-compared"


def _merge_symbol_ranges(
        ranges: list[tuple[int, int]]) -> list[tuple[int, int, int]]:
    """One contiguous span per run of ranges that overlap or touch.

    Symbol mode answers "where is this definition". Extraction stores a
    decorated definition as two rows — the decorator line and the declaration
    line, with the same end line — so one definition would otherwise surface
    as two overlapping ranges and a caller counting segments would count it
    twice. Ranges of the one requested name that overlap or are adjacent are
    merged into a single span; genuinely disjoint ranges stay separate. The
    merge is part of the answer, never a silent convenience.

    Callers pass the rows' OWN ranges — the caller merges first and applies
    ``--context-lines`` padding to each surviving span afterwards. Padding
    before the merge would let two definitions a few lines apart touch and
    collapse into one span covering the code between them.

    Returns ``(start, end, symbol_rows)`` per span, ordered by start line:
    ``symbol_rows`` is the number of rows folded into that span, so a caller
    can report the merge instead of leaving it implicit.
    """
    merged: list[tuple[int, int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            prev_start, prev_end, folded = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end), folded + 1)
        else:
            merged.append((start, end, 1))
    return merged


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
        # The two clients of the semantic extra are built on first use, never
        # here: a core command constructs neither, and an absent extra costs
        # one actionable line instead of a traceback (ADR-0001 section 1).
        # They stay plain attributes behind setters, so a test seam can
        # replace one after construction exactly as before.
        self._embedder: Embedder | None = None
        self._store: Store | None = None
        self._indexer: Indexer | None = None
        # Per-process staleness cache: slug -> last-scan monotonic time.
        self._last_scan: dict[str, float] = {}
        # Indexing state per slug (state, error, last_result) for status.
        self._index_state: dict[str, dict[str, Any]] = {}

    # -- lazily constructed clients of the semantic extra ------------------

    @property
    def embedder(self) -> Embedder:
        """The Ollama embedder, constructed on first use."""
        if self._embedder is None:
            self._embedder = self._make_embedder()
        return self._embedder

    @embedder.setter
    def embedder(self, value: Embedder | None) -> None:
        self._embedder = value

    def _make_embedder(self) -> Embedder:
        try:
            from .embedder import Embedder as _Embedder
        except ImportError as exc:  # the semantic extra is not installed
            raise ExtensionUnavailableError(
                SEMANTIC_EXTENSION_MESSAGE) from exc
        return _Embedder(self.cfg.ollama_url, self.cfg.embed_model,
                         batch_size=self.cfg.embed_batch,
                         timeout=self.cfg.ollama_timeout,
                         cache=self._embed_cache())

    @property
    def store(self) -> Store:
        """The Qdrant store, constructed on first use."""
        if self._store is None:
            self._store = self._make_store()
        return self._store

    @store.setter
    def store(self, value: Store | None) -> None:
        self._store = value

    def _make_store(self) -> Store:
        try:
            from .store import Store as _Store
        except ImportError as exc:  # the semantic extra is not installed
            raise ExtensionUnavailableError(
                SEMANTIC_EXTENSION_MESSAGE) from exc
        return _Store(self.cfg.qdrant_url, upsert_batch=self.cfg.upsert_batch)

    @property
    def indexer(self) -> Indexer:
        """The pass engine over the current embedder/store pair."""
        if self._indexer is None:
            from .indexer import Indexer as _Indexer
            self._indexer = _Indexer(self.cfg, self.embedder, self.store)
        return self._indexer

    @indexer.setter
    def indexer(self, value: Indexer | None) -> None:
        self._indexer = value

    @staticmethod
    def _embed_cache():
        """Content-addressed embedding cache shared by all embed calls
        (unchanged chunks and queries skip Ollama); disabled when
        EMBED_CACHE=0."""
        if os.environ.get("EMBED_CACHE", "1") in ("0", "false"):
            return None
        from .embed_cache import EmbedCache
        return EmbedCache(load_config().index_root)

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

    def refresh(self, entry: ProjectEntry) -> dict[str, Any]:
        """One explicit incremental pass under the per-project flock.

        Called only when the caller asked for it (``--refresh``) — never by a
        plain read. A flock miss is a hard failure, not a quiet stale answer:
        an explicit request for fresh data must not print an old answer under
        a "refreshed" label. Returns the pass result; raises LockedError (exit
        5) when another process holds the lock and InternalError when the
        pass itself failed.
        """
        result = self.run_index(entry.slug, entry.path, force=False,
                                verbose=True)
        if result.get("state") == "indexing":
            raise LockedError("indexing in progress")
        if result.get("state") == "error":
            raise InternalError(
                f"index pass failed for {entry.path}: {result.get('error')}")
        return result

    # ------------------------------------------------------------------
    # staleness (detect, never repair)
    # ------------------------------------------------------------------

    def staleness(self, entry: ProjectEntry) -> staleness.StalenessVerdict:
        """The index's verdict against the tree: fresh, stale or unknown.

        Pure read: the manifest is opened only to read rows and meta, the
        tree is walked with the scanner's own include/exclude rules and stat
        fast-path, and only files whose stat identity changed are hashed. No
        service is contacted. The walk carries its own budget so a very large
        tree degrades to ``unknown`` rather than a long silence.
        """
        mdir = os.path.join(self.cfg.index_root, entry.slug, "manifest.db")
        # The manifest's own fields belong to an ``unknown`` verdict too: an
        # answer reports the pass it describes even when the check cannot run.
        last_indexed: str | None = None
        index_revision: str | None = None
        if os.path.isfile(mdir):
            m = Manifest(mdir)
            try:
                last_indexed = staleness.iso_timestamp(m.get_meta("last_indexed"))
                index_revision = (m.get_meta("index_revision")
                                  or m.get_meta("last_indexed"))
            finally:
                m.close()
        if not os.path.isdir(entry.path):
            return staleness.unknown(f"project path absent: {entry.path}",
                                     last_indexed=last_indexed,
                                     index_revision=index_revision)
        if not os.path.isfile(mdir):
            return staleness.unknown("no index manifest for this project",
                                     last_indexed=last_indexed,
                                     index_revision=index_revision)
        m = Manifest(mdir)
        try:
            indexed = m.all_files()
            scope = [ln for ln in (m.get_meta("scope_include") or "")
                     .splitlines() if ln.strip()]
        finally:
            m.close()
        if not indexed or last_indexed is None:
            return staleness.unknown("no indexing pass has completed",
                                     last_indexed=last_indexed,
                                     index_revision=index_revision)
        previous = {
            path: (row.size, row.mtime_ns, row.inode, row.content_hash)
            for path, row in indexed.items()
            if row.mtime_ns is not None and row.inode is not None
        }
        budget = ScanBudget(max_files=staleness.SCAN_BUDGET_FILES,
                            max_seconds=staleness.SCAN_BUDGET_SECONDS)
        try:
            scanned: list[ScannedFile] = scan_project(
                entry.path, max_file_bytes=self.cfg.max_file_bytes,
                previous=previous, include=scope or None, budget=budget)
        except ScanBudgetExceeded as exc:
            return staleness.unknown(str(exc), last_indexed=last_indexed,
                                     index_revision=index_revision)
        except OSError as exc:
            return staleness.unknown(f"tree unreadable: {exc}",
                                     last_indexed=last_indexed,
                                     index_revision=index_revision)
        return staleness.detect(indexed, scanned, last_indexed=last_indexed,
                                index_revision=index_revision)

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
        """Human-readable per-project summary (one line, ``key=value``).

        The text renderer of ``status_fields``; both surfaces come from the
        same read so the text and JSON answers can never disagree.
        """
        fields = self.status_fields(entry)
        parts = [f"path={fields['path']}", f"slug={fields['slug']}",
                 f"state={fields['state']}", f"files={fields['files']}",
                 f"chunks={fields['chunks']}",
                 f"last_indexed={fields['last_indexed']}"]
        if fields.get("reason"):
            parts.append(f"reason={fields['reason']}")
        if fields.get("note"):
            parts.append(f"note={fields['note']}")
        return " ".join(parts)

    def status_fields(self, entry: ProjectEntry) -> dict[str, Any]:
        """Structured per-project summary: the fields both renderers carry.

        Manifest and registry reads only. Collection membership is a service
        verdict this read deliberately does not establish (ADR-0001 section 1,
        last rule): ``doctor`` owns the collection-versus-registry check, and
        a read command never dials a store to answer a question the manifest
        does not cover.
        """
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
        reason = None
        note = None
        if last_indexed is not None and not fp["ok"]:
            st = NEEDS_REINDEX
            reason = fp["reason"]
        if fp["recorded"] is None and last_indexed is not None:
            note = "fingerprint backfills on next index pass"
        return {
            "path": entry.path,
            "slug": entry.slug,
            "state": st,
            "files": file_count,
            "chunks": chunk_count,
            "last_indexed": last_indexed or "never",
            "reason": reason,
            "note": note,
        }

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

    def configured_fingerprint_values(self) -> dict[str, str]:
        """Fingerprint keys this process can state without a service."""
        return configured_values(self.cfg.embed_model)

    def fingerprint_status(self, entry: ProjectEntry) -> dict[str, Any]:
        """{'ok': bool, 'state': str, 'reason': str|None, 'recorded': str|None}.

        ``state`` is the fingerprint vocabulary: ``ok`` (the manifest is
        legacy and will be backfilled), ``needs-reindex`` (a key this process
        can state from configuration alone differs), or ``unverified`` (the
        probe-free keys match, but the recorded embedding dimension has no
        configured counterpart to compare against without dialling the
        service — and a read never dials). ``ok`` stays exactly "no KNOWN
        mismatch", so the query-path gate (``assert_fingerprint_ok``) refuses
        what it always refused, and ``unverified`` is never folded into
        ``ok``: a caller that needs the dimension comparison must ask for it
        explicitly.

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
        if recorded is None:
            return {"ok": True, "state": "ok", "reason": None,
                    "recorded": None}
        diffs = probe_free_mismatches(
            recorded, self.configured_fingerprint_values())
        if not diffs:
            return {"ok": True, "state": UNVERIFIED, "reason": UNVERIFIED_REASON,
                    "recorded": recorded.describe()}
        return {"ok": False, "state": NEEDS_REINDEX,
                "reason": "; ".join(diffs),
                "recorded": recorded.describe()}

    def assert_fingerprint_ok(self, entry: ProjectEntry) -> None:
        """Raise ConfigError when the index predates the current config.

        Query paths call this before spending an embedding round trip on a
        mismatched index; --skip-stale-check does NOT bypass it (a wrong-model
        query is wrong regardless of staleness). The comparison uses the keys
        this process can state without a service, so the gate itself dials
        nothing and an ``unverified`` project is not turned into a refusal.
        """
        status = self.fingerprint_status(entry)
        if not status["ok"]:
            raise ConfigError(
                f"index for {entry.path} does not match the "
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
    # stored-row staleness (manifest-only surfaces)
    # ------------------------------------------------------------------

    def rows_staleness(self, entry: ProjectEntry) -> str | None:
        """One-line warning for a manifest-only surface, or None.

        The manifest-only commands (find-references, find-callers,
        find-callees, deps, skeleton, outline) read stored rows produced by
        whatever extraction code built the index. When the version-ordered
        fingerprint keys show a different build, those rows may not be what
        this code would extract, so the command says so once instead of
        silently answering from rows it cannot vouch for. A WARNING, never a
        refusal: refusing would remove the tool's usefulness on exactly the
        index a user is inspecting. Only the version keys are consulted —
        the embedding-configuration keys affect vectors, not stored rows
        (see fingerprint.rows_staleness_warning).
        """
        mdir = os.path.join(self.cfg.index_root, entry.slug, "manifest.db")
        if not os.path.isfile(mdir):
            return None
        m = Manifest(mdir)
        try:
            recorded = read_fingerprint(m)
        finally:
            m.close()
        if recorded is None:
            # Legacy/absent fingerprint: it backfills on the next pass and is
            # not a mismatch, so there is nothing to warn about.
            return None
        return rows_staleness_warning(recorded, current_version_stamp())

    # ------------------------------------------------------------------
    # orphan sweep
    # ------------------------------------------------------------------

    def sweep_orphans(self, entry: ProjectEntry, project_path: str,
                      apply: bool = False) -> dict[str, Any]:
        """Find (and optionally remove) residue rows for files that are gone.

        A sweep candidate is a row in ``refs``/``imports``/``symbols`` whose
        ``file`` has no ``files`` row AND whose file is absent from the
        project's working tree. The tree check is DEFENCE IN DEPTH, not a
        demonstrably necessary predicate — "no files row" and "the file is
        gone" are different conditions and the schema does not tie them
        together, so the guard is a cheap second opinion. The real safety
        property is the dry run plus the reported count: ``apply=False``
        (the default) prints what it WOULD clear and changes nothing.

        Deletion, when asked for, is one transaction (``Manifest.delete_files``)
        that reports the count removed. No implicit purge is added to the
        index pass — that would change pass behaviour and is a separate owner
        decision.
        """
        m = self.manifest_for(entry.slug)
        try:
            orphan_map = m.orphan_rows()
            gone = {f: counts for f, counts in orphan_map.items()
                    if not os.path.exists(os.path.join(project_path, f))}
            kept = sorted(set(orphan_map) - set(gone))
            per_table = {table: sum(c.get(table, 0) for c in gone.values())
                         for table in m._FILE_KEYED_TABLES}
            files = sorted(gone)
            removed = 0
            if apply and files:
                removed = sum(sum(c.values()) for c in gone.values())
                m.delete_files(files)
        finally:
            m.close()
        return {"apply": apply, "files": files, "per_table": per_table,
                "total": sum(per_table.values()), "removed": removed,
                "present_on_disk_kept": kept}

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------

    def _query_vector(self, query: str) -> list[float]:
        """ONE embedding attempt for the query, short timeout, no retry chain.

        The vector is computed once per invocation and reused for every
        project, so fan-out cost is bounded by project count in the store
        calls only — never by a retry chain. A failure is a BackendError
        (exit 3): the service is absent, not the answer empty.
        """
        qtext = query
        qtext_fn = getattr(self.embedder, "query_instruction_text", None)
        if qtext_fn is not None:
            qtext = qtext_fn(query)
        once = getattr(self.embedder, "embed_query_once", None)
        try:
            if once is not None:
                return once(qtext)
            return self.embedder.embed([qtext])[0]
        except Exception as exc:  # noqa: BLE001 — the query path's one attempt
            raise BackendError(
                f"embedding service unavailable: {exc}") from exc

    def search_one(self, entry: ProjectEntry, query: str, limit: int,
                   file_filter: str | None,
                   symbol_type: str | None = None,
                   language: str | None = None,
                   ranking_mode: str = "vector",
                   rerank: str | None = None,
                   vector: list[float] | None = None) -> list[dict[str, Any]]:
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
        # Query-side embedding: the instruction prefix applies ONLY
        # here; document embeddings stay raw. The cache key is the exact
        # text, so instructed and raw embeddings never collide. Embedder
        # duck-typing: stubs without query_instruction_text embed verbatim.
        if vector is None:
            vector = self._query_vector(query)
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
                # Staleness provenance for get_code_context.
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
               mode: str = "dense",
               per_file: int = 0,
               max_chars: int | None = None,
               max_tokens: int | None = None,
               merge: bool = True,
               rerank: str | None = None,
               all_projects: bool = False,
               budget_s: float | None = None) -> dict[str, Any]:
        """Semantic search across one project, or (``all_projects``) all.

        A read: it never indexes, never calls a staleness pass and never
        retries an embedding. Resolution defaults to the containing project
        inferred from the invocation path; fan-out requires ``all_projects``.
        The query makes ONE embedding attempt (short timeout) and reuses its
        vector for every project, so cost is bounded by construction. The
        fan-out stops at ``budget_s`` (default 30 s) and reports the
        remaining projects as skipped with ``reason=budget``.

        ``skip_refresh`` is accepted and inert: reads no longer refresh, and
        ``--refresh`` is the explicit write trigger.

        Raises ValueError with a user-facing message on resolution errors,
        ConfigError (a rebuild) from the fingerprint gate before any
        embedding work, and BackendError when no project could answer.

        Result shaping (token-budget work item): same-file overlapping/adjacent
        hits are merged, at most ``per_file`` hits per file are kept (0 = no
        generic cap), and ``max_chars``/``max_tokens`` trim the lowest-ranked
        hits. Returns {'hits': [...], 'dropped': int, 'skipped': [...],
        'partial': bool} so adapters can report trimming and a partial answer.
        """
        if mode not in ("dense", "lexical", "hybrid"):
            raise ValueError(f"error: unknown search mode: {mode}")
        if all_projects:
            entries = self.registry.list_projects()
            if not entries:
                raise ValueError("error: no projects registered")
        elif project:
            entry, err = self.resolve_entry(project)
            if entry is None:
                raise ValueError(err)
            entries = [entry]
        else:
            entry, err = self.resolve_entry(None)
            if entry is None:
                raise ValueError(err)
            entries = [entry]
        per_project: dict[str, list[dict[str, Any]]] = {}
        # Projects whose store could not answer (or that the wall budget did
        # not reach). One dead registration must not kill the whole search:
        # it is skipped, named, and reported so the caller can say the answer
        # is partial.
        skipped: list[dict[str, str]] = []
        lexical_only = mode == "lexical"
        hybrid = mode == "hybrid"
        vector: list[float] | None = None
        if not lexical_only:
            vector = self._query_vector(query)
        budget = QUERY_BUDGET_DEFAULT_S if budget_s is None else budget_s
        started = time.monotonic()
        for index, entry in enumerate(entries):
            if time.monotonic() - started > budget:
                skipped.extend(
                    {"project": e.path, "reason": "budget"}
                    for e in entries[index:])
                break
            # Fingerprint gate BEFORE any embedding work: a query against a
            # wrong-model index is wrong regardless of staleness, and
            # --skip-stale-check must not bypass it. Pure-lexical mode needs
            # no Ollama round trip, so the gate (which needs the dim probe)
            # is skipped there — a stale fingerprint cannot corrupt a bm25
            # text match.
            #
            # FATAL BY DESIGN, and deliberately OUTSIDE the per-project
            # try/except below: a wrong-model index answers wrong SILENTLY,
            # which is worse than a loud failure. A needs-reindex project
            # aborts the whole run; it must never be folded into the
            # skipped-with-a-warning path. Do not "fix" this into silence.
            if not lexical_only:
                self.assert_fingerprint_ok(entry)
            try:
                if lexical_only:
                    per_project[entry.path] = self._lexical_hits(
                        entry, query, limit, rerank)
                    continue
                dense_hits = self.search_one(
                    entry, query, limit, file_filter,
                    symbol_type=symbol_type, language=language,
                    ranking_mode=ranking_mode, rerank=rerank, vector=vector)
                if hybrid:
                    per_project[entry.path] = self._fuse_lexical_dense(
                        entry, query, dense_hits, limit)
                else:
                    per_project[entry.path] = dense_hits
            except Exception as exc:  # noqa: BLE001 — never fatal per project
                logger.warning(
                    "search: skipping %s — its index could not answer: %s",
                    entry.path, exc)
                skipped.append({"project": entry.path, "reason": str(exc)})
        if skipped:
            logger.warning(
                "search: %d of %d project(s) skipped: %s", len(skipped),
                len(entries), ", ".join(s["project"] for s in skipped))
        if not per_project:
            # Nothing answered at all. This is a real failure, not an empty
            # success: there is no partial result to return. A dead backend
            # is a missing install (exit 3), never an empty result.
            detail = "; ".join(f"{s['project']}: {s['reason']}"
                               for s in skipped)
            raise BackendError(
                f"search failed on every project ({detail})")

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
        # "skipped" names the projects whose store could not answer (see the
        # per-project isolation above); the hit contract in format_hits JSON
        # is unchanged — this rides on the Core result dict only.
        return {"hits": all_hits[:max(1, limit)], "dropped": dropped,
                "skipped": skipped, "partial": bool(skipped)}

    # ------------------------------------------------------------------
    # lexical / hybrid retrieval (FTS5 + RRF)
    # ------------------------------------------------------------------

    def _lexical_hits(self, entry: ProjectEntry, query: str, limit: int,
                      rerank: str | None = None) -> list[dict[str, Any]]:
        """bm25-ranked hits from the manifest's FTS table, mapped to the hit
        contract (file, line range via the manifest symbols, score=rank)."""
        m = self.manifest_for(entry.slug)
        try:
            fts_rows = m.fts_search(query, limit * 3)
            out: list[dict[str, Any]] = []
            for rank, row in enumerate(fts_rows, 1):
                file = row["file"]
                sym_rows = m.symbols_for_file(file)
                sym = row.get("symbol")
                # Anchor the hit at the matching symbol's range when the FTS
                # symbol column names one, else the file head.
                start, end = 1, max(1, max(
                    (r.end_line for r in sym_rows), default=1))
                named = [r for r in sym_rows
                         if sym and sym.split(", ")[0] in r.name]
                if named:
                    start, end = named[0].start_line, named[0].end_line
                    symbol = named[0].name
                    symbol_type = named[0].symbol_type
                else:
                    symbol = None
                    symbol_type = None
                out.append({
                    "project": entry.path,
                    "file": file,
                    "score": round(1.0 / (60 + rank), 6),  # RRF-style score
                    "symbol": symbol,
                    "symbol_type": symbol_type,
                    "lang": _lang_from_path(file),
                    "start_line": start,
                    "end_line": end,
                    "snippet": (f"lexical match: {file}"
                                + (f" ({symbol})" if symbol else "")),
                    "match": "lexical",
                })
        finally:
            m.close()
        if rerank and rerank != "none":
            from .reranker import select_reranker
            out = select_reranker(rerank).rerank(out, query)
        return out

    def _fuse_lexical_dense(self, entry: ProjectEntry, query: str,
                            dense_hits: list[dict[str, Any]],
                            limit: int) -> list[dict[str, Any]]:
        """RRF-fuse the FTS bm25 ranking with the dense ranking (k=60).

        Exact-symbol shortcut: an identifier-shaped query also runs
        find_symbol and pins exact matches first, labelled match=symbol.
        """
        lexical = self._lexical_hits(entry, query, limit)
        fused = ranking.rrf_fuse({"dense": dense_hits, "lexical": lexical},
                                 limit * 3)
        fused = [dict(h, match=h.get("match") or "fused") for h in fused]
        ident = query.strip()
        if re.fullmatch(r"[A-Za-z_][\w:.]*", ident):
            m = self.manifest_for(entry.slug)
            try:
                # find_symbols caps this read at 25 rows, and the pin below
                # takes 3. The cap cannot change that answer: the read's order
                # key is total, so 3 of 25 and an uncapped read pin the same
                # three (3 of 25 under a total key). The get-code-context
                # --symbol lookup no longer shares this shape: it filters name
                # and file in one uncapped read (issue #23).
                exact = [r for r in m.find_symbols(ident, substring=False)]
            finally:
                m.close()
            if exact:
                pinned = [{
                    "project": entry.path, "file": r.file,
                    "score": 2.0, "symbol": r.name,
                    "symbol_type": r.symbol_type,
                    "lang": _lang_from_path(r.file),
                    "start_line": r.start_line, "end_line": r.end_line,
                    "snippet": f"exact symbol match: {r.name}",
                    "match": "symbol",
                } for r in exact[:3]]
                fused = pinned + fused
        return fused

    def format_hits(self, hits: list[dict[str, Any]], fmt: str = "text",
                dropped: int = 0, context_lines: int = 0,
                skipped: list[dict[str, Any]] | None = None,
                partial: bool = False,
                staleness_block: dict[str, Any] | None = None) -> str:
        """Format search hits (stable field contract).

        ``text`` — the original verbose two-line-per-hit render (default;
        kept for compatibility with existing agent workflows).
        ``compact`` — one line per hit: ``path:start-end  symbol  score``;
        the token-budget agent loop (search compact -> get-code-context).
        ``json`` — the JSON contract, which carries ``skipped`` /
        ``skipped_count`` / ``partial`` and the additive staleness block, so
        a consumer can tell the result is partial.
        ``dropped`` > 0 appends one trailing truncation line in text/compact
        mode, and a non-empty ``skipped`` appends one trailing partial-result
        line (the documented exceptions to single-line stderr reporting).
        """
        if fmt == "json":
            payload: dict[str, Any] = {
                "schema": 1,
                "hits": hits, "truncated": dropped > 0, "dropped": dropped,
                "skipped": skipped or [],
                "skipped_count": len(skipped or []),
                "partial": partial or bool(skipped),
            }
            payload.update(staleness_block or {})
            return json.dumps(payload, ensure_ascii=False, indent=2)
        if not hits:
            return "no results"
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
        if skipped:
            lines.append(
                f"({len(skipped)} project(s) skipped — their index could not "
                "answer, so this result is partial: "
                + ", ".join(s["project"] for s in skipped) + ")")
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
                           rerank: str | None = None,
                           mode: str = "dense",
                           all_projects: bool = False,
                           budget_s: float | None = None,
                           staleness_block: dict[str, Any] | None = None) -> str:
        """Answer text for one search. Failures PROPAGATE.

        The old shape caught every ValueError and printed the message as the
        answer on stdout with exit 0, which made a total backend failure
        indistinguishable from an empty result. This surface raises (the CLI
        maps it onto the taxonomy and stderr), so "no results" is only ever
        an answer when the search actually ran.
        """
        result = self.search(query, project=project, limit=limit,
                             file_filter=file_filter,
                             symbol_type=symbol_type, language=language,
                             ranking_mode=ranking_mode,
                             skip_refresh=skip_refresh, per_file=per_file,
                             max_chars=max_chars, max_tokens=max_tokens,
                             rerank=rerank, mode=mode,
                             all_projects=all_projects, budget_s=budget_s)
        return self.format_hits(
            result["hits"], fmt, dropped=result["dropped"],
            context_lines=context_lines, skipped=result.get("skipped", []),
            partial=result.get("partial", False),
            staleness_block=staleness_block)

    # ------------------------------------------------------------------
    # reference surfaces (find-references): the single reference store
    # ------------------------------------------------------------------

    # Reported relationship filter -> stored relationship values in the ONE
    # reference store. The CLI vocabulary stays calls|includes|inherits|
    # references; the stored vocabulary is call|includes|base_class|type|
    # member|identifier. The mapping lives here so neither spelling leaks.
    _REFERENCE_FILTERS: dict[str, tuple[str, ...]] = {
        "calls": ("call",),
        "includes": ("includes",),
        "inherits": ("base_class",),
        "references": ("type", "member", "identifier"),
    }

    def find_references(self, entry: ProjectEntry, name: str,
                        relationship: str | None = None,
                        limit: int = 25) -> list[dict[str, Any]]:
        """Every reference TO ``name`` from the single reference store.

        One writer (``graph_extract``) puts one row per reference into
        ``refs`` with an honest relationship and confidence: tree-sitter rows
        (``call``/``type``/``base_class``/``member``, confidence ``ast``) for
        the AST languages, and the textual identifier scan (``identifier``)
        plus import/include lines (``includes``), confidence ``heuristic``,
        for every other language. A name that is referenced but never invoked
        as ``name(`` is therefore visible, and a call is stored once, not
        once per store.

        ``relationship`` filters the *reported* vocabulary
        (calls|includes|inherits|references); ``None`` returns every row.
        Rows come back sorted by (file, line, relationship), capped at
        ``limit``.
        """
        if (relationship is not None
                and relationship not in self._REFERENCE_FILTERS):
            raise ValueError(
                f"error: unknown relationship: {relationship} "
                "(expected calls|includes|inherits|references)")
        stored = (self._REFERENCE_FILTERS[relationship]
                  if relationship is not None else None)
        m = self.manifest_for(entry.slug)
        try:
            rows = m.refs_to(name, relationships=stored, limit=limit)
        finally:
            m.close()
        return [{"file": r["file"], "line": r["line"],
                 "from_symbol": r["from_symbol"], "target": r["to_name"],
                 "relationship": r["relationship"],
                 "confidence": r["confidence"],
                 "receiver": r["receiver"]} for r in rows]

    def reference_relationship_counts(self, entry: ProjectEntry, name: str
                                      ) -> list[tuple[str, int]]:
        """(stored relationship, count) for every reference to ``name``,
        unfiltered. One aggregate query — the CLI uses it to say WHY a
        filtered report is empty instead of implying the name is absent."""
        m = self.manifest_for(entry.slug)
        try:
            return m.ref_relationship_counts(name)
        finally:
            m.close()

    def all_symbol_names(self, entry: ProjectEntry) -> set[str]:
        """Names with a definition row (for exact-vs-heuristic labelling)."""
        m = self.manifest_for(entry.slug)
        try:
            return m.all_symbol_names()
        finally:
            m.close()

    # ------------------------------------------------------------------
    # token reduction: skeleton / outline (subcommand design §2.1/§2.2)
    # ------------------------------------------------------------------

    def skeleton(self, entry: ProjectEntry, prefix: str | None = None,
                 tree_mode: bool = False, limit: int | None = None,
                 include_signatures: bool = True) -> dict[str, Any]:
        """Whole-project or per-subtree structural map (design §2.1).

        Manifest only — files joined with symbols ordered by start_line.
        Zero re-parsing, and no index pass: a read answers from the index as
        of ``meta.last_indexed``. Pre-v3 manifests simply have NULL
        signatures (and the caller emits MIGRATION_HINT when a migration just
        happened).
        """
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
            return json.dumps(dict(data, schema=1), ensure_ascii=False,
                              indent=2)
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
                include_docstrings: bool = False) -> dict[str, Any]:
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
    # stale-safe line ranges
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
                     context_lines: int = 0,
                     live: bool = False) -> dict[str, Any]:
        """Stale-safe get_code_context.

        Symbol mode: when the file changed since indexing (or the caller
        passes ``live=True``, the CLI's --fresh), re-resolve the symbol
        against the LIVE file with tree-sitter and return fresh line ranges
        (stale stays false — the answer is current). Line mode: the
        requested lines are returned as-is with ``stale: true`` so the
        caller knows the numbers may be shifted.

        Symbol mode returns EVERY matching symbol row in this file — the
        name and file filters run in one uncapped read, so a row that sorts
        past ``find_symbols``' default cap is still found, and a name with
        more than five matching rows in one file is not shortened
        (issue #23). Rows are not definitions, so the ranges of the requested
        name that OVERLAP OR TOUCH are merged into one contiguous span before
        segments are built (issue #26): a decorated definition is extracted
        as two rows (decorator line + declaration line, same end line) and
        surfaces as ONE segment with one honest range, while genuinely
        disjoint ranges stay separate. The rows' own ranges are merged
        first, then ``context_lines`` padding is applied to each surviving
        span — identity is decided on the definitions, so ``--context-lines``
        cannot change how many segments answer. Each segment carries
        ``symbol_rows``, the number of stored rows it merged, so the merge is
        auditable and never silent; a definition nested inside another of the
        same name is absorbed by the outer span. It is part of the answer,
        never a silent normalisation. Spans are returned in start-line order.
        """
        rel = file.lstrip("/")
        abs_path = os.path.normpath(os.path.join(entry.path, rel))
        if not abs_path.startswith(os.path.normpath(entry.path) + os.sep):
            raise ValueError(f"error: path outside registered project: {file}")
        if not os.path.isfile(abs_path):
            raise ValueError(f"error: file not found: {abs_path}")

        changed = self.file_changed_since_index(entry, rel)
        # (start, end, symbol_rows): symbol_rows is the number of stored rows
        # folded into the span, and None in line mode, which merges nothing.
        ranges: list[tuple[int, int, int | None]] = []
        re_resolved = False
        if symbol:
            # The rows' OWN ranges, before any --context-lines padding.
            own_ranges: list[tuple[int, int]] = []
            if changed or live:
                # Re-resolve against the live file; the manifest rows are
                # stale by definition here.
                with open(abs_path, encoding="utf-8", errors="replace") as f:
                    live_text = f.read()
                live_chunks = ts_chunker.chunk_text(rel, live_text)
                fresh_rows = [c for c in live_chunks if c.symbol == symbol]
                if fresh_rows:
                    own_ranges = [(c.start_line, c.end_line)
                                  for c in fresh_rows]
                    re_resolved = True
            if not own_ranges:
                core_manifest = self.manifest_for(entry.slug)
                try:
                    # Scoped in SQL: the name filter AND the file filter are
                    # the same read, uncapped. Filtering a capped exact-name
                    # read in Python dropped a file whose row sorted past the
                    # cap (issue #23: registry.py's __init__ is row 26 of 40).
                    rows = core_manifest.find_symbols(
                        symbol, substring=False, file=rel, limit=None)
                finally:
                    core_manifest.close()
                if not rows:
                    raise ValueError(
                        f"error: symbol {symbol!r} not indexed in {rel}")
                own_ranges = [(r.start_line, r.end_line) for r in rows]
            # Identity first, presentation second: the rows' own ranges are
            # merged into one span per run (a decorated definition is two
            # rows, issue #26), and only then is the --context-lines padding
            # applied to each surviving span. Padding before the merge would
            # fuse two genuinely disjoint definitions that sit a few lines
            # apart into one span that contains the code between them, making
            # the segment count depend on a display flag.
            ranges = [(max(1, s - context_lines), e + context_lines, n)
                      for s, e, n in _merge_symbol_ranges(own_ranges)]
        elif start_line is not None:
            end = end_line if end_line is not None else start_line
            ranges.append((max(1, start_line - context_lines),
                           end + context_lines, None))
        else:
            raise ValueError(
                "error: provide --start-line/--end-line or --symbol")

        with open(abs_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        segments = []
        for s, e, symbol_rows in ranges:
            segment: dict[str, Any] = {"start_line": s, "end_line": e}
            if symbol_rows is not None:
                # Symbol mode reports the merge so it is never silent; line
                # mode's shape is unchanged.
                segment["symbol_rows"] = symbol_rows
            if s > total:
                segment["error"] = (f"start_line {s} beyond end of "
                                    f"file ({total} lines)")
                segments.append(segment)
                continue
            s2, e2 = max(1, s), min(total, e)
            segment["lines"] = [f"{i:>5}| {lines[i - 1].rstrip()}"
                                for i in range(s2, e2 + 1)]
            segments.append(segment)
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
            return json.dumps(dict(data, schema=1), ensure_ascii=False,
                              indent=2)
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
