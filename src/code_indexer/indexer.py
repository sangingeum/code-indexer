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
from .embedder import Embedder
from . import ts_chunker
from .manifest import Manifest, ManifestFile, RefRow, SymbolRow
from .scanner import ScannedFile, scan_project
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


def _snippet(text: str, cap: int = 500) -> str:
    return text[:cap]


class Indexer:
    def __init__(self, cfg: Config, embedder: Embedder, store: Store):
        self.cfg = cfg
        self.embedder = embedder
        self.store = store

    def index_project(self, project_path: str, slug: str, manifest: Manifest,
                      force_full: bool = False) -> IndexResult:
        """One incremental pass. Caller holds the per-project lock."""
        t0 = time.time()
        collection = f"idx_{slug}"

        if not self.store.collection_exists(collection):
            self.store.create_collection(collection, self.embedder.dimension())

        scanned: list[ScannedFile] = scan_project(
            project_path, max_file_bytes=self.cfg.max_file_bytes
        )
        scanned_map = {f.path: f for f in scanned}
        old_files = manifest.all_files()

        added = [p for p in scanned_map if p not in old_files]
        deleted = [p for p in old_files if p not in scanned_map]
        changed = [
            p for p in scanned_map
            if p in old_files and scanned_map[p].content_hash != old_files[p].content_hash
        ]
        if force_full:
            changed = list(scanned_map.keys())
            added = []
            deleted = [p for p in old_files if p not in scanned_map]
            # A full rebuild must actually re-embed: the chunk_hashes cache
            # records "this vector exists in Qdrant", and Qdrant may have been
            # wiped/reset independently of the manifest (owner reset, collection
            # loss). Stale cache + empty collection = silent index loss. Force
            # re-embed everything on a full pass.
            manifest.set_meta("chunk_hashes", "")

        # 1) Purge deleted files (payload-filter delete + manifest rows).
        for path in deleted:
            self.store.purge_file_points(collection, project_path, path)
        if deleted:
            manifest.delete_files(deleted)
            logger.info("purged %d deleted files", len(deleted))

        # 2) Chunk changed/added files; embed only chunks whose chunk_hash
        #    is new. Unchanged-chunk hashes are cached in the meta table
        #    (chunk_hash -> seen) keyed per file+index via the manifest
        #    chunk table below.
        to_embed: list[tuple[str, Chunk]] = []   # (file, chunk)
        points: list = []
        reused = 0
        seen_hashes = self._load_chunk_hashes(manifest)

        rows: list[ManifestFile] = []
        # Per-file symbol/ref extraction results, applied at step 4 in the
        # same commit as the manifest rows (never ahead of vectors).
        pending_symbols: list[tuple[str, list, list]] = []
        for path in added + changed:
            f = scanned_map[path]
            try:
                with open(f.abs_path, encoding="utf-8", errors="replace") as fh:
                    file_text = fh.read()
                    chunks = _chunk_dispatch(path, file_text)
            except OSError as exc:
                logger.warning("cannot read %s: %s", path, exc)
                continue

            pending_symbols.append((
                path,
                ts_chunker.extract_symbols(path, file_text, chunks),
                ts_chunker.extract_refs(path, file_text),
            ))

            # Shrinkage: delete surplus chunk indices before upsert (§3/B).
            old = old_files.get(path)
            if old and old.chunk_count > len(chunks):
                self.store.purge_file_points(
                    collection, project_path, path, min_chunk_index=len(chunks)
                )
                # If shrinking, also purge same-hash duplicates? No: ids for
                # kept indices are identical, surplus are purged above.

            for chunk_ in chunks:
                key = f"{path}|{chunk_.chunk_hash}"
                if key in seen_hashes and not force_full:
                    reused += 1
                else:
                    to_embed.append((path, chunk_))

            rows.append(ManifestFile(path, f.content_hash, f.size, len(chunks), "ok"))

        # 3) Embed in batches (one HTTP round trip per batch).
        embedded = 0
        if to_embed:
            vectors = self.embedder.embed([c.text for _, c in to_embed])
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            batch_points = []
            for (path, chunk_), vec in zip(to_embed, vectors):
                pid = point_id(project_path, path, chunk_.chunk_index)
                payload = {
                    "project": project_path,
                    "file": path,
                    "chunk_index": chunk_.chunk_index,
                    "content_hash": scanned_map[path].content_hash,
                    "chunk_hash": chunk_.chunk_hash,
                    "symbol": chunk_.symbol,
                    "symbol_type": chunk_.symbol_type,
                    "lang": _lang_from_ext(path),
                    "start_line": chunk_.start_line,
                    "end_line": chunk_.end_line,
                    "snippet": _snippet(chunk_.text),
                    "indexed_at": now,
                }
                from qdrant_client.models import PointStruct
                batch_points.append(PointStruct(id=pid, vector=vec, payload=payload))
            # Record chunk hashes only AFTER the vectors are actually in
            # Qdrant: recording up-front means a mid-pass crash leaves the
            # cache claiming vectors exist that were never upserted (silent
            # index loss). Post-commit recording keeps first-index resumable
            # — a retry re-embeds only what never reached the store.
            for i in range(0, len(batch_points), self.cfg.upsert_batch):
                self.store.upsert_points(collection, batch_points[i:i + self.cfg.upsert_batch])
            for (path, chunk_), _vec in zip(to_embed, vectors):
                self._remember_chunk_hash(manifest, path, chunk_)
            embedded = len(to_embed)

        # 4) Manifest update in one transaction (symbols/refs included so
        #    they can never be committed ahead of the vectors).
        if rows:
            manifest.upsert_files(rows)
        for path, syms, refs in pending_symbols:
            manifest.replace_file_symbols(
                path,
                [SymbolRow(file=path, name=s["name"], symbol_type=s["symbol_type"],
                           start_line=s["start_line"], end_line=s["end_line"],
                           source=s["source"]) for s in syms],
                [RefRow(file=path, line=r["line"], src_symbol=r["src_symbol"],
                        relationship=r["relationship"], target=r["target"])
                 for r in refs],
            )
        manifest.mark_scanned(manifest.read_git_branch(project_path))
        manifest.set_meta("last_indexed", str(time.time()))

        # Unchanged files keep their manifest rows; ensure they're recorded
        # (status ok) for accurate file_count.
        unchanged_rows = [
            ManifestFile(p, scanned_map[p].content_hash, scanned_map[p].size,
                         old_files[p].chunk_count, "ok")
            for p in scanned_map
            if p in old_files and p not in set(added + changed)
        ]
        if unchanged_rows:
            manifest.upsert_files(unchanged_rows)

        return IndexResult(
            files_indexed=len(rows), chunks_embedded=embedded,
            chunks_reused=reused, files_deleted=len(deleted),
            duration_s=round(time.time() - t0, 2),
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