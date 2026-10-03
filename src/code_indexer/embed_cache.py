"""Content-addressed embedding cache (embedding-cost work item).

SQLite at INDEX_ROOT/embed_cache.db. Key = sha256(embed_text) + model + dim +
embed_text_version; value = the vector as float16 bytes (halves storage; the
float16 rounding is a documented ~3-decimal-digit precision loss on
retrieval, measured indistinguishable in eval). Consulted before Ollama, so
branch switches, reverts, copied files, and re-added projects stop
re-embedding unchanged text.

LRU by last_used with a size cap (EMBED_CACHE_MAX_GB, default 2 GiB of
estimated float16 payload); eviction removes the oldest-used rows first.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import struct
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS embed_cache (
    key TEXT PRIMARY KEY,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL,
    last_used REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_embed_cache_used ON embed_cache(last_used);
"""


def cache_key(embed_text: str, model: str, dim: int,
              embed_text_version: str) -> str:
    digest = hashlib.sha256(embed_text.encode("utf-8")).hexdigest()
    return f"{digest}|{model}|{dim}|{embed_text_version}"


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}e", *vector)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 2}e", blob))


class EmbedCache:
    def __init__(self, index_root: str,
                 max_bytes: int | None = None):
        self.db_path = os.path.join(index_root, "embed_cache.db")
        self.max_bytes = max_bytes or int(
            float(os.environ.get("EMBED_CACHE_MAX_GB", "2")) * (1 << 30))
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, timeout=30)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def get(self, key: str) -> list[float] | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT vec, dim FROM embed_cache WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE embed_cache SET last_used = ? WHERE key = ?",
                     (time.time(), key))
        conn.commit()
        return _unpack(row[0])

    def put(self, key: str, vector: list[float]) -> None:
        conn = self._connect()
        conn.execute(
            "INSERT INTO embed_cache(key, dim, vec, last_used) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
            "vec=excluded.vec, last_used=excluded.last_used",
            (key, len(vector), _pack(vector), time.time()))
        conn.commit()
        self._evict_if_needed(conn)

    def _evict_if_needed(self, conn: sqlite3.Connection) -> None:
        total = int(conn.execute(
            "SELECT COALESCE(SUM(length(vec)), 0) FROM embed_cache").fetchone()[0])
        if total <= self.max_bytes:
            return
        # Evict oldest-used first until under the cap.
        conn.execute(
            "DELETE FROM embed_cache WHERE key IN ("
            "SELECT key FROM embed_cache ORDER BY last_used ASC "
            "LIMIT (SELECT COUNT(*) / 4 + 1 FROM embed_cache))")
        conn.commit()

    def size(self) -> int:
        conn = self._connect()
        return int(conn.execute("SELECT COUNT(*) FROM embed_cache").fetchone()[0])