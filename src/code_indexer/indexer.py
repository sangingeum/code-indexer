"""Indexer: scan -> hash diff -> chunk -> embed changed -> upsert/purge.

Implements design §6. The content-hash diff (never mtime) is what makes
incremental indexing correct across branch switches — do not "optimize"
back to mtime (§7/F).

Reference rows have ONE writer (``graph_extract``) and land in the single
``refs`` manifest table at the commit phase, in the same transaction family
as the symbol rows — never ahead of the vectors.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .chunker import Chunk
from .config import Config
from .embed_text import EMBED_FORMAT, embed_text
from .fingerprint import (configured_values, current_fingerprint,
                          manifest_newer_than, read_fingerprint,
                          row_side_mismatches, write_fingerprint)
from . import ts_chunker
from .manifest import ChunkRow, Manifest, ManifestFile, SymbolRow
from .scanner import (SKIP_SENSITIVE, SKIP_UNREADABLE, ScanAccounting,
                      ScannedFile, scan_project, write_scan_accounting)
from .store import point_id
from . import graph_extract
from . import langmatrix
from .sensitive import (SENSITIVE_READ_CHARS, SensitiveReport, skip_verdict)

if TYPE_CHECKING:  # annotations only: importing this module costs no client
    from .embedder import Embedder
    from .store import Store


def _chunk_dispatch(path: str, text: str) -> list[Chunk]:
    """Chunker seam (design §5): tree-sitter AST when supported, else fallback."""
    return ts_chunker.chunk_text(path, text)


def reference_rows(path: str, text: str, lang: str
                   ) -> tuple[list, list, str]:
    """The reference writer's decision for one file: rows, and confidence.

    The outcome is ``none``, ``ast`` or ``heuristic`` and it comes from
    ``langmatrix.reference_reader`` -- the same function the published
    language matrix reads its `references` column from. One rule, two
    consumers: they were encoded separately once, and the matrix then said
    "none" for languages whose files stored heuristic rows.
    """
    reader = langmatrix.reference_reader(lang)
    if reader == "none":
        # A sentence naming a name is not a reference to it, and the file keeps
        # its chunks for search regardless.
        return [], [], "none"
    if reader == "ast":
        imports, refs = graph_extract.extract_graph(path, text, lang)
        return imports, refs, "ast"
    imports, refs = graph_extract.extract_graph_textual(path, text)
    return imports, refs, "heuristic"


def _parse_dispatch(path: str, text: str) -> tuple[list[Chunk], str, str | None]:
    """Chunker seam + the file's parse verdict + the grammar that parsed it.

    The verdict is the parser's own (``parsed``/``parse_error``) or
    ``parse_unavailable`` when no AST exists at all -- no grammar pack, no
    grammar for the extension, or a parser exception; it is never inferred
    from the chunks themselves (ADR-0004 section 7). The language is the
    grammar the parser chose for THIS file, which for an ambiguous `.h`
    header is a decision the bytes made, not the name.
    """
    return ts_chunker.parse_file_with_language(path, text)


logger = logging.getLogger("code-indexer.indexer")


@dataclass
class IndexResult:
    files_indexed: int
    chunks_embedded: int
    chunks_reused: int
    files_deleted: int
    duration_s: float
    chunks_skipped: int = 0     # embed failures recorded in index_errors
    files_committed: int = 0    # manifest rows committed after their upserts
    sensitive_skipped: int = 0  # secret-bearing files excluded (privacy)
    chunks_unrepairable: int = 0  # repair: rows whose vector was not found


def _snippet(text: str, cap: int = 500) -> str:
    return text[:cap]


def _embed_key(chunk_hash: str, symbol: str | None) -> tuple[str, str | None]:
    """The identity of a stored vector: the exact embedding input.

    ``chunk_hash`` covers the chunk text and the symbol is the other half of
    ``embed_text``'s header, so two chunks with the same text under different
    symbols are two different vectors. A vector may only be carried from a
    point whose payload names this same pair -- content addressing by hash
    alone would merge them.
    """
    return (chunk_hash, symbol)


def _by_embed_key(points: list[dict[str, Any]]) -> dict[
        tuple[str, str | None],
        tuple[list[float], dict[str, Any], str]]:
    """Index a file's stored points by embedding input (first point wins).

    The point id rides along: a payload can describe the right row and still
    sit at the wrong id (a store permuted under an older pass), and identity
    is the pair -- this row, at the id its chunk_index names.
    """
    out: dict[tuple[str, str | None],
              tuple[list[float], dict[str, Any], str]] = {}
    for p in points:
        payload = p.get("payload") or {}
        vector = p.get("vector")
        if vector is None or not payload.get("chunk_hash"):
            continue
        out.setdefault(_embed_key(payload["chunk_hash"], payload.get("symbol")),
                       (vector, payload, str(p.get("id"))))
    return out


def _payload_agrees(payload: dict[str, Any], path: str, row: Any,
                    content_hash: str, lang: str) -> bool:
    """True when a stored point already describes this row exactly.

    Every field a read path can report is compared, so a point is only left
    alone when rewriting it would change nothing: the row's identity and
    range, its text digest, the file digest a stale-answer warning uses, and
    the language. ``indexed_at`` and the snippet are provenance, not identity.
    """
    return (payload.get("file") == path
            and payload.get("chunk_index") == row.chunk_index
            and payload.get("chunk_hash") == row.chunk_hash
            and payload.get("start_line") == row.start_line
            and payload.get("end_line") == row.end_line
            and payload.get("symbol") == row.symbol
            and payload.get("symbol_type") == row.symbol_type
            and payload.get("content_hash") == content_hash
            and payload.get("lang") == lang)


def _pid(project_path: str, path: str, chunk_index: int) -> str:
    return point_id(project_path, path, chunk_index)


def _point_dict(project_path: str, path: str, row: Any, vector: list[float],
                content_hash: str, lang: str, now: str) -> dict[str, Any]:
    """One point as a plain dict; the store builds the client type.

    ``row`` is a ``Chunk`` (a freshly chunked file) or a ``ChunkRow`` (an
    embedded manifest row) -- both carry the same fields, and the payload is
    the same shape either way. It used to be built twice, once per writer.
    """
    return {
        "id": _pid(project_path, path, row.chunk_index),
        "vector": vector,
        "payload": {
            "project": project_path,
            "file": path,
            "chunk_index": row.chunk_index,
            "content_hash": content_hash,
            "file_hash": content_hash[:8],
            "chunk_hash": row.chunk_hash,
            "symbol": row.symbol,
            "symbol_type": row.symbol_type,
            "lang": lang,
            "start_line": row.start_line,
            "end_line": row.end_line,
            "snippet": _snippet(row.text),
            "indexed_at": now,
        },
    }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Indexer:
    def __init__(self, cfg: Config, embedder: Embedder | None,
                 store: Store | None, progress_cb=None):
        self.cfg = cfg
        self.embedder = embedder
        self.store = store
        # progress_cb(info: dict) — called during the embed/commit loop with
        # {'files_done', 'files_total', 'chunks_done', 'chunks_total',
        #  'docs_per_s', 'eta_s'} for index-status live progress.
        self.progress_cb = progress_cb

    def physical_collection(self, slug: str) -> str:
        """The collection a vector-writing pass fills for ``slug``.

        ONE place decides the physical name: it comes from
        ``vectors.collection_for`` and therefore always carries the composed
        embedding identity, so a pass can never append vectors of one model
        to a collection built by another (ADR-0001 section 2). The caller
        swaps the result in under the slug's alias once the pass succeeds.
        """
        from . import vectors as vectors_mod
        identity = vectors_mod.embedding_identity(
            self.cfg.embed_model, self.embedder.dimension())
        return vectors_mod.collection_for(slug, identity)

    def _embed_with_errors_compat(
            self, texts: list[str],
    ) -> tuple[list[list[float] | None], list[tuple[int, str]]]:
        """embed_with_errors with a fallback for plain embed() embedders
        (test stubs, third-party adapters): on batch failure, retry per text
        so one poisoned input does not lose the whole batch."""
        method = getattr(self.embedder, "embed_with_errors", None)
        if method is not None:
            return method(texts)
        try:
            vectors = self.embedder.embed(texts)
            return list(vectors), []
        except Exception:  # noqa: BLE001 — bisect below
            vecs: list[list[float] | None] = [None] * len(texts)
            errors: list[tuple[int, str]] = []
            for i, text in enumerate(texts):
                try:
                    vecs[i] = self.embedder.embed([text])[0]
                except Exception as single_exc:  # noqa: BLE001
                    errors.append((i, str(single_exc)))
            return vecs, errors

    # -- point identity ---------------------------------------------------

    def _stored_points(self, collection: str, project_path: str,
                       path: str) -> list[dict[str, Any]]:
        """What the store currently holds for one file (payload + vector).

        A store that cannot report its points yields an empty list, and then
        every chunk is embedded rather than reused: the pass never assumes a
        vector exists because a cache said so. That assumption -- a bare
        ``(file, chunk_hash)`` flag -- is the defect this replaces, because
        it stays true when the chunk's point id moves underneath it.
        """
        reader = getattr(self.store, "points_for_file", None)
        if reader is None:
            return []
        return reader(collection, project_path, path)

    def _write_points(self, collection: str, points: list[dict[str, Any]]) -> int:
        """Upsert points in configured batches; returns how many were sent."""
        for i in range(0, len(points), self.cfg.upsert_batch):
            self.store.upsert_points(
                collection, points[i:i + self.cfg.upsert_batch])
        return len(points)

    def _purge_beyond(self, collection: str, project_path: str, path: str,
                      row_count: int, stored: list[dict[str, Any]]) -> None:
        """Drop points past the file's current chunk count.

        A file that shrank leaves its old tail at indices no row owns. The
        row count is the authority, not the previous manifest count, so a
        collection that already disagrees with the rows is cleaned too.
        """
        if any(int((p.get("payload") or {}).get("chunk_index", -1)) >= row_count
               for p in stored):
            self.store.purge_file_points(
                collection, project_path, path, min_chunk_index=row_count)

    def index_project(self, project_path: str, slug: str, manifest: Manifest,
                      force_full: bool = False,
                      collection: str | None = None,
                      vectors: bool = True) -> IndexResult:
        """One incremental pass. Caller holds the per-project lock.

        ``collection`` overrides the target collection name — the swap-based
        rebuild fills a fresh physical collection and the caller swaps it into
        place, so search against the old one stays available until the swap.
        The default is ``physical_collection(slug)``: the name carries the
        embedding identity, so a pass never writes across models.

        ``vectors=False`` is the service-free pass: it scans, parses, writes
        files/symbols/refs/imports/chunks/FTS/index_errors and records
        ``meta.vectors=off``, touching neither the embedder nor the store. A
        later vectors-on pass embeds from ``chunks``, not from source.
        """
        t0 = time.time()

        if vectors:
            if self.store is None or self.embedder is None:
                raise ValueError("a vectors-on pass needs the semantic clients")
            collection = collection or self.physical_collection(slug)
            if not self.store.collection_exists(collection):
                self.store.create_collection(collection,
                                             self.embedder.dimension())

        # Scoped indexing: the manifest's scope patterns limit the scan;
        # priority patterns order the pass. Stored meta keys:
        #   scope_include (newline-joined globs), scope_priority.
        include = [ln for ln in (manifest.get_meta("scope_include") or "")
                   .splitlines() if ln.strip()]
        priority = [ln for ln in (manifest.get_meta("scope_priority") or "")
                    .splitlines() if ln.strip()]
        # What became of every file the walk saw. Recorded at the end of the
        # pass, so a reader can reconcile the index against the tree without
        # walking it again.
        accounting = ScanAccounting()
        scanned: list[ScannedFile] = scan_project(
            project_path, max_file_bytes=self.cfg.max_file_bytes,
            previous=manifest.stat_map(), include=include or None,
            priority=priority or None, accounting=accounting,
        )
        scanned_map = {f.path: f for f in scanned}
        old_files = manifest.all_files()

        # Sensitive-content exclusion (privacy work item): the override is
        # stored in the manifest by add-project --allow-sensitive and honored
        # on every later pass; without it, secret-bearing files (by filename
        # glob or high-confidence content pattern) never reach the index.
        # Skipped files are treated as deliberately absent (not "deleted") so
        # their removal from the store is silent and repeat passes are stable.
        allow_sensitive = manifest.get_meta("allow_sensitive") == "1"
        sensitive_report = SensitiveReport()
        if not allow_sensitive:
            kept: dict[str, ScannedFile] = {}
            for path, f in scanned_map.items():
                try:
                    with open(f.abs_path, encoding="utf-8",
                              errors="replace") as fh:
                        text = fh.read(SENSITIVE_READ_CHARS)
                except OSError:
                    kept[path] = f
                    continue
                verdict = skip_verdict(path, text)
                if verdict == "filename":
                    sensitive_report.files_by_name += 1
                    sensitive_report.skipped_names.append(path)
                elif verdict == "content":
                    sensitive_report.files_by_content += 1
                    sensitive_report.skipped_content.append(path)
                else:
                    kept[path] = f
            scanned_map = kept

        # A file withheld for privacy is still a file the walk saw: it lands
        # in a named bucket instead of vanishing from the reconciliation.
        withheld = len(scanned) - len(scanned_map)
        if withheld:
            accounting.skipped[SKIP_SENSITIVE] += withheld
            accounting.examples.setdefault(SKIP_SENSITIVE, []).extend(
                list(sensitive_report.skipped_names)[:50])

        # Embedding-text format guard: a stored vector is only reusable when it
        # was built by the same embed_text() construction. A manifest with no
        # recorded format (pre-guard indexes, or a brand-new project) or a
        # different one is treated exactly like a full rebuild — the cached
        # chunk hashes describe vectors of another format, so they must not be
        # reused. The value is re-recorded at the end of a successful pass.
        recorded_format = manifest.get_meta("embed_format")
        format_changed = vectors and recorded_format != EMBED_FORMAT
        rebuild = force_full or format_changed
        if format_changed and recorded_format is not None:
            logger.info("embed_format changed (%s -> %s): full re-embed",
                        recorded_format, EMBED_FORMAT)
        # Fingerprint guard: same principle for model/dim/chunker version.
        # A legacy manifest without a fingerprint is backfilled with the
        # current config (with a one-line notice) — no forced reindex, per
        # the improvement plan.
        current_fp = (
            current_fingerprint(self.cfg.embed_model, self.embedder.dimension())
            if vectors else None)
        recorded_fp = read_fingerprint(manifest)
        if current_fp is not None and recorded_fp is None and (old_files or force_full):
            logger.info(
                "manifest has no index fingerprint; backfilling current "
                "config (%s) — no reindex required", current_fp.describe())
            write_fingerprint(manifest, current_fp)
            recorded_fp = current_fp
        # Direction-aware fingerprint guard. The rebuild-and-restamp path is
        # correct when the manifest was built by OLDER code (a legitimate
        # upgrade) or when the model/dim/format genuinely changed. It is WRONG
        # when the manifest was built by NEWER code than this process: an
        # older-code watcher sweeping would re-embed the whole project with
        # its older extraction code (fingerprint_mismatches is direction-blind)
        # and then stamp its own older version over the newer one — the flag
        # reverts and good vectors are replaced. So: warn loudly and defer the
        # re-stamp, leaving the recorded (newer) value untouched. The deferral
        # guards the fingerprint write and the direction-blind FULL REBUILD
        # only — it does not skip the pass, so a CHANGED file is still embedded
        # with this older process's extraction code while the recorded newer
        # fingerprint stands.
        defer_fingerprint_write = False
        if current_fp is not None and manifest_newer_than(recorded_fp, current_fp):
            assert recorded_fp is not None  # manifest_newer_than implies it
            logger.warning(
                "index for %s was built by NEWER code (%s) than this process "
                "(%s, pid %d): refusing the fingerprint re-stamp and the "
                "direction-blind full rebuild; changed files may still be "
                "embedded with this older extraction code. Remedy: stop this "
                "watcher (code-indexer unwatch) and restart it on the current "
                "code, or run: code-indexer reindex-project %s",
                slug, recorded_fp.describe(), current_fp.describe(),
                os.getpid(), project_path)
            defer_fingerprint_write = True
        else:
            row_diffs = ([] if current_fp is None else
                         row_side_mismatches(recorded_fp,
                                             configured_values(self.cfg.embed_model)))
            fp_changed = bool(row_diffs)
            if fp_changed:
                logger.info("index row axes changed (%s): full reparse",
                            row_diffs)
            rebuild = rebuild or fp_changed

        # Turning vectors on for a project that was kept off: no cached hash
        # describes a vector in this mode, so this pass embeds what it
        # processes instead of trusting a cache built under another mode.
        from . import vectors as vectors_mod
        if vectors and vectors_mod.recorded_mode(manifest) == vectors_mod.MODE_OFF:
            rebuild = True
        # A changed embedding identity means a NEW physical collection (the
        # name carries the identity), so no cached hash describes a vector in
        # it: re-embed instead of reusing (ADR-0001 section 2). Without this,
        # an incremental pass after a model change would fill the new
        # collection with nothing and the swap would publish an empty one.
        if vectors:
            identity = vectors_mod.embedding_identity(
                self.cfg.embed_model, self.embedder.dimension())
            recorded_identity = manifest.get_meta("embedding_version")
            if recorded_identity not in (None, identity):
                logger.info("embedding identity changed (%s -> %s): full "
                            "re-embed into a new collection",
                            recorded_identity, identity)
                rebuild = True

        # Legacy-manifest FTS backfill (migration-style, idempotent): a
        # manifest with indexed chunks but an empty chunks_fts is populated
        # from disk once; later passes maintain it incrementally.
        if manifest.fts_backfill_needed():
            logger.info("backfilling FTS lexical index (legacy manifest)")
            backfill_rows: list[tuple[str, str, str, str, str, int]] = []
            for p in list(manifest.all_files()):
                mf = manifest.get_file(p)
                abs_p = os.path.join(project_path, p)
                if mf is None:
                    continue
                try:
                    with open(abs_p, encoding="utf-8", errors="replace") as fh:
                        ftext = fh.read()
                except OSError:
                    continue
                for c in _chunk_dispatch(p, ftext):
                    backfill_rows.append((p, c.chunk_index, c.text,
                                          c.symbol or "", p, c.start_line))
            manifest.fts_backfill(backfill_rows)

        added = [p for p in scanned_map if p not in old_files]
        deleted = [p for p in old_files if p not in scanned_map]
        changed = [
            p for p in scanned_map
            if p in old_files and scanned_map[p].content_hash != old_files[p].content_hash
        ]
        if force_full or rebuild:
            changed = list(scanned_map.keys())
            added = []
            deleted = [p for p in old_files if p not in scanned_map]
            # A full rebuild must actually re-embed everything it processes:
            # the store may have been wiped independently of the manifest, and
            # a changed embedding construction describes vectors built by
            # another one. Nothing is carried from the old points (see
            # ``stored`` below, which is empty when ``rebuild`` is set).

        # 1) Purge deleted files (payload-filter delete + manifest rows).
        if vectors:
            for path in deleted:
                self.store.purge_file_points(collection, project_path, path)
        if deleted:
            manifest.delete_files(deleted)
            for path in deleted:
                manifest.fts_purge_file(path)
            logger.info("purged %d deleted files", len(deleted))

        # 2) Chunk changed/added files; carry each unchanged chunk's stored
        #    vector to the point id its CURRENT chunk_index names, and embed
        #    only the chunks whose embedding input the file's points do not
        #    already hold. With EMBED_CONCURRENCY > 1, parse/chunk in a small
        #    thread pool while the embed call is in flight (tree-sitter
        #    parsing is CPU-bound in the C extension and file reads are
        #    IO-bound; the embed HTTP call dominates wall time).
        to_embed: list[tuple[str, Chunk]] = []   # (file, chunk)
        reused = 0

        rows: list[ManifestFile] = []
        # Per-file symbol/reference/import extraction results, applied at the
        # commit phase (step 3/4) in the same commit family as the manifest
        # rows — never ahead of vectors.
        pending_meta: dict[str, tuple[list, list, list, str, str]] = {}
        # Total chunk count per processed file (for the manifest row).
        file_chunk_counts: dict[str, int] = {}
        # Parse verdict per processed file (parsed|parse_error|parse_unavailable).
        file_verdicts: dict[str, str] = {}
        # The grammar each processed file was parsed with (a `.h` header's is
        # decided from its contents, so it is not the extension's answer).
        file_languages: dict[str, str] = {}
        # Approx LOC per processed file (max chunk end_line).
        file_loc_counts: dict[str, int] = {}
        # Files that had at least one chunk queued for embedding.
        to_embed_files: set[str] = set()
        # Files the scan returned and the parse could not open again, with the
        # reason. They become a named skip (never `uncommitted`, whose one
        # cause is a chunk lost to an embed failure).
        read_failures: dict[str, str] = {}

        def _process_file(path: str) -> tuple[str, str, list[Chunk], list, str,
                                          str | None] | None:
            """Read + chunk + extract symbols for one file.

            Returns None only when the file cannot be opened now, in which
            case the reason is left in ``read_failures`` for the caller to
            record as a skip. The last element is the grammar the parse used.
            """
            f = scanned_map[path]
            try:
                with open(f.abs_path, encoding="utf-8", errors="replace") as fh:
                    file_text = fh.read()
                    chunks, verdict, lang = _parse_dispatch(path, file_text)
            except OSError as exc:
                logger.warning("cannot read %s: %s", path, exc)
                read_failures[path] = str(exc)
                return None
            if langmatrix.is_prose_or_config(lang or _lang_from_ext(path)):
                # Prose and configuration are indexed for SEARCH: their chunks
                # embed and rank, and nothing in them is a declaration or a
                # reference. The chunk-level symbol metadata is cleared here so
                # no payload echoes it -- left set, a markdown heading rides
                # every payload that echoes a chunk symbol and the phantom is
                # back. This is a payload concern only: what may enter the
                # SYMBOL TABLE is guarded once, by the AST-only rule in
                # ts_chunker.extract_symbols.
                for c in chunks:
                    c.symbol = None
                    c.symbol_type = None
            syms = ts_chunker.extract_symbols(path, file_text, chunks)
            return path, file_text, chunks, syms, verdict, lang

        def _record_file(path: str, file_text: str, chunks: list[Chunk],
                         syms, verdict: str, lang: str | None) -> None:
            """Apply one parsed file's bookkeeping (main thread only)."""
            nonlocal reused
            # ONE reference writer (v6), reading the SAME rule the language
            # matrix publishes: tree-sitter queries give confidence 'ast',
            # the textual scan stamps 'heuristic', and prose/configuration
            # stores nothing. Rows are held until the commit phase so a crash
            # never claims references for a file whose vectors are absent.
            lang = _stored_language(path, lang)
            imports, refs, confidence = reference_rows(path, file_text, lang)
            resolutions = graph_extract.resolve_imports(
                project_path, path, lang, [i[0] for i in imports])
            import_rows = [(rt, resolutions.get(rt), kind, ln)
                           for (rt, _r, kind, ln) in imports]
            pending_meta[path] = (syms, refs, import_rows, confidence, lang)
            old = old_files.get(path)
            # What this file's points already carry decides reuse -- not a
            # recorded hash, which says only that the text was embedded
            # somewhere at some time. The vector is carried from a point whose
            # payload names the same embedding input, and it is written at the
            # id the chunk's CURRENT chunk_index names; that is the repair of
            # the shifted-sequence defect, and it also heals a file whose
            # points were already permuted by an older pass.
            stored = (self._stored_points(collection, project_path, path)
                      if vectors and not rebuild else [])
            if vectors and old and old.chunk_count > len(chunks):
                self.store.purge_file_points(
                    collection, project_path, path, min_chunk_index=len(chunks))
            # FTS rows for every chunk of a (re)processed file — written in
            # the same manifest transaction family as the row commit, so the
            # lexical index never drifts from the manifest.
            manifest.fts_add_chunks([
                (path, c.chunk_index, c.text, c.symbol or "", path,
                 c.start_line) for c in chunks])
            # The chunk content itself (ADR-0001 section 3): the manifest is
            # authoritative for what a chunk is, so a vectors-on pass can
            # embed without re-reading this file. Replaced per file, so a
            # chunk that vanished from the file vanishes here too.
            manifest.replace_file_chunks(path, [
                ChunkRow(file=path, chunk_index=c.chunk_index,
                         start_line=c.start_line, end_line=c.end_line,
                         symbol=c.symbol, symbol_type=c.symbol_type,
                         chunk_hash=c.chunk_hash, text=c.text)
                for c in chunks])
            content_hash = scanned_map[path].content_hash
            by_key = _by_embed_key(stored)
            refreshed: list[dict[str, Any]] = []
            for chunk_ in chunks:
                entry = by_key.get(_embed_key(chunk_.chunk_hash, chunk_.symbol))
                if entry is None:
                    if vectors:
                        to_embed.append((path, chunk_))
                        to_embed_files.add(path)
                    continue
                reused += 1
                vector, payload, pid = entry
                if (pid != _pid(project_path, path, chunk_.chunk_index)
                        or not _payload_agrees(payload, path, chunk_,
                                               content_hash, lang)):
                    refreshed.append(_point_dict(
                        project_path, path, chunk_, vector, content_hash, lang,
                        _now()))
            if refreshed:
                self._write_points(collection, refreshed)
            if vectors:
                self._purge_beyond(collection, project_path, path, len(chunks),
                                   stored)
            f = scanned_map[path]
            rows.append(ManifestFile(path, f.content_hash, f.size, len(chunks), "ok",
                                     getattr(f, "mtime_ns", None),
                                     getattr(f, "inode", None),
                                     max((c.end_line for c in chunks),
                                         default=0),
                                     lang, verdict))
            file_chunk_counts[path] = len(chunks)
            file_loc_counts[path] = max((c.end_line for c in chunks), default=0)
            file_verdicts[path] = verdict
            file_languages[path] = lang

        def _commit_metadata(path: str) -> None:
            """Write a file's symbols, references and imports in ONE place.

            Called only after the file's vectors are in the store (or when the
            file had nothing to embed), so a crash never leaves reference rows
            claiming a file whose points are absent.
            """
            meta = pending_meta.pop(path, None)
            if meta is None:
                return
            syms, refs, import_rows, confidence, lang = meta
            manifest.replace_file_symbols(
                path,
                [SymbolRow(file=path, name=s["name"],
                           symbol_type=s["symbol_type"],
                           start_line=s["start_line"],
                           end_line=s["end_line"], source=s["source"],
                           signature=s.get("signature"),
                           visibility=s.get("visibility"),
                           qualified_name=s["qualified_name"],
                           container=s["container"],
                           language=lang,
                           decl_hash=s["decl_hash"]) for s in syms])
            manifest.replace_file_refs(path, refs, confidence)
            manifest.replace_file_imports(path, import_rows)

        def _record_read_failure(path: str) -> None:
            """A file the scan handed over and the parse could not open.

            It is a skip under the scanner's own reason, so it appears in the
            reconciliation as ``unreadable`` instead of parking in
            ``uncommitted`` -- whose only cause is a chunk lost to an embed
            failure. The reason is recorded against the file too, so
            ``index-status`` names it rather than counting a silent drop.
            """
            accounting.skip(SKIP_UNREADABLE, path)
            manifest.record_index_error(
                path, None,
                f"cannot read: {read_failures.get(path, 'unknown error')}")

        process_paths = added + changed
        concurrency = max(1, self.cfg.embed_concurrency)
        if concurrency == 1:
            for path in process_paths:
                parsed = _process_file(path)
                if parsed is None:
                    _record_read_failure(path)
                else:
                    _record_file(parsed[0], parsed[1], parsed[2], parsed[3],
                                 parsed[4], parsed[5])
        else:
            # Pipeline: parse the next file in the pool while recording the
            # current one. The embed call itself happens after the loop (one
            # batched request); the pool hides file-read/parse latency.
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = {pool.submit(_process_file, p): p
                           for p in process_paths}
                for future, path in futures.items():
                    parsed = future.result()
                    if parsed is None:
                        _record_read_failure(path)
                    else:
                        _record_file(parsed[0], parsed[1], parsed[2], parsed[3],
                                     parsed[4], parsed[5])

        # 3) Embed with per-text failure isolation (bisect on batch failure);
        #    commit each file's manifest row + symbols AFTER its points are
        #    upserted, so a crashed pass resumes where it stopped and a
        #    poisoned chunk is recorded in index_errors instead of failing
        #    the whole pass.
        embedded = 0
        skipped_errors = 0
        files_committed = 0
        by_file: dict[str, list[tuple[int, str, Chunk]]] = {}
        if to_embed:
            # Contextual embedding text (path+symbol header + chunk text):
            # raw code chunks alone land in a tight similarity band and NL
            # queries mis-rank them; the header restores file/symbol context.
            vectors, embed_errors = self._embed_with_errors_compat(
                [embed_text(p, c) for p, c in to_embed])
            for idx, msg in embed_errors:
                path, chunk_ = to_embed[idx]
                manifest.record_index_error(path, chunk_.chunk_index, msg)
                skipped_errors += 1
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            def _point(path: str, chunk_: Chunk,
                       vec: list[float]) -> dict[str, Any]:
                """The point as a plain dict; the store builds the client type."""
                return _point_dict(
                    project_path, path, chunk_, vec,
                    scanned_map[path].content_hash,
                    file_languages.get(path) or _lang_from_ext(path), now)

            # Group to-embed entries by file, upsert per file, then commit
            # that file's manifest rows — the resumability unit.
            by_file: dict[str, list[tuple[int, str, Chunk]]] = {}
            for idx, (path, chunk_) in enumerate(to_embed):
                if vectors[idx] is None:
                    continue  # poisoned chunk: recorded, skipped
                by_file.setdefault(path, []).append((idx, path, chunk_))

            t_embed0 = time.time()
            files_total = len(by_file)
            for files_done, (path, entries) in enumerate(by_file.items(), 1):
                points = [_point(path, chunk_, vectors[idx])
                          for idx, _p, chunk_ in entries]
                self._write_points(collection, points)
                embedded += len(entries)
                # Commit AFTER the vectors reached the store: a crash before
                # this line costs a re-embed of this file only; nothing is
                # claimed that does not exist.
                if path in pending_meta:
                    _commit_metadata(path)
                f = scanned_map[path]
                manifest.upsert_files([ManifestFile(
                    path, f.content_hash, f.size,
                    file_chunk_counts.get(path, 0), "ok",
                    getattr(f, "mtime_ns", None),
                    getattr(f, "inode", None),
                    file_loc_counts.get(path, 0),
                    file_languages.get(path) or _lang_from_ext(path),
                    file_verdicts.get(path))])
                files_committed += 1
                if self.progress_cb is not None:
                    elapsed = max(time.time() - t_embed0, 1e-6)
                    rate = embedded / elapsed  # chunks/s
                    remaining_files = files_total - files_done
                    eta = (rate and remaining_files *
                           (file_chunk_counts.get(path, 1) / max(rate, 1e-6))) or 0.0
                    self.progress_cb({
                        "files_done": files_done, "files_total": files_total,
                        "chunks_done": embedded,
                        "chunks_total": len(to_embed),
                        "docs_per_s": round(rate, 2), "eta_s": round(eta, 1),
                        "errors": skipped_errors})
            if skipped_errors:
                logger.warning("%d chunk(s) skipped after embed failures "
                               "(recorded in index_errors)", skipped_errors)
        # Files with nothing to embed (fully reused) still need their rows — but
        # NOT files whose chunks all failed to embed: their vectors are not in
        # the store, so committing them would claim an index that does not
        # exist. Those stay uncommitted and the next pass re-embeds them.
        all_failed = {
            path for path in file_chunk_counts
            if path not in by_file and path in to_embed_files}
        remaining_rows = [
            ManifestFile(path, scanned_map[path].content_hash,
                         scanned_map[path].size, file_chunk_counts.get(path, 0),
                         "ok", getattr(scanned_map[path], "mtime_ns", None),
                         getattr(scanned_map[path], "inode", None),
                         file_loc_counts.get(path, 0),
                         file_languages.get(path) or _lang_from_ext(path),
                         file_verdicts.get(path))
            for path in file_chunk_counts
            if path not in by_file and path not in all_failed]
        if remaining_rows:
            manifest.upsert_files(remaining_rows)
        for path in list(pending_meta):
            if path in by_file:
                continue  # already committed with its vectors
            _commit_metadata(path)
        # Attribute every reference to the definition it resolves to. Run
        # AFTER the last symbol/ref row of this pass is written, over the whole
        # store: a definition added, removed or renamed anywhere re-attributes
        # the rows that point at its name, and a row no rule may attribute is
        # left NULL and counted rather than credited to an arbitrary file.
        manifest.resolve_ref_targets()
        manifest.mark_scanned(manifest.read_git_branch(project_path))
        manifest.set_meta("last_indexed", str(time.time()))
        # The pass's accounting, counted against what the manifest now holds:
        # walked = indexed + uncommitted + skipped, with every skip carrying a
        # reason. Written here, after every row is in, so the numbers describe
        # the index a reader will be given and not the walk in progress.
        # `uncommitted` is scanned-but-not-stored: a file whose every chunk
        # failed to embed. A file the pass could not re-open is already in
        # `skipped` (`unreadable`), so it is subtracted rather than counted
        # twice.
        # An unreadable file is counted under `skipped`, so a row an older pass
        # left for it is not this pass's work: `indexed` counts the manifest
        # rows this pass did not fail to read, which keeps one file out of two
        # buckets and the identity true on a second pass as on the first.
        committed = manifest.all_files()
        accounting.indexed = sum(1 for p in committed if p not in read_failures)
        accounting.uncommitted = max(
            0, len(scanned_map) - len(read_failures) - accounting.indexed)
        write_scan_accounting(manifest, accounting)
        # One revision per committed pass: the revision a read answer was
        # read from. A counter (never a timestamp) so two passes in the same
        # second are still distinguishable.
        manifest.set_meta("index_revision", _next_revision(manifest))
        # Record the construction that produced (or refreshed) the vectors in
        # this pass, so a later construction change is detected as a rebuild.
        if vectors:
            manifest.set_meta("embed_format", EMBED_FORMAT)
        self._record_vectors_meta(manifest, vectors, skipped_errors)
        # DEFERRED when the manifest was built by newer code than this process
        # (see the direction-aware guard above): stamping our own older
        # fingerprint here is exactly the regression this pass must not make.
        if not defer_fingerprint_write and current_fp is not None:
            write_fingerprint(manifest, current_fp)
        elif not defer_fingerprint_write:
            from .fingerprint import write_row_side
            write_row_side(manifest)

        # Unchanged files keep their manifest rows; ensure they're recorded
        # (status ok) for accurate file_count.
        unchanged_rows = [
            ManifestFile(p, scanned_map[p].content_hash, scanned_map[p].size,
                         old_files[p].chunk_count, "ok",
                         getattr(scanned_map[p], "mtime_ns", None),
                         getattr(scanned_map[p], "inode", None),
                         old_files[p].loc or 0,
                         old_files[p].language or _lang_from_ext(p),
                         old_files[p].parse_verdict)
            for p in scanned_map
            if p in old_files and p not in set(added + changed)
        ]
        if unchanged_rows:
            manifest.upsert_files(unchanged_rows)

        return IndexResult(
            files_indexed=len(file_chunk_counts), chunks_embedded=embedded,
            chunks_reused=reused, files_deleted=len(deleted),
            duration_s=round(time.time() - t0, 2),
            chunks_skipped=skipped_errors, files_committed=files_committed,
            sensitive_skipped=sensitive_report.total,
        )

    def _record_vectors_meta(self, manifest: Manifest, vectors: bool,
                             errors: int = 0) -> None:
        """Record the mode and the known state of the vector side.

        A vectors-off pass never claims anything about vectors it did not
        touch: it records the mode and, for a project that was never
        vectored, ``absent``. A vectors-on pass records ``current`` unless
        chunks were lost to embed failures, which is ``error``.
        """
        from . import vectors as vectors_mod
        if not vectors:
            manifest.set_meta("vectors", vectors_mod.MODE_OFF)
            if manifest.get_meta("vectors_state") is None:
                manifest.set_meta("vectors_state", vectors_mod.STATE_ABSENT)
            vectors_mod.record_rows(manifest)
            return
        manifest.set_meta("vectors", vectors_mod.MODE_ON)
        manifest.set_meta("vectors_state",
                          vectors_mod.STATE_ERROR if errors
                          else vectors_mod.STATE_CURRENT)
        vectors_mod.record_rows(manifest)

    def embed_index(self, project_path: str, slug: str, manifest: Manifest,
                    collection: str | None = None,
                    repair: bool = False) -> IndexResult:
        """Embed the stored chunk rows; read no source file, reparse nothing.

        This is the vectors-on pass of ADR-0001 section 3: every input comes
        from ``chunks`` (text and hash) plus the file rows (content hash,
        language), so it runs with the source tree absent or unreadable.

        It is incremental by CONTENT: a row whose embedding input the
        collection already holds at this file is carried to the point id its
        current ``chunk_index`` names instead of being embedded again. That is
        also the repair for a collection written before a file's chunk
        sequence shifted -- every row is re-addressed from the vector that
        describes it, so the run is idempotent and a second run upserts
        nothing.

        ``repair=True`` writes only what it can carry: a row whose vector is
        found nowhere in the collection is counted in ``chunks_unrepairable``
        and left untouched -- never embedded, never guessed.

        ``collection`` names a NEW physical collection when the embedding
        identity changed; the caller swaps the alias afterwards and drops the
        old collection.
        """
        if self.embedder is None or self.store is None:
            raise ValueError("embedding from the manifest needs the semantic "
                             "clients")
        t0 = time.time()
        collection = collection or self.physical_collection(slug)
        dim = self.embedder.dimension()
        if not self.store.collection_exists(collection):
            self.store.create_collection(collection, dim)

        # A new identity means a new collection: nothing stored under the old
        # one is carried into it (vectors are never mixed).
        from . import vectors as vectors_mod
        identity = vectors_mod.embedding_identity(self.cfg.embed_model, dim)
        same_identity = manifest.get_meta("embedding_version") in (None, identity)

        files = manifest.all_files()
        rows_by_file: dict[str, list[ChunkRow]] = {}
        for row in manifest.all_chunks():
            if row.file in files:
                rows_by_file.setdefault(row.file, []).append(row)

        now = _now()
        reused = 0
        unrepairable = 0
        pending: list[tuple[str, ChunkRow]] = []
        carried: list[dict[str, Any]] = []
        stored_by_file: dict[str, list[dict[str, Any]]] = {}
        for file, rows in rows_by_file.items():
            file_row = files[file]
            stored = (self._stored_points(collection, project_path, file)
                      if same_identity else [])
            stored_by_file[file] = stored
            by_key = _by_embed_key(stored)
            for row in rows:
                entry = by_key.get(_embed_key(row.chunk_hash, row.symbol))
                if entry is None:
                    pending.append((file, row))
                    continue
                reused += 1
                vector, payload, pid = entry
                if (pid != _pid(project_path, file, row.chunk_index)
                        or not _payload_agrees(payload, file, row,
                                               file_row.content_hash,
                                               file_row.language)):
                    carried.append(_point_dict(
                        project_path, file, row, vector, file_row.content_hash,
                        file_row.language, now))
        if carried:
            self._write_points(collection, carried)
        # AFTER the carry: a point past the row count may be a row's only copy
        # of its vector, and the read above already holds what it needs.
        for file, rows in rows_by_file.items():
            self._purge_beyond(collection, project_path, file, len(rows),
                               stored_by_file[file])

        embedded = 0
        skipped = 0
        written_files = {p["payload"]["file"] for p in carried}
        if repair:
            unrepairable = len(pending)
        elif pending:
            texts = [embed_text(f, _chunk_from_row(r)) for f, r in pending]
            vectors, errors = self._embed_with_errors_compat(texts)
            for idx, msg in errors:
                file, row = pending[idx]
                manifest.record_index_error(file, row.chunk_index, msg)
                skipped += 1
            by_file: dict[str, list[tuple[int, ChunkRow]]] = {}
            for idx, (file, row) in enumerate(pending):
                if vectors[idx] is None:
                    continue
                by_file.setdefault(file, []).append((idx, row))
            for done, (file, entries) in enumerate(by_file.items(), 1):
                file_row = files[file]
                points = [_point_dict(project_path, file, row, vectors[idx],
                                      file_row.content_hash, file_row.language,
                                      now) for idx, row in entries]
                self._write_points(collection, points)
                embedded += len(entries)
                written_files.add(file)
                if self.progress_cb is not None:
                    self.progress_cb({
                        "files_done": done, "files_total": len(by_file),
                        "chunks_done": embedded, "chunks_total": len(pending),
                        "docs_per_s": 0.0, "eta_s": 0.0, "errors": skipped})

        # Record the construction that produced these vectors, on the vector
        # side only: the rows were not touched and their axes are already
        # recorded (ADR-0001 section 2).
        from .fingerprint import write_embedding_side
        write_embedding_side(manifest, self.cfg.embed_model, dim)
        manifest.set_meta("embed_format", EMBED_FORMAT)
        manifest.set_meta("last_indexed", str(time.time()))
        manifest.set_meta("index_revision", _next_revision(manifest))
        manifest.set_meta("vectors", vectors_mod.MODE_ON)
        manifest.set_meta("vectors_state",
                          vectors_mod.STATE_ERROR if (skipped or unrepairable)
                          else vectors_mod.STATE_CURRENT)
        return IndexResult(
            files_indexed=0, chunks_embedded=embedded, chunks_reused=reused,
            files_deleted=0, duration_s=round(time.time() - t0, 2),
            chunks_skipped=skipped, files_committed=len(written_files),
            chunks_unrepairable=unrepairable,
        )


def _next_revision(manifest: Manifest) -> str:
    """The next index revision for this manifest: one per committed pass."""
    raw = manifest.get_meta("index_revision") or "0"
    try:
        return str(int(raw) + 1)
    except ValueError:
        # A legacy/manual value that is not a counter: start a clean sequence.
        return "1"


def _chunk_from_row(row) -> Chunk:
    """A Chunk from a manifest chunk row: what the embedder would see.

    The signature/container fields do not participate in the embedding text;
    they are empty here because the manifest deliberately stores the chunk
    content, not the extractor's whole result.
    """
    return Chunk(text=row.text, chunk_hash=row.chunk_hash, symbol=row.symbol,
                 start_line=row.start_line, end_line=row.end_line,
                 chunk_index=row.chunk_index, symbol_type=row.symbol_type)


def _stored_language(path: str, parsed: str | None) -> str:
    """The manifest's language spelling for a file, from the grammar it used.

    For an ambiguous `.h` header the grammar IS the fact: the file and its
    symbols are labelled with the language that actually parsed them (``c``
    or ``cpp``), so `--language` and the coverage counts describe what
    happened. Every other spelling keeps the manifest's own vocabulary --
    ``.sh`` stores ``shell`` here while the chunker's map says ``bash`` -- and
    a parse that yielded no grammar (``None``) falls back to the name.
    """
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if parsed and ext in ts_chunker.AMBIGUOUS_HEADER_EXTENSIONS:
        return parsed
    return _lang_from_ext(path)


def _lang_from_ext(path: str) -> str:
    """The language a file's extension names, spelled as the reference
    extractor and the capability matrix spell it.

    The C++ header and translation-unit spellings are one language: a ``.hpp``
    read as the bare token ``hpp`` has no grammar, no symbol rows and no
    references, so its ``#include`` directives were silently absent from the
    graph. ``.h`` is the one header spelling whose language its NAME cannot
    settle -- C and C++ projects both ship it -- so this function only gives
    the family default (``cpp``); the grammar a file is actually parsed with is
    decided from the file's own bytes in ``ts_chunker._choose_grammar`` and is
    what the index stores as the file's and its symbols' language.
    """
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "py": "python", "md": "markdown", "js": "javascript", "jsx": "javascript",
        "ts": "typescript", "tsx": "tsx",
        "rs": "rust", "go": "go", "java": "java", "c": "c", "cpp": "cpp",
        "cc": "cpp", "cxx": "cpp", "hpp": "cpp", "hxx": "cpp",
        "hh": "cpp", "h": "cpp", "cs": "csharp", "sh": "shell", "toml": "toml",
        "yaml": "yaml", "yml": "yaml", "json": "json", "txt": "text",
    }.get(ext, ext or "text")
