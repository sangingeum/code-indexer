"""Indexer: scan -> hash diff -> chunk -> embed changed -> upsert/purge.

Implements design §6. The content-hash diff (never mtime) is what makes
incremental indexing correct across branch switches — do not "optimize"
back to mtime (§7/F).
"""

from __future__ import annotations

import fnmatch
import logging
import os
import time
from dataclasses import dataclass

from .chunker import Chunk, chunk
from .config import Config
from .embed_text import EMBED_FORMAT, embed_text
from .embedder import Embedder
from .fingerprint import (Fingerprint, current_fingerprint,
                          fingerprint_mismatches, read_fingerprint,
                          write_fingerprint)
from . import ts_chunker
from .manifest import Manifest, ManifestFile, RefRow, SymbolRow
from .scanner import ScannedFile, scan_project
from . import graph_extract
from .sensitive import SensitiveReport, file_is_sensitive
from .store import Store, point_id


def _chunk_dispatch(path: str, text: str) -> list[Chunk]:
    """Chunker seam (design §5): tree-sitter AST when supported, else fallback."""
    return ts_chunker.chunk_text(path, text)

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
    def __init__(self, cfg: Config, embedder: Embedder, store: Store,
                 progress_cb=None):
        self.cfg = cfg
        self.embedder = embedder
        self.store = store
        # progress_cb(info: dict) — called during the embed/commit loop with
        # {'files_done', 'files_total', 'chunks_done', 'chunks_total',
        #  'docs_per_s', 'eta_s'} for index-status live progress.
        self.progress_cb = progress_cb

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
        except Exception as exc:  # noqa: BLE001 — bisect below
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
                      collection: str | None = None) -> IndexResult:
        """One incremental pass. Caller holds the per-project lock.

        ``collection`` overrides the target collection name — the swap-based
        rebuild (fingerprint mismatch) fills idx_<slug>__new and the caller
        swaps it into place, so search against the old collection stays
        available until the swap.
        """
        t0 = time.time()
        collection = collection or f"idx_{slug}"

        if not self.store.collection_exists(collection):
            self.store.create_collection(collection, self.embedder.dimension())

        scanned: list[ScannedFile] = scan_project(
            project_path, max_file_bytes=self.cfg.max_file_bytes,
            previous=manifest.stat_map(),
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
                        text = fh.read(65536 * 4)
                except OSError:
                    kept[path] = f
                    continue
                verdict = file_is_sensitive(path, text)
                if verdict == "filename":
                    sensitive_report.files_by_name += 1
                    sensitive_report.skipped_names.append(path)
                elif verdict == "content":
                    sensitive_report.files_by_content += 1
                    sensitive_report.skipped_content.append(path)
                else:
                    kept[path] = f
            scanned_map = kept

        # Embedding-text format guard: a stored vector is only reusable when it
        # was built by the same embed_text() construction. A manifest with no
        # recorded format (pre-guard indexes, or a brand-new project) or a
        # different one is treated exactly like a full rebuild — the cached
        # chunk hashes describe vectors of another format, so they must not be
        # reused. The value is re-recorded at the end of a successful pass.
        recorded_format = manifest.get_meta("embed_format")
        format_changed = recorded_format != EMBED_FORMAT
        rebuild = force_full or format_changed
        if format_changed and recorded_format is not None:
            logger.info("embed_format changed (%s -> %s): full re-embed",
                        recorded_format, EMBED_FORMAT)
        # Fingerprint guard: same principle for model/dim/chunker version.
        # A legacy manifest without a fingerprint is backfilled with the
        # current config (with a one-line notice) — no forced reindex, per
        # the improvement plan.
        current_fp = current_fingerprint(self.cfg.embed_model,
                                         self.embedder.dimension())
        recorded_fp = read_fingerprint(manifest)
        if recorded_fp is None and (old_files or force_full):
            logger.info(
                "manifest has no index fingerprint; backfilling current "
                "config (%s) — no reindex required", current_fp.describe())
            write_fingerprint(manifest, current_fp)
            recorded_fp = current_fp
        fp_changed = bool(fingerprint_mismatches(recorded_fp, current_fp))
        if fp_changed:
            logger.info("index fingerprint changed (%s): full re-embed",
                        fingerprint_mismatches(recorded_fp, current_fp))
        rebuild = rebuild or fp_changed

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
        # Per-file symbol/ref extraction results, applied at step 4 in the
        # same commit as the manifest rows (never ahead of vectors).
        pending_symbols: list[tuple[str, list, list]] = []
        # Total chunk count per processed file (for the manifest row).
        file_chunk_counts: dict[str, int] = {}
        # Approx LOC per processed file (max chunk end_line).
        file_loc_counts: dict[str, int] = {}
        # Files that had at least one chunk queued for embedding.
        to_embed_files: set[str] = set()

        def _process_file(path: str) -> tuple[str, str, list[Chunk], list, list] | None:
            """Read + chunk + extract symbols/refs for one file (pure)."""
            f = scanned_map[path]
            try:
                with open(f.abs_path, encoding="utf-8", errors="replace") as fh:
                    file_text = fh.read()
                    chunks = _chunk_dispatch(path, file_text)
            except OSError as exc:
                logger.warning("cannot read %s: %s", path, exc)
                return None
            syms = ts_chunker.extract_symbols(path, file_text, chunks)
            refs = ts_chunker.extract_refs(path, file_text)
            return path, file_text, chunks, syms, refs

        def _record_file(path: str, chunks: list[Chunk], syms, refs) -> None:
            """Apply one parsed file's bookkeeping (main thread only)."""
            nonlocal reused
            pending_symbols.append((path, syms, refs))
            old = old_files.get(path)
            if old and old.chunk_count > len(chunks):
                self.store.purge_file_points(
                    collection, project_path, path, min_chunk_index=len(chunks))
            # FTS rows for every chunk of a (re)processed file — written in
            # the same manifest transaction family as the row commit, so the
            # lexical index never drifts from the manifest.
            manifest.fts_add_chunks([
                (path, c.chunk_index, c.text, c.symbol or "", path,
                 c.start_line) for c in chunks])
            # Graph extraction (graph work item): AST imports/references for
            # query languages, textual fallback otherwise — replace-by-file.
            abs_path = os.path.join(project_path, path)
            try:
                with open(abs_path, encoding="utf-8", errors="replace") as fh:
                    ftext = fh.read()
            except OSError:
                ftext = ""
            if ftext:
                lang = _lang_from_ext(path)
                imports, refs = graph_extract.extract_graph(
                    path, ftext, lang)
                textual_fallback = False
                if not imports and not refs and \
                        lang not in graph_extract.LANGUAGES_WITH_AST:
                    imports, refs = graph_extract.extract_graph_textual(
                        path, ftext)
                    textual_fallback = True
                resolutions = graph_extract.resolve_imports(
                    project_path, path, lang, [i[0] for i in imports])
                manifest.replace_file_imports(path, [
                    (rt, resolutions.get(rt), kind, ln)
                    for (rt, _r, kind, ln) in imports])
                if textual_fallback:
                    manifest.replace_file_refs_heuristic(path, refs)
                else:
                    manifest.replace_file_refs(path, refs)
            for chunk_ in chunks:
                key = f"{path}|{chunk_.chunk_hash}"
                if key in seen_hashes and not rebuild:
                    reused += 1
                else:
                    to_embed.append((path, chunk_))
                    to_embed_files.add(path)
            f = scanned_map[path]
            rows.append(ManifestFile(path, f.content_hash, f.size, len(chunks), "ok",
                                     getattr(f, "mtime_ns", None),
                                     getattr(f, "inode", None),
                                     max((c.end_line for c in chunks),
                                         default=0),
                                     _lang_from_ext(path)))
            file_chunk_counts[path] = len(chunks)
            file_loc_counts[path] = max((c.end_line for c in chunks), default=0)

        process_paths = added + changed
        concurrency = max(1, self.cfg.embed_concurrency)
        if concurrency == 1:
            for path in process_paths:
                parsed = _process_file(path)
                if parsed is not None:
                    _record_file(parsed[0], parsed[2], parsed[3], parsed[4])
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
                    if parsed is not None:
                        _record_file(parsed[0], parsed[2], parsed[3], parsed[4])

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
            from qdrant_client.models import PointStruct
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            def _point(path: str, chunk_: Chunk, vec: list[float]) -> PointStruct:
                return PointStruct(
                    id=point_id(project_path, path, chunk_.chunk_index),
                    vector=vec,
                    payload={
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
                    })

            # Group to-embed entries by file, upsert per file, then commit
            # that file's manifest rows — the resumability unit.
            by_file: dict[str, list[tuple[int, str, Chunk]]] = {}
            for idx, (path, chunk_) in enumerate(to_embed):
                if vectors[idx] is None:
                    continue  # poisoned chunk: recorded, skipped
                by_file.setdefault(path, []).append((idx, path, chunk_))

            pending_syms = {entry[0]: (entry[1], entry[2])
                            for entry in pending_symbols}
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
                if path in pending_syms:
                    syms, refs = pending_syms.pop(path)
                    manifest.replace_file_symbols(
                        path,
                        [SymbolRow(file=path, name=s["name"],
                                   symbol_type=s["symbol_type"],
                                   start_line=s["start_line"],
                                   end_line=s["end_line"], source=s["source"],
                                   signature=s.get("signature"),
                                   visibility=s.get("visibility"))
                         for s in syms],
                        [RefRow(file=path, line=r["line"],
                                src_symbol=r["src_symbol"],
                                relationship=r["relationship"],
                                target=r["target"]) for r in refs])
                f = scanned_map[path]
                manifest.upsert_files([ManifestFile(
                    path, f.content_hash, f.size,
                    file_chunk_counts.get(path, 0), "ok",
                    getattr(f, "mtime_ns", None),
                    getattr(f, "inode", None),
                    file_loc_counts.get(path, 0), _lang_from_ext(path))])
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
                         file_loc_counts.get(path, 0), _lang_from_ext(path))
            for path in file_chunk_counts
            if path not in by_file and path not in all_failed]
        if remaining_rows:
            manifest.upsert_files(remaining_rows)
        for path, syms, refs in pending_symbols:
            if path in by_file:
                continue  # already committed with its vectors
            manifest.replace_file_symbols(
                path,
                [SymbolRow(file=path, name=s["name"], symbol_type=s["symbol_type"],
                           start_line=s["start_line"], end_line=s["end_line"],
                           source=s["source"], signature=s.get("signature"),
                           visibility=s.get("visibility")) for s in syms],
                [RefRow(file=path, line=r["line"], src_symbol=r["src_symbol"],
                        relationship=r["relationship"], target=r["target"])
                 for r in refs],
            )
        manifest.mark_scanned(manifest.read_git_branch(project_path))
        manifest.set_meta("last_indexed", str(time.time()))
        # Record the construction that produced (or refreshed) the vectors in
        # this pass, so a later construction change is detected as a rebuild.
        manifest.set_meta("embed_format", EMBED_FORMAT)
        write_fingerprint(manifest, current_fp)

        # Unchanged files keep their manifest rows; ensure they're recorded
        # (status ok) for accurate file_count.
        unchanged_rows = [
            ManifestFile(p, scanned_map[p].content_hash, scanned_map[p].size,
                         old_files[p].chunk_count, "ok",
                         getattr(scanned_map[p], "mtime_ns", None),
                         getattr(scanned_map[p], "inode", None),
                         old_files[p].loc or 0,
                         old_files[p].language or _lang_from_ext(p))
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


def _lang_from_ext(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "py": "python", "md": "markdown", "js": "javascript", "ts": "typescript",
        "rs": "rust", "go": "go", "java": "java", "c": "c", "cpp": "cpp",
        "h": "c", "cs": "csharp", "sh": "shell", "toml": "toml",
        "yaml": "yaml", "yml": "yaml", "json": "json", "txt": "text",
    }.get(ext, ext or "text")