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


def _parse_dispatch(path: str, text: str) -> tuple[list[Chunk], str]:
    """Chunker seam + the file's parse verdict, from ONE parse.

    The verdict is the parser's own (``parsed``/``parse_error``) or
    ``parse_unavailable`` when no AST exists at all -- no grammar pack, no
    grammar for the extension, or a parser exception; it is never inferred
    from the chunks themselves (ADR-0004 section 7).
    """
    return ts_chunker.parse_file(path, text)


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


def _snippet(text: str, cap: int = 500) -> str:
    return text[:cap]


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
            # A full rebuild must actually re-embed: the chunk_hashes cache
            # records "this vector exists in Qdrant", and Qdrant may have been
            # wiped/reset independently of the manifest (owner reset, collection
            # loss). Stale cache + empty collection = silent index loss. Force
            # re-embed everything on a full pass. The same applies when the
            # embedding-text format changed: cached hashes describe vectors
            # built by a different construction, so they cannot be reused.
            manifest.set_meta("chunk_hashes", "")

        # 1) Purge deleted files (payload-filter delete + manifest rows).
        if vectors:
            for path in deleted:
                self.store.purge_file_points(collection, project_path, path)
        if deleted:
            manifest.delete_files(deleted)
            for path in deleted:
                manifest.fts_purge_file(path)
            logger.info("purged %d deleted files", len(deleted))

        # 2) Chunk changed/added files; embed only chunks whose chunk_hash
        #    is new. With EMBED_CONCURRENCY > 1, parse/chunk in a small
        #    thread pool while the embed call is in flight (tree-sitter
        #    parsing is CPU-bound in the C extension and file reads are
        #    IO-bound; the embed HTTP call dominates wall time).
        to_embed: list[tuple[str, Chunk]] = []   # (file, chunk)
        points: list = []
        reused = 0
        seen_hashes = self._load_chunk_hashes(manifest)

        rows: list[ManifestFile] = []
        # Per-file symbol/reference/import extraction results, applied at the
        # commit phase (step 3/4) in the same commit family as the manifest
        # rows — never ahead of vectors.
        pending_meta: dict[str, tuple[list, list, list, str]] = {}
        # Total chunk count per processed file (for the manifest row).
        file_chunk_counts: dict[str, int] = {}
        # Parse verdict per processed file (parsed|parse_error|parse_unavailable).
        file_verdicts: dict[str, str] = {}
        # Approx LOC per processed file (max chunk end_line).
        file_loc_counts: dict[str, int] = {}
        # Files that had at least one chunk queued for embedding.
        to_embed_files: set[str] = set()
        # Files the scan returned and the parse could not open again, with the
        # reason. They become a named skip (never `uncommitted`, whose one
        # cause is a chunk lost to an embed failure).
        read_failures: dict[str, str] = {}

        def _process_file(path: str) -> tuple[str, str, list[Chunk], list, str] | None:
            """Read + chunk + extract symbols for one file.

            Returns None only when the file cannot be opened now, in which
            case the reason is left in ``read_failures`` for the caller to
            record as a skip.
            """
            f = scanned_map[path]
            try:
                with open(f.abs_path, encoding="utf-8", errors="replace") as fh:
                    file_text = fh.read()
                    chunks, verdict = _parse_dispatch(path, file_text)
            except OSError as exc:
                logger.warning("cannot read %s: %s", path, exc)
                read_failures[path] = str(exc)
                return None
            if langmatrix.is_prose_or_config(_lang_from_ext(path)):
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
            return path, file_text, chunks, syms, verdict

        def _record_file(path: str, file_text: str, chunks: list[Chunk],
                         syms, verdict: str) -> None:
            """Apply one parsed file's bookkeeping (main thread only)."""
            nonlocal reused
            # ONE reference writer (v6), reading the SAME rule the language
            # matrix publishes: tree-sitter queries give confidence 'ast',
            # the textual scan stamps 'heuristic', and prose/configuration
            # stores nothing. Rows are held until the commit phase so a crash
            # never claims references for a file whose vectors are absent.
            lang = _lang_from_ext(path)
            imports, refs, confidence = reference_rows(path, file_text, lang)
            resolutions = graph_extract.resolve_imports(
                project_path, path, lang, [i[0] for i in imports])
            import_rows = [(rt, resolutions.get(rt), kind, ln)
                           for (rt, _r, kind, ln) in imports]
            pending_meta[path] = (syms, refs, import_rows, confidence)
            old = old_files.get(path)
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
            for chunk_ in chunks:
                key = f"{path}|{chunk_.chunk_hash}"
                if vectors and key in seen_hashes and not rebuild:
                    reused += 1
                elif vectors:
                    to_embed.append((path, chunk_))
                    to_embed_files.add(path)
            f = scanned_map[path]
            rows.append(ManifestFile(path, f.content_hash, f.size, len(chunks), "ok",
                                     getattr(f, "mtime_ns", None),
                                     getattr(f, "inode", None),
                                     max((c.end_line for c in chunks),
                                         default=0),
                                     _lang_from_ext(path), verdict))
            file_chunk_counts[path] = len(chunks)
            file_loc_counts[path] = max((c.end_line for c in chunks), default=0)
            file_verdicts[path] = verdict

        def _commit_metadata(path: str) -> None:
            """Write a file's symbols, references and imports in ONE place.

            Called only after the file's vectors are in the store (or when the
            file had nothing to embed), so a crash never leaves reference rows
            claiming a file whose points are absent.
            """
            meta = pending_meta.pop(path, None)
            if meta is None:
                return
            syms, refs, import_rows, confidence = meta
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
                           language=_lang_from_ext(path),
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
                    _record_file(parsed[0], parsed[1], parsed[2], parsed[3], parsed[4])
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
                        _record_file(parsed[0], parsed[1], parsed[2], parsed[3], parsed[4])

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
            from .store import point_id
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            def _point(path: str, chunk_: Chunk,
                       vec: list[float]) -> dict[str, Any]:
                """The point as a plain dict; the store builds the client type."""
                return {
                    "id": point_id(project_path, path, chunk_.chunk_index),
                    "vector": vec,
                    "payload": {
                        "project": project_path,
                        "file": path,
                        "chunk_index": chunk_.chunk_index,
                        "content_hash": scanned_map[path].content_hash,
                        "file_hash": scanned_map[path].content_hash[:8],
                        "chunk_hash": chunk_.chunk_hash,
                        "symbol": chunk_.symbol,
                        "symbol_type": chunk_.symbol_type,
                        "lang": _lang_from_ext(path),
                        "start_line": chunk_.start_line,
                        "end_line": chunk_.end_line,
                        "snippet": _snippet(chunk_.text),
                        "indexed_at": now,
                    },
                }

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
                for i in range(0, len(points), self.cfg.upsert_batch):
                    self.store.upsert_points(
                        collection, points[i:i + self.cfg.upsert_batch])
                for idx, _p, chunk_ in entries:
                    self._remember_chunk_hash(manifest, path, chunk_)
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
                    file_loc_counts.get(path, 0), _lang_from_ext(path),
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
                         file_loc_counts.get(path, 0), _lang_from_ext(path),
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
                    collection: str | None = None) -> IndexResult:
        """Embed the stored chunk rows; read no source file, reparse nothing.

        This is the vectors-on pass of ADR-0001 section 3: every input comes
        from ``chunks`` (text and hash) plus the file rows (content hash,
        language), so it runs with the source tree absent or unreadable. The
        chunk-hash cache keeps it incremental — a chunk already vectored in
        this collection costs nothing.

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

        # A new identity means a new collection: no hash recorded under the
        # old collection may be reused here (vectors are never mixed).
        from . import vectors as vectors_mod
        identity = vectors_mod.embedding_identity(self.cfg.embed_model, dim)
        if manifest.get_meta("embedding_version") not in (None, identity):
            manifest.set_meta("chunk_hashes", "")

        files = manifest.all_files()
        chunks = [r for r in manifest.all_chunks() if r.file in files]
        seen = self._load_chunk_hashes(manifest)
        pending = [(r, f"{r.file}|{r.chunk_hash}") for r in chunks
                   if f"{r.file}|{r.chunk_hash}" not in seen
                   or manifest.get_meta("embedding_version") not in (None, identity)]
        reused = len(chunks) - len(pending)

        embedded = 0
        skipped = 0
        if pending:
            texts = [embed_text(r.file, _chunk_from_row(r)) for r, _k in pending]
            vectors, errors = self._embed_with_errors_compat(texts)
            for idx, msg in errors:
                row = pending[idx][0]
                manifest.record_index_error(row.file, row.chunk_index, msg)
                skipped += 1
            from .store import point_id
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            by_file: dict[str, list[tuple[int, Any]]] = {}
            for idx, (row, _key) in enumerate(pending):
                if vectors[idx] is None:
                    continue
                by_file.setdefault(row.file, []).append((idx, row))
            for file, entries in by_file.items():
                file_row = files[file]
                points = []
                for idx, row in entries:
                    points.append({
                        "id": point_id(project_path, file, row.chunk_index),
                        "vector": vectors[idx],
                        "payload": {
                            "project": project_path,
                            "file": file,
                            "chunk_index": row.chunk_index,
                            "content_hash": file_row.content_hash,
                            "file_hash": file_row.content_hash[:8],
                            "chunk_hash": row.chunk_hash,
                            "symbol": row.symbol,
                            "symbol_type": row.symbol_type,
                            "lang": file_row.language,
                            "start_line": row.start_line,
                            "end_line": row.end_line,
                            "indexed_at": now,
                        },
                    })
                for i in range(0, len(points), self.cfg.upsert_batch):
                    self.store.upsert_points(
                        collection, points[i:i + self.cfg.upsert_batch])
                for _idx, row in entries:
                    self._remember_chunk_hash(manifest, file, _chunk_from_row(row))
                embedded += len(entries)
                if self.progress_cb is not None:
                    self.progress_cb({
                        "files_done": len(by_file), "files_total": len(by_file),
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
                          vectors_mod.STATE_ERROR if skipped
                          else vectors_mod.STATE_CURRENT)
        return IndexResult(
            files_indexed=0, chunks_embedded=embedded, chunks_reused=reused,
            files_deleted=0, duration_s=round(time.time() - t0, 2),
            chunks_skipped=skipped, files_committed=len(by_file) if pending else 0,
        )

    # -- chunk-hash cache (design §6 step 6) ---------------------------

    def _load_chunk_hashes(self, manifest: Manifest) -> set[str]:
        raw = manifest.get_meta("chunk_hashes") or ""
        return set(raw.split("\n")) if raw else set()

    def _remember_chunk_hash(self, manifest: Manifest, file: str, chunk_: Chunk) -> None:
        raw = manifest.get_meta("chunk_hashes") or ""
        key = f"{file}|{chunk_.chunk_hash}"
        if key not in raw:
            lines = [ln for ln in raw.split("\n") if ln]
            lines.append(key)
            manifest.set_meta("chunk_hashes", "\n".join(lines[-5000:]))


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


def _lang_from_ext(path: str) -> str:
    """The language a file's extension names, spelled as the reference
    extractor and the capability matrix spell it.

    The C++ header and translation-unit spellings are one language: a ``.hpp``
    read as the bare token ``hpp`` has no grammar, no symbol rows and no
    references, so its ``#include`` directives were silently absent from the
    graph. ``.h`` stays C, because that is what the chunker has always called
    it.
    """
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "py": "python", "md": "markdown", "js": "javascript", "jsx": "javascript",
        "ts": "typescript", "tsx": "tsx",
        "rs": "rust", "go": "go", "java": "java", "c": "c", "cpp": "cpp",
        "cc": "cpp", "cxx": "cpp", "hpp": "cpp", "hxx": "cpp",
        "hh": "cpp", "h": "c", "cs": "csharp", "sh": "shell", "toml": "toml",
        "yaml": "yaml", "yml": "yaml", "json": "json", "txt": "text",
    }.get(ext, ext or "text")
