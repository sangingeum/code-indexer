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
from .errors import (ArgumentError, BackendError,
                     ExtensionUnavailableError, InternalError, LockedError,
                     NotFoundError, RebuildRequiredError,
                     VectorsDisabledError)
from .extensions import SEMANTIC_EXTENSION_MESSAGE
from .fingerprint import (ConfigError, EMBED_FORMAT, ROW_SIDE_KEYS,
                          configured_values, read_fingerprint,
                          recorded_row_version_gaps, row_side_mismatches,
                          vector_side_mismatches)
from .locks import project_lock
from .manifest import SCHEMA_VERSION, Manifest, ManifestFile, SymbolRow
from . import ordering
from . import ranking
from . import staleness
from . import ts_chunker
from . import vectors as vectors_policy
from .registry import ProjectEntry, Registry
from .scanner import (ScanBudget, ScanBudgetExceeded, ScannedFile,
                      read_scan_accounting, scan_project, skip_counts_phrase,
                      skip_reason_for)

if TYPE_CHECKING:  # annotations only: no client library is imported here
    from .embedder import Embedder
    from .indexer import Indexer
    from .store import Store

def _hit_identity(hit: dict[str, Any], width: int = 80) -> str:
    """What a hit matched, for every output format.

    The enclosing symbol when the chunk has one; otherwise the first non-blank
    line of the chunk's snippet; otherwise the hit's own file path, which is
    always known, so a hit never carries a constant in place of its name. The
    literal ``file`` survives only as the last resort for a hit dict that
    carries no path at all. Never an empty string and never a bare dash: text,
    compact and JSON name the same thing, so a scripted caller reads the
    answer an agent reads.
    """
    symbol = (hit.get("symbol") or "").strip()
    if symbol:
        return symbol
    for raw in (hit.get("snippet") or "").splitlines():
        line = raw.strip()
        if line:
            return line[:width]
    return (hit.get("file") or "").strip()[:width] or "file"


logger = logging.getLogger("code-indexer.core")

# Wall budget for one unscoped query invocation (ADR-0003 section 6): the
# fan-out stops at this wall and reports the remaining projects as skipped
# with reason=budget instead of running for minutes.
QUERY_BUDGET_DEFAULT_S = 30.0

# The accepted values of the two query-time enumerations. They live HERE, in
# the module that runs the search, because three readers need the same list:
# the CLI validates its flags against them, the eval harness forwards its own
# mode into Core.search, and the search itself refuses anything else. One
# source of truth means a value a caller accepts is a value this module
# understands, and a refusal can print the list it read.
SEARCH_MODES = ("dense", "lexical", "hybrid")
RANKING_MODES = ("vector", "metadata", "hybrid")

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


def validate_context_selector(symbol: str | None, start_line: int | None,
                             end_line: int | None) -> None:
    """Refuse a `get-code-context` request shape that names no single range.

    One function, raised from the read AND called by the CLI boundary before
    the first index read, so a wrong invocation is one verdict (ArgumentError,
    exit 2) whichever side answers it first. The messages are the taxonomy's:
    each names the two shapes the caller may pass instead.
    """
    if symbol is not None and (start_line is not None
                               or end_line is not None):
        raise ArgumentError(
            "--symbol and --start-line/--end-line are two ways to name "
            "one range; pass one selector")
    if end_line is not None and start_line is None:
        raise ArgumentError(
            "--end-line needs --start-line: a range with no start")
    if (start_line is not None and end_line is not None
            and end_line < start_line):
        raise ArgumentError(
            f"--end-line {end_line} is below --start-line {start_line}")
    if symbol is None and start_line is None:
        raise ArgumentError(
            "provide --start-line/--end-line or --symbol")


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

    def class_names(self, slug: str) -> ordering.ClassNames:
        """The project's ordering ladder: built-in names plus its own.

        Read once per slug from the project root's ``pyproject.toml``
        (``[tool.code-indexer.ordering]``) and handed to every manifest this
        Core opens, so a Python ordering key and the SQL ``file_class_weight``
        function of the same project read the SAME classifier. An unregistered
        slug or a malformed file yields the built-in ladder.
        """
        cache = getattr(self, "_class_names", None)
        if cache is None:
            cache = self._class_names = {}
        if slug not in cache:
            entry = self.registry.get_by_slug(slug)
            cache[slug] = ordering.load_class_names(
                entry.path if entry else None)
        return cache[slug]

    def manifest_for(self, slug: str, *, writable: bool = True) -> Manifest:
        """Open one project's manifest — writable unless a reader asks otherwise.

        ``writable=False`` is the READ-PATH open, and ``read_manifest`` is the
        shorthand every read surface uses. A read open sets no journal mode,
        runs no DDL and runs no migration, so a read can never empty a table;
        the recorded schema revision is then the caller's decision (see
        ``assert_rows_acceptable``): an older-revision manifest is refused,
        not upgraded. Migration belongs to a write open — the three ops that
        own a pass (``run_index``, ``reindex_with_swap``,
        ``sweep_orphans --apply``) and the CLI commands that record
        registration metadata.

        A read open of a project with no manifest yet answers from an empty
        in-memory manifest, so ``find-symbol`` on a never-indexed project
        still reports "no symbols" without creating a file as a side effect.
        """
        d = os.path.join(self.cfg.index_root, slug)
        path = os.path.join(d, "manifest.db")
        names = self.class_names(slug)
        if writable:
            os.makedirs(d, exist_ok=True)
            return Manifest(path, writable=True, class_names=names)
        if not os.path.isfile(path):
            return Manifest(":memory:", class_names=names)
        return Manifest(path, writable=False, class_names=names)

    def read_manifest(self, slug: str) -> Manifest:
        """Open one project's manifest READ-ONLY: the read-path contract.

        No journal-mode write, no DDL, no migration, no row write. Every read
        surface goes through this name, so "which open am I?" is answered by
        which method was called, not by a keyword buried at a call site.
        """
        return self.manifest_for(slug, writable=False)

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
        m = Manifest(mdir, writable=False)
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

    def vectors_status(self, entry: ProjectEntry) -> dict[str, Any]:
        """The ONE vector verdict a read can state without a service.

        ``vectors`` is the project's mode (on|off), ``vectors_state`` is the
        last recorded state of the collection relative to the rows
        (current|absent|stale|error) — and ``stale`` also means the recorded
        embedding identity is not the one this build would write, which a
        read can see from configuration alone. ``rows`` is the row-side
        verdict: current only when this build's extraction wrote them.

        ``identity_reason`` is the identity comparison ITSELF, named by key,
        and it is reported in BOTH modes: with vectors on it is why
        ``vectors_state`` reads ``stale``; with vectors off it is the inert
        recorded fact about the collection a future vectors-on pass would
        write. Keeping it in the same object as the mode and the marker is
        what stops a reader — and the sentence that explains this verdict —
        from taking the identity comparison for the whole vector verdict.
        """
        mdir = os.path.join(self.cfg.index_root, entry.slug, "manifest.db")
        if not os.path.isfile(mdir):
            return {"vectors": vectors_policy.MODE_OFF,
                    "vectors_state": vectors_policy.STATE_ABSENT,
                    "rows": "absent", "identity_reason": None}
        m = Manifest(mdir, writable=False)
        try:
            mode = vectors_policy.recorded_mode(m) or vectors_policy.MODE_ON
            state = vectors_policy.vectors_state(m)
            diffs = vector_side_mismatches(
                read_fingerprint(m), self.cfg.embed_model, EMBED_FORMAT)
            if mode == vectors_policy.MODE_ON and diffs:
                state = vectors_policy.STATE_STALE
            return {
                "vectors": mode,
                "vectors_state": state,
                "rows": vectors_policy.rows_state(m),
                "identity_reason": "; ".join(diffs) or None,
            }
        finally:
            m.close()

    # ------------------------------------------------------------------
    # indexing
    # ------------------------------------------------------------------

    def resolve_vectors(self, slug: str,
                        requested: str = vectors_policy.MODE_AUTO) -> str:
        """The mode a pass runs in: ``auto`` means the recorded mode.

        A project that has never recorded a mode resolves ON — the
        pre-extension behaviour, kept so an existing vectors-on project stays
        on across the upgrade. ``add-project`` records ``off`` when it
        registers a NEW project, so auto never turns embedding on by itself.
        """
        m = self.read_manifest(slug)
        try:
            return vectors_policy.resolve_mode(m, requested)
        finally:
            m.close()

    def _build_indexer(self, vectors: bool) -> Indexer:
        """The pass engine for this mode.

        A vectors-off pass constructs neither semantic client (ADR-0001
        section 1). A vectors-on pass reuses the shared engine so a test seam
        that replaced its embedder/store keeps working.
        """
        if vectors:
            return self.indexer
        from .indexer import Indexer as _Indexer
        return _Indexer(self.cfg, None, None)

    def _vector_identity_changed(self, manifest: Manifest) -> bool:
        """True when the recorded embedding identity is not this build's.

        Configuration-only: the recorded identity's model and embedded-text
        format are compared, and the dimension is the recorded one. A vectors
        rebuild is then a NEW collection under the new identity — never an
        append to one built by another model (ADR-0001 section 2).
        """
        recorded = read_fingerprint(manifest)
        if recorded is None:
            return False
        return bool(vector_side_mismatches(recorded, self.cfg.embed_model,
                                           EMBED_FORMAT))

    def _vector_target(self, slug: str, indexer: Indexer) -> str:
        """Where a vector-writing pass writes: the identity-stamped collection.

        ONE helper decides the physical name (``indexer.physical_collection``),
        so the embedding identity is always in it and a changed identity can
        only ever be a NEW collection. The caller swaps the result in under the
        project's alias once the pass succeeds; the previous collection is
        dropped by that swap, never appended to.
        """
        return indexer.physical_collection(slug)

    def run_index(self, slug: str, project_path: str, force: bool = False,
                  verbose: bool = False,
                  vectors: str = vectors_policy.MODE_AUTO,
                  rescan: bool = False) -> dict[str, Any]:
        """Run one indexing pass under the per-project lock.

        Returns {'state': 'idle', 'result': IndexResult-dict} on success,
        {'state': 'indexing', 'detail': 'indexing in progress'} when another
        process holds the lock, or {'state': 'error', 'error': ...}.

        ``vectors`` is the request (auto|on|off); the resolved mode decides
        whether a semantic client is constructed at all. When the rows are
        already current and the project was kept with vectors off, turning
        vectors on embeds straight from ``chunks`` — no scan, no reparse, no
        source read. ``rescan=True`` (index-more, which widens the scope)
        forces the row pass instead.
        """
        with project_lock(self._lock_path(slug)) as acquired:
            if not acquired:
                return {"state": "indexing", "detail": "indexing in progress"}
            self._set_state(slug, state="indexing", error=None)
            manifest = self.manifest_for(slug, writable=True)
            try:
                mode = vectors_policy.resolve_mode(manifest, vectors)
                use_vectors = mode == vectors_policy.MODE_ON
                identity_changed = (use_vectors
                                    and self._vector_identity_changed(manifest))
                embed_only = (use_vectors and not rescan
                              and vectors_policy.rows_current(manifest)
                              and (vectors_policy.recorded_mode(manifest)
                                   == vectors_policy.MODE_OFF
                                   or identity_changed))
                indexer = self._build_indexer(use_vectors)
                indexer.progress_cb = (
                    lambda info, s=slug: self._set_state(s, progress=info))
                if embed_only:
                    collection = self._vector_target(slug, indexer)
                    result = indexer.embed_index(
                        project_path, slug, manifest, collection=collection)
                    # Write the identity-stamped collection, then bind the
                    # alias to it — a no-op when it is already bound.
                    self.store.swap_collection(
                        collection, vectors_policy.alias_for(slug))
                else:
                    # A vector-writing pass names the identity-stamped
                    # collection and swaps the alias onto it, so a pass can
                    # never append to a collection another model built.
                    collection = (self._vector_target(slug, indexer)
                                  if use_vectors else None)
                    result = indexer.index_project(
                        project_path, slug, manifest, force_full=force,
                        vectors=use_vectors, collection=collection)
                    if collection is not None:
                        self.store.swap_collection(
                            collection, vectors_policy.alias_for(slug))
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
        """One watcher pass: an incremental index under the per-project flock.

        The same core op ``refresh`` calls (run_index with force=False); the
        difference is the caller, not the work. The watch loop runs this every
        tick and gets a held lock back as a return state, where an explicit
        ``--refresh`` raises LockedError. Cheap when nothing changed (stat and
        hash diff against the manifest; embeddings happen only for real
        changes) and cached by no clock: a tick after an edit does that edit's
        work.
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
        fast-path, and only files whose stat identity changed are hashed. A
        scanned path with no manifest row is opened for the same bounded
        prefix the pass reads, so a deliberately withheld file is reported as
        withheld rather than as an added reason; the project's
        ``allow_sensitive`` override decides whether anything is withheld at
        all. No service is contacted. The walk carries its own budget so a
        very large tree degrades to ``unknown`` rather than a long silence.
        """
        mdir = os.path.join(self.cfg.index_root, entry.slug, "manifest.db")
        # The manifest's own fields belong to an ``unknown`` verdict too: an
        # answer reports the pass it describes even when the check cannot run.
        last_indexed: str | None = None
        index_revision: str | None = None
        if os.path.isfile(mdir):
            m = Manifest(mdir, writable=False)
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
        m = Manifest(mdir, writable=False)
        try:
            indexed = m.all_files()
            scope = [ln for ln in (m.get_meta("scope_include") or "")
                     .splitlines() if ln.strip()]
            # Read with the scope, from the same open: the override changes
            # what the next pass withholds, so the diff must honour it too.
            allow_sensitive = m.get_meta("allow_sensitive") == "1"
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
                                index_revision=index_revision,
                                allow_sensitive=allow_sensitive)

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
                 f"rows={fields['rows']}", f"vectors={fields['vectors']}",
                 f"vectors_state={fields['vectors_state']}",
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
            m = Manifest(mdir, writable=False)
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
        vs = self.vectors_status(entry)
        return {
            "path": entry.path,
            "slug": entry.slug,
            "state": st,
            "files": file_count,
            "chunks": chunk_count,
            "last_indexed": last_indexed or "never",
            "reason": reason,
            "note": note,
            "rows": vs["rows"],
            "vectors": vs["vectors"],
            "vectors_state": vs["vectors_state"],
        }

    # ------------------------------------------------------------------
    # index fingerprint (v4): mismatch protection
    # ------------------------------------------------------------------

    def configured_fingerprint_values(self) -> dict[str, str]:
        """Fingerprint keys this process can state without a service."""
        return configured_values(self.cfg.embed_model)

    def fingerprint_status(self, entry: ProjectEntry) -> dict[str, Any]:
        """The ROW verdict plus the VECTOR verdict, compared per side.

        Two sides, two questions (ADR-0001 section 5):

        * ``ok``/``state``/``reason`` describe the STORED ROWS: a difference
          on the row axes (chunker, extraction) means this build would not
          produce these rows, so row surfaces refuse. A missing or
          mismatched embedding identity says nothing about them.
        * ``vectors`` is ``current`` | ``stale`` | ``absent``, compared on the
          embedding identity's model and embedded-text format. The recorded
          dimension has no configured counterpart without a probe, so it is
          taken from the recorded identity — a read never dials.

        ``unverified`` keeps its meaning for the row side: every comparable
        row key matches, and the dimension question is not one a read answers.
        A manifest with no fingerprint at all counts as ok: the next pass
        backfills it without forcing a reindex.

        A manifest that recorded no COMPLETE fingerprint but did record a row
        key that disagrees is not that case: a vectors-off pass records only
        the row keys, so a chunker or extraction-version difference still
        means the stored rows are obsolete and the row surfaces must refuse
        rather than answer from them.
        """
        recorded = None
        recorded_row_keys: dict[str, str | None] = {}
        mdir = os.path.join(self.cfg.index_root, entry.slug, "manifest.db")
        if os.path.isfile(mdir):
            m = Manifest(mdir, writable=False)
            try:
                recorded = read_fingerprint(m)
                recorded_row_keys = {key: m.get_meta(key)
                                     for key in ROW_SIDE_KEYS}
            finally:
                m.close()
        if recorded is None:
            gaps = recorded_row_version_gaps(recorded_row_keys)
            if gaps:
                return {"ok": False, "state": NEEDS_REINDEX,
                        "reason": "; ".join(gaps), "recorded": None,
                        "embedding": None, "vectors": "absent",
                        "vectors_reason": None}
            return {"ok": True, "state": "ok", "reason": None,
                    "recorded": None, "embedding": None,
                    "vectors": "absent", "vectors_reason": None}
        configured = self.configured_fingerprint_values()
        row_diffs = row_side_mismatches(recorded, configured)
        vector_diffs = vector_side_mismatches(
            recorded, self.cfg.embed_model, EMBED_FORMAT)
        status: dict[str, Any] = {
            "recorded": recorded.describe(),
            "embedding": recorded.embedding_version(),
            "vectors": "stale" if vector_diffs else "current",
            "vectors_reason": "; ".join(vector_diffs) or None,
        }
        if row_diffs:
            status.update(ok=False, state=NEEDS_REINDEX,
                          reason="; ".join(row_diffs))
        else:
            status.update(ok=True, state=UNVERIFIED, reason=UNVERIFIED_REASON)
        return status

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
        if status["vectors"] == "stale":
            raise ConfigError(
                f"index for {entry.path} was built with a different embedding "
                f"identity ({status['vectors_reason']}); its vectors are not "
                f"comparable with this configuration; run: code-indexer "
                f"reindex-project {entry.path}")

    def assert_vectors_available(self, entry: ProjectEntry) -> None:
        """Refuse a vector-dependent query on a vectors-off project.

        A project kept with vectors off has rows but no collection, so there
        is nothing for a dense query to rank. The failure is explicit and
        names the remedy: a vector rebuild (``--vectors on``). It never
        degrades to lexical (a different answer under the same name) and
        never returns an empty success (a failure wearing an answer's clothes).
        """
        if self.resolve_vectors(entry.slug) != vectors_policy.MODE_OFF:
            return
        raise VectorsDisabledError(
            f"index for {entry.path} was built with vectors=off; semantic "
            f"search needs vectors; run: code-indexer reindex-project "
            f"{entry.path} --vectors on")

    def recorded_schema_version(self, slug: str) -> int | None:
        """The manifest's RECORDED schema revision, read-only (None if absent).

        Read paths compare this against ``SCHEMA_VERSION`` instead of letting
        an open migrate the file: a revision this build does not read is a
        refusal with a named remedy, never an implicit upgrade (ADR-0003's
        "a read command never writes the manifest").
        """
        mdir = os.path.join(self.cfg.index_root, slug, "manifest.db")
        if not os.path.isfile(mdir):
            return None
        m = Manifest(mdir, writable=False)
        try:
            return m.schema_version
        finally:
            m.close()

    def assert_rows_acceptable(self, entry: ProjectEntry) -> None:
        """Refuse a row-reading surface when this build cannot read the rows.

        Two refusals, one shape: the manifest's RECORDED schema revision is
        older than this build's (a read does not migrate it — that is a pass
        command's job), or the stored rows were produced by a configuration
        this build would not reproduce (the fingerprint gate). Either way the
        caller gets one ``RebuildRequiredError`` line naming the remedy, empty
        stdout, exit 4.

        Round 1's review found that only the search path gated
        (``assert_fingerprint_ok`` above), so ``find-symbol``/``outline``
        answered "no symbols" with exit 0 from an index whose stored rows this
        build would not produce. The extraction-version bump makes every
        project needs-reindex the moment the new extraction lands, so answering
        from those rows would become a universal defect rather than a local one.

        Every surface that reads stored symbol/reference/import rows therefore
        refuses: one ``RebuildRequiredError`` line naming the remedy, empty
        stdout, exit 4. Surfaces that answer without rows (``list-projects``,
        ``lookup-project``, ``index-status``, ``doctor``) never call this.
        """
        recorded_schema = self.recorded_schema_version(entry.slug)
        if (recorded_schema is not None
                and recorded_schema < int(SCHEMA_VERSION)):
            raise RebuildRequiredError(
                f"index for {entry.path} was written at manifest schema "
                f"{recorded_schema} and this build reads schema "
                f"{SCHEMA_VERSION}; its rows are in an older layout, so this "
                f"answer would describe an index this build would not "
                f"produce; run: code-indexer reindex-project {entry.path}")
        status = self.fingerprint_status(entry)
        if status["ok"]:
            return
        raise RebuildRequiredError(
            f"index for {entry.path} does not match the current "
            f"configuration ({status['reason']}); the stored rows are "
            f"obsolete, so this answer would describe an index this build "
            f"would not produce; run: code-indexer reindex-project "
            f"{entry.path}")

    def gate_entries(self, entries: list[ProjectEntry]) -> None:
        """Run both query gates over every entry a search will visit.

        Called BEFORE the query embedding is computed, so the refusal a caller
        sees on a machine with no embedding service is the documented one —
        ``ConfigError`` naming ``--vectors on`` (exit 4) for a vectors-off
        project, ``ConfigError`` naming ``reindex-project`` for a wrong-model
        or obsolete index — and not a ``BackendError`` (exit 3) raised by the
        embedder first. Neither gate dials a service: both answer from the
        manifest and this process's configuration.
        """
        for entry in entries:
            self.assert_vectors_available(entry)
            self.assert_fingerprint_ok(entry)

    def reindex_with_swap(self, slug: str, project_path: str,
                          vectors: str = vectors_policy.MODE_AUTO
                          ) -> dict[str, Any]:
        """Full rebuild of one project, by side.

        The two sides are rebuilt independently and neither forces the other:
        a vectors-only rebuild embeds from the stored chunks and swaps a
        collection named after the new identity; a row rebuild parses source
        and leaves the collection alone when vectors are off. A crash before
        the swap leaves the old collection serving reads.
        """
        with project_lock(self._lock_path(slug)) as acquired:
            if not acquired:
                return {"state": "indexing", "detail": "indexing in progress"}
            self._set_state(slug, state="indexing", error=None)
            manifest = self.manifest_for(slug, writable=True)
            try:
                mode = vectors_policy.resolve_mode(manifest, vectors)
                use_vectors = mode == vectors_policy.MODE_ON
                indexer = self._build_indexer(use_vectors)
                indexer.progress_cb = (
                    lambda info, s=slug: self._set_state(s, progress=info))
                if use_vectors and vectors_policy.rows_current(manifest):
                    # Rows survive a vector rebuild: no parse, no source read.
                    # The physical collection carries the identity; the swap
                    # binds the alias to it (a no-op when already bound).
                    collection = self._vector_target(slug, indexer)
                    result = indexer.embed_index(
                        project_path, slug, manifest, collection=collection)
                    self.store.swap_collection(
                        collection, vectors_policy.alias_for(slug))
                elif use_vectors:
                    # Rows are obsolete too: parse and embed into a fresh
                    # collection, then swap it in.
                    tmp = indexer.physical_collection(slug)
                    if self.store.collection_exists(tmp):
                        self.store.drop_collection(tmp)
                    result = indexer.index_project(
                        project_path, slug, manifest, force_full=True,
                        collection=tmp, vectors=True)
                    self.store.swap_collection(
                        tmp, vectors_policy.alias_for(slug))
                else:
                    result = indexer.index_project(
                        project_path, slug, manifest, force_full=True,
                        vectors=False)
                self._last_scan[slug] = time.monotonic()
                self._set_state(slug, state="idle", last_result=vars(result))
                return {"state": "idle", "result": vars(result)}
            except Exception as exc:  # noqa: BLE001
                logger.exception("swap rebuild failed for %s", project_path)
                self._set_state(slug, state="error", error=str(exc))
                return {"state": "error", "error": str(exc)}
            finally:
                manifest.close()

    def recorded_embedding_dimension(self, manifest: Manifest, slug: str,
                                     project_path: str) -> int:
        """The dimension the stored vectors already have, read never probed.

        A repair re-addresses vectors that are already in the collection, so
        it needs the dimension only to NAME that collection and to record the
        identity it belongs to -- it never embeds, and it must work with the
        embedding service down. Two recorded sources answer, in order:

        * ``meta.embed_dim``, written by every vector-writing pass;
        * the length of a vector the collection already holds, sampled from
          the collection the project's alias resolves to, for a manifest that
          recorded no dimension.

        When neither exists there is nothing to fake from: this raises
        ``RebuildRequiredError`` (exit 4) naming the remedy rather than
        dialling the service to guess a dimension the index never recorded.
        """
        raw = manifest.get_meta("embed_dim")
        try:
            recorded = int(raw) if raw not in (None, "") else 0
        except (TypeError, ValueError):
            recorded = 0
        if recorded > 0:
            return recorded
        physical = getattr(self.store, "physical_name", lambda _n: None)(
            vectors_policy.alias_for(slug))
        if physical is not None:
            for file in sorted(manifest.all_files()):
                for point in self.store.points_for_file(
                        physical, project_path, file):
                    vector = point.get("vector")
                    if vector:
                        return len(vector)
        raise RebuildRequiredError(
            f"error: {slug}: the index records no embedding dimension and the "
            f"collection holds no vector to read one from, so no stored "
            f"vector can be re-addressed; run: code-indexer reindex-project "
            f"{project_path}")

    def chunk_row_count(self, slug: str) -> int:
        """How many chunk rows the index holds -- what a repair re-addresses.

        A question for an index that records no vectors: there is no point to
        re-address, but the rows it would have carried are still a fact worth
        reporting, so a repair loop can say how much it left alone.
        """
        m = self.read_manifest(slug)
        try:
            return m.count_chunks()
        finally:
            m.close()

    def repair_points(self, slug: str, project_path: str) -> dict[str, Any]:
        """Re-address every stored row's vector at its current point id.

        The repair for a collection written before a file's chunk sequence
        shifted: each row's vector is found by CONTENT among that file's
        points (the payload's ``chunk_hash`` and ``symbol`` name the exact
        embedding input) and written at the positional id the row's current
        ``chunk_index`` names, with the row's current payload. Nothing is
        embedded. A row whose vector is found nowhere is counted in
        ``chunks_unrepairable`` and left untouched -- the command refuses to
        invent a vector it does not have, and the caller names the count.

        Idempotent: a second run finds every point already agreeing with its
        row and writes nothing -- and, writing nothing, does not advance
        ``index_revision`` or ``last_indexed`` either, so a repair that
        changed nothing does not read as a change to a later audit. Returns
        the same shape as ``run_index``.

        No embedding service is needed: the dimension comes from what the
        index already records (see ``recorded_embedding_dimension``), so the
        repair runs with the service down.

        A refusal that the index must be rebuilt raises
        ``RebuildRequiredError`` (exit 4) rather than being folded into the
        generic error state, which would surface as ``InternalError``.
        """
        with project_lock(self._lock_path(slug)) as acquired:
            if not acquired:
                return {"state": "indexing", "detail": "indexing in progress"}
            self._set_state(slug, state="indexing", error=None)
            manifest = self.manifest_for(slug, writable=True)
            try:
                if not vectors_policy.rows_current(manifest):
                    raise RebuildRequiredError(
                        f"error: {slug}: the stored rows are not current, so "
                        f"there is no stable numbering to re-address; run: "
                        f"code-indexer reindex-project {project_path}")
                indexer = self._build_indexer(vectors=True)
                # The dimension the stored vectors have, never a probe: the
                # repair carries vectors that are already in the collection.
                dim = self.recorded_embedding_dimension(
                    manifest, slug, project_path)
                collection = indexer.physical_collection(slug, dim)
                if not self.store.collection_exists(collection):
                    raise RebuildRequiredError(
                        f"error: {slug}: the vector collection is gone, so no "
                        f"stored vector can be re-addressed; run: "
                        f"code-indexer reindex-project {project_path}")
                result = indexer.embed_index(
                    project_path, slug, manifest, collection=collection,
                    repair=True, dim=dim)
                self._set_state(slug, state="idle", last_result=vars(result))
                return {"state": "idle", "result": vars(result)}
            except RebuildRequiredError as exc:
                # Exit 4 is the caller's next move (rebuild), not a generic
                # runtime failure: record the state, then let the taxonomy
                # class reach the caller unchanged.
                self._set_state(slug, state="error", error=str(exc))
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("point repair failed for %s", project_path)
                self._set_state(slug, state="error", error=str(exc))
                return {"state": "error", "error": str(exc)}
            finally:
                manifest.close()

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
        # A dry-run sweep is a read and opens read-only; only --apply writes.
        m = self.manifest_for(entry.slug, writable=apply)
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
        # Defence only: the CLI refuses a bad ranking value at its own
        # boundary as a usage error (exit 2), so no caller reaches this line
        # with one. Kept because this method is also the seam the eval
        # harness and the MCP relay call directly.
        if ranking_mode not in RANKING_MODES:
            raise ValueError(f"error: unknown ranking mode: {ranking_mode}")
        collection = vectors_policy.alias_for(entry.slug)
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
        # ONE source of truth for what a hit describes: the manifest row its
        # point names. Text, range and symbol all come from that row, so an
        # answer can never show one chunk's text and name another chunk's
        # region. The payload supplies only the point's identity (file,
        # chunk_index) and the provenance a row does not carry (file_hash,
        # indexed_at). A missing row (a stale point for a deleted chunk) falls
        # back to the payload, which is all that is left to describe it.
        m = self.read_manifest(entry.slug)
        try:
            for h in hits:
                p = h.payload or {}
                row = (m.chunk_row(p["file"], p["chunk_index"])
                       if p.get("file") is not None
                       and p.get("chunk_index") is not None else None)
                snippet = row.text if row else m.chunk_text(
                    p.get("file"), p.get("chunk_index"))
                out.append({
                    "project": entry.path,
                    "file": p.get("file"),
                    "score": round(h.score, 4),
                    "symbol": row.symbol if row else p.get("symbol"),
                    "symbol_type": (row.symbol_type if row
                                    else p.get("symbol_type")),
                    "lang": p.get("lang"),
                    "start_line": row.start_line if row else p.get("start_line"),
                    "end_line": row.end_line if row else p.get("end_line"),
                    "snippet": (snippet or "")[:500],
                    # Staleness provenance for get_code_context.
                    "file_hash": p.get("file_hash"),
                    "indexed_at": p.get("indexed_at"),
                })
        finally:
            m.close()
        qtokens = ranking.query_tokens(query)
        if ranking_mode == "metadata":
            out = ranking.metadata_rerank(out, qtokens)
        elif ranking_mode == "hybrid":
            out = ranking.hybrid_fuse(out, qtokens)
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
        if mode not in SEARCH_MODES:
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
        # The gates run over the resolved entries FIRST, then the ONE query
        # embedding: a vectors-off or obsolete index refuses with its own
        # error (ConfigError naming ``--vectors on``, RebuildRequiredError
        # naming reindex-project) instead of whatever the embedder raises
        # first. Both are exit 4; the embedder's failure is exit 3 — the
        # wrong answer on the machine this refusal exists for.
        vector: list[float] | None = None
        if not lexical_only:
            self.gate_entries(entries)
            vector = self._query_vector(query)
        budget = QUERY_BUDGET_DEFAULT_S if budget_s is None else budget_s
        started = time.monotonic()
        for index, entry in enumerate(entries):
            if time.monotonic() - started > budget:
                skipped.extend(
                    {"project": e.path, "reason": "budget"}
                    for e in entries[index:])
                break
            # The gates already ran over EVERY entry above, before the
            # embedding: they are FATAL BY DESIGN and deliberately outside the
            # per-project try/except below. A wrong-model index answers wrong
            # SILENTLY, which is worse than a loud failure; a needs-reindex or
            # vectors-off project aborts the whole run and is never folded into
            # the skipped-with-a-warning path. Do not "fix" this into silence.
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
            all_hits = ranking.score_order(all_hits)
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

        # Result shaping: merge overlapping/adjacent same-file hits, one class
        # order for every retrieval and ranking mode, generic per-file cap,
        # budget trim (lowest-ranked dropped first). The class order runs after
        # the pure-data share cap above and before the generic caps, so it
        # decides which hits fill them; it is an order, never a filter
        # (ranking.class_order).
        if merge:
            all_hits = ranking.merge_overlapping(all_hits)
        # The ladder is each hit's own project table, never the built-in
        # names: a name a project adds under [tool.code-indexer.ordering]
        # must carry the same rung here that it carries on the structural
        # surfaces (`self.class_names`). One project resolves to one value; a
        # cross-project window resolves per hit, because --all-projects can
        # mix trees with different tables.
        names_by_project = {e.path: self.class_names(e.slug) for e in entries}

        def names_for(project_path: str) -> ordering.ClassNames:
            return names_by_project.get(project_path,
                                        ordering.DEFAULT_CLASS_NAMES)

        all_hits = ranking.class_order(
            all_hits, ranking.query_tokens(query), names_for)
        # Siblings of one code region (a declaration and a line window over the
        # same lines, a container and its methods) are separate chunks, so two
        # slots could show one region twice while another region never appears.
        # The repeat-coverage order keeps the best-ranked hit of each span and
        # moves later overlapping siblings behind their rung's distinct spans.
        all_hits = ranking.demote_repeated_spans(
            all_hits, ranking.query_tokens(query), names_for)
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
        m = self.read_manifest(entry.slug)
        try:
            fts_rows = m.fts_search(query, limit * 3)
            # The hit's language is the one the file's ROW stores -- the
            # grammar that actually parsed it -- not the one its extension
            # suggests: a `.h` header the index read as C is stored `c`, and
            # the name-only map calls it cpp.
            file_rows = m.all_files()
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
                    "lang": ((file_rows.get(file).language
                              if file_rows.get(file) else None)
                             or _lang_from_path(file)),
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
        find_symbol and pins exact matches first, labelled match=symbol. The
        pin orders inside its own rung only: the class order in `search`
        re-sorts this list afterwards, so an exact match defined in a test or
        docs file can still sit below a source hit.
        """
        lexical = self._lexical_hits(entry, query, limit)
        fused = ranking.rrf_fuse({"dense": dense_hits, "lexical": lexical},
                                 limit * 3)
        fused = [dict(h, match=h.get("match") or "fused") for h in fused]
        ident = query.strip()
        if re.fullmatch(r"[A-Za-z_][\w:.]*", ident):
            m = self.read_manifest(entry.slug)
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
                    "lang": r.language or _lang_from_path(r.file),
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
                staleness_block: dict[str, Any] | None = None,
                empty_answer: tuple[str, str] | None = None) -> str:
        """Format search hits (stable field contract).

        ``text`` — the original verbose two-line-per-hit render (default;
        kept for compatibility with existing agent workflows).
        ``compact`` — one line per hit: ``path:start-end  symbol  score``;
        the token-budget agent loop (search compact -> get-code-context).
        ``json`` — the JSON contract, which carries ``skipped`` /
        ``skipped_count`` / ``partial`` and the additive staleness block, so
        a consumer can tell the result is partial. Each JSON hit also carries
        ``identity``: exactly the name the text modes print (the symbol, else
        the first non-blank snippet line, else the hit's file path).
        ``dropped`` > 0 appends one trailing truncation line in text/compact
        mode, and a non-empty ``skipped`` appends one trailing partial-result
        line (the documented exceptions to single-line stderr reporting).

        ``empty_answer`` is the ``(empty_reason, sentence)`` the CLI derived
        from the index's own coverage; with no hits it replaces the bare
        ``no results`` (text) and rides the JSON payload beside the staleness
        block, so an empty search says WHY it is empty exactly as the name
        reads do. It is never attached to an answer that has hits.
        """
        if fmt == "json":
            payload: dict[str, Any] = {
                "schema": 1,
                "hits": [dict(h, identity=_hit_identity(h)) for h in hits],
                "truncated": dropped > 0, "dropped": dropped,
                "skipped": skipped or [],
                "skipped_count": len(skipped or []),
                "partial": partial or bool(skipped),
            }
            if not hits and empty_answer is not None:
                payload["empty_reason"] = empty_answer[0]
                payload["empty_reason_sentence"] = empty_answer[1]
            payload.update(staleness_block or {})
            return json.dumps(payload, ensure_ascii=False, indent=2)
        if not hits:
            return empty_answer[1] if empty_answer is not None else "no results"
        lines: list[str] = []
        if fmt == "compact":
            for h in hits:
                sym = _hit_identity(h)
                lines.append(
                    f"{h['project']}::{h['file']}:{h['start_line']}-"
                    f"{h['end_line']}  {sym}  {h['score']:.4f}")
        else:
            # The list is class-ordered (ranking.class_order), score
            # descending only inside a rung: the header names that order.
            lines.append("search results (file class, then score desc):")
            for h in hits:
                if h.get("symbol"):
                    sym = f" ({h['symbol']}"
                    if h.get("symbol_type"):
                        sym += f", {h['symbol_type']}"
                    sym += ")"
                else:
                    # No enclosing symbol: name the code line that matched.
                    sym = f" ({_hit_identity(h)})"
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
                           staleness_block: dict[str, Any] | None = None,
                           empty_answer: tuple[str, str] | None = None) -> str:
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
            staleness_block=staleness_block, empty_answer=empty_answer)

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
    # The same names in the same order, derived from the mapping above: the
    # CLI's accepted list for --relationship and this refusal message both
    # read it, so neither can drift from the words the filter understands.
    REFERENCE_FILTERS: tuple[str, ...] = tuple(_REFERENCE_FILTERS)

    # The relationships that ANSWER "what references this": a reference row.
    # `includes` is deliberately absent -- an include directive names a file,
    # it does not use the symbol -- so an unfiltered answer reports importers
    # in their own section instead of counting them as references.
    _REFERENCE_RELATIONSHIPS: tuple[str, ...] = (
        "call", "type", "base_class", "member", "identifier")
    _IMPORTER_RELATIONSHIPS: tuple[str, ...] = ("includes",)

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
        (calls|includes|inherits|references); ``None`` returns every
        REFERENCE row -- ``includes`` is not one, because an include directive
        names a file rather than using the symbol, so the unfiltered answer
        leaves importers to :meth:`find_importers` and its own section.
        Rows come back in the ordering policy's order -- file class first, then
        a reference inside a named enclosing symbol before a module-level
        mention, then attributed syntax, then (file, line, relationship,
        from_symbol, receiver) -- capped at ``limit``. ``find-references``
        reads a capped store query, so the order is applied before the cap and
        the answer is its prefix.
        """
        if (relationship is not None
                and relationship not in self.REFERENCE_FILTERS):
            raise ValueError(
                f"error: unknown relationship: {relationship} "
                f"(expected {'|'.join(self.REFERENCE_FILTERS)})")
        stored = (self._REFERENCE_FILTERS[relationship]
                  if relationship is not None
                  else self._REFERENCE_RELATIONSHIPS)
        m = self.read_manifest(entry.slug)
        try:
            rows = m.refs_to(name, relationships=stored, limit=limit)
        finally:
            m.close()
        return [{"file": r["file"], "line": r["line"],
                 "from_symbol": r["from_symbol"], "target": r["to_name"],
                 "relationship": r["relationship"],
                 "confidence": r["confidence"],
                 "receiver": r["receiver"]} for r in rows]

    def find_importers(self, entry: ProjectEntry, name: str,
                       limit: int = 25) -> list[dict[str, Any]]:
        """Files whose import/include directive targets a file defining
        ``name`` -- the IMPORTERS, not users.

        An ``#include``/``import`` directive names a FILE, not a symbol, so
        the connection to a symbol is made through the defining file: every
        resolved import whose target defines ``name``. Same row shape and
        order as :meth:`find_references`.
        """
        m = self.read_manifest(entry.slug)
        try:
            defining = {row.file for row in m.definitions_of(name)}
            rows = [imp for imp in m.import_rows()
                    if imp["resolved_file"] in defining]
        finally:
            m.close()
        out = [{"file": r["file"], "line": r["line"],
                "from_symbol": None,
                "target": r["raw_target"],
                "relationship": f"import:{r['kind']}",
                "confidence": "ast", "receiver": None}
               for r in rows]
        return out[:limit] if limit is not None else out

    def reference_relationship_counts(self, entry: ProjectEntry, name: str
                                      ) -> list[tuple[str, int]]:
        """(stored relationship, count) for every reference to ``name``,
        unfiltered. One aggregate query — the CLI uses it to say WHY a
        filtered report is empty instead of implying the name is absent."""
        m = self.read_manifest(entry.slug)
        try:
            return m.ref_relationship_counts(name)
        finally:
            m.close()

    def all_symbol_names(self, entry: ProjectEntry) -> set[str]:
        """Names with a definition row (for exact-vs-heuristic labelling)."""
        m = self.read_manifest(entry.slug)
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
        of ``meta.last_indexed``. A manifest older than this build's revision
        is refused before this point (``assert_rows_acceptable``), never
        migrated by a read.
        """
        m = self.read_manifest(entry.slug)
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
            return self._tree_projection(entry, files, by_file)
        out_files: list[dict[str, Any]] = []
        for path in sorted(files):
            f = files[path]
            rows = by_file.get(path, [])
            if limit is not None:
                rows = rows[:limit]
            out_files.append({
                "file": path,
                "lang": f.language or _lang_from_path(path),
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
            files: dict[str, ManifestFile],
            by_file: dict[str, list[SymbolRow]]) -> dict[str, Any]:
        """`--tree` mode (design §2.1): directory tree with per-dir symbol
        count and dominant language. No symbols listed; --limit ignored.

        The language counted is the one each file's ROW stores, so a `.h`
        header the index read as C counts `c` -- the name-only map would make
        a C project whose headers are all `.h` report cpp as dominant.
        """
        dirs: dict[str, dict[str, Any]] = {}
        for path in sorted(files):
            parent = os.path.dirname(path).replace(os.sep, "/") or "."
            d = dirs.setdefault(parent, {"symbols": 0, "langs": {}})
            d["symbols"] += len(by_file.get(path, []))
            lang = files[path].language or _lang_from_path(path)
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
        m = self.read_manifest(entry.slug)
        try:
            rows = m.symbols_for_file(rel)
        finally:
            m.close()
        if not rows and not self.registry_has_file(entry, rel):
            # NotFoundError, not InternalError: the request named a file the
            # index does not hold, which is the Exit 1 table's own definition
            # of this class ("a path ... or an indexed definition that is not
            # there"). The exit code is 1 either way; the type is what tells a
            # caller the file is absent rather than the tool broken.
            reason = self._skip_reason(entry, rel)
            if reason:
                # A file the scan deliberately left out says so; "not
                # indexed" alone reads like a bug or an empty index.
                raise NotFoundError(f"file not indexed: {file} "
                                    f"(the scan skipped it: {reason})")
            counts = self._skip_counts(entry, rel)
            if counts:
                # Past the named examples the path is still counted: name the
                # classes rather than leave a 51st file answered with silence.
                raise NotFoundError(f"file not indexed: {file} "
                                    f"({counts})")
            raise NotFoundError(f"file not indexed: {file}")
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
        m = self.read_manifest(entry.slug)
        try:
            return m.get_file(rel) is not None
        finally:
            m.close()

    def _skip_reason(self, entry: ProjectEntry, rel: str) -> str | None:
        """Why the last pass left this path out, when it recorded a reason."""
        m = self.read_manifest(entry.slug)
        try:
            return skip_reason_for(rel, read_scan_accounting(m))
        finally:
            m.close()

    def _skip_counts(self, entry: ProjectEntry, rel: str) -> str | None:
        """The counted skip classes, for a path past the named examples."""
        m = self.read_manifest(entry.slug)
        try:
            return skip_counts_phrase(read_scan_accounting(m))
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
        m = self.read_manifest(entry.slug)
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
        # Request shape first, before any path or index state is consulted.
        # The CLI calls the SAME function at its own boundary before the
        # first read, so a wrong invocation answers ArgumentError (exit 2)
        # whoever answers first -- never the read's NotFoundError for a
        # project that merely happens not to be registered.
        validate_context_selector(symbol, start_line, end_line)

        rel = file.lstrip("/")
        abs_path = os.path.normpath(os.path.join(entry.path, rel))
        if not abs_path.startswith(os.path.normpath(entry.path) + os.sep):
            raise ArgumentError(f"path outside registered project: {file}")
        if not os.path.isfile(abs_path):
            raise NotFoundError(f"file not found: {abs_path}")

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
                core_manifest = self.read_manifest(entry.slug)
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
            # A real raise, not an assert: `python -O` strips an assert, and
            # the guard is the only thing standing between a selector-less
            # call and an empty `ranges` rendered as an empty answer. The CLI
            # refuses this shape before the core is reached (exit 2), so this
            # line is defence -- but defence that survives optimisation.
            raise InternalError("unreachable: selectors were validated")

        with open(abs_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        if start_line is not None and start_line > total:
            # LINE mode: the caller named lines the file does not have, so
            # the range is wrong and says so as a usage error. Symbol mode
            # keeps its per-segment ``error`` field, because the range there
            # came from an index row rather than from the request.
            raise ArgumentError(
                f"--start-line {start_line} is past the end of {rel} "
                f"({total} lines)")
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


# ---------------------------------------------------------------------------
# Token-reduction ops (subcommand design §2.1/§2.2): manifest-only, no parsing
# at query time. Each op has a format_* twin following format_hits' pattern.
# ---------------------------------------------------------------------------

def _lang_from_path(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "py": "python", "js": "javascript", "ts": "typescript",
        "rs": "rust", "go": "go", "java": "java", "c": "c", "cpp": "cpp",
        "cc": "cpp", "h": "cpp", "hpp": "cpp", "cs": "csharp", "rb": "ruby",
        "php": "php", "sh": "bash", "md": "markdown", "json": "json",
        "yaml": "yaml", "yml": "yaml", "toml": "toml",
    }.get(ext, ext or "text")
