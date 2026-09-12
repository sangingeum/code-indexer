"""Per-project SQLite manifest (design §3).

Schema:
    files(path TEXT PK, content_hash TEXT, size INT, chunk_count INT, status TEXT)
    meta(key TEXT PK, value TEXT)  # schema_version, last_full_scan, branch, last_indexed

Opened in WAL mode. All writes are short transactions — safe under the
per-project lock discipline; WAL allows concurrent readers during indexing.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass

logger = logging.getLogger("mcp-code-indexer.manifest")

SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    size INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok'
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


@dataclass
class ManifestFile:
    path: str
    content_hash: str
    size: int
    chunk_count: int
    status: str


class Manifest:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )

    def close(self) -> None:
        self._conn.close()

    # -- files ---------------------------------------------------------

    def all_files(self) -> dict[str, ManifestFile]:
        cur = self._conn.execute(
            "SELECT path, content_hash, size, chunk_count, status FROM files"
        )
        return {
            row[0]: ManifestFile(*row)
            for row in cur.fetchall()
        }

    def get_file(self, path: str) -> ManifestFile | None:
        row = self._conn.execute(
            "SELECT path, content_hash, size, chunk_count, status FROM files WHERE path = ?",
            (path,),
        ).fetchone()
        return ManifestFile(*row) if row else None

    def upsert_files(self, rows: list[ManifestFile]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT INTO files(path, content_hash, size, chunk_count, status) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET content_hash=excluded.content_hash, "
                "size=excluded.size, chunk_count=excluded.chunk_count, status=excluded.status",
                [(r.path, r.content_hash, r.size, r.chunk_count, r.status) for r in rows],
            )

    def delete_files(self, paths: list[str]) -> None:
        with self._conn:
            self._conn.executemany(
                "DELETE FROM files WHERE path = ?", [(p,) for p in paths]
            )

    # -- meta ----------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def mark_scanned(self, branch: str | None = None) -> None:
        self.set_meta("last_full_scan", str(time.time()))
        if branch is not None:
            self.set_meta("branch", branch)

    def read_git_branch(self, project_root: str) -> str | None:
        import os
        head = os.path.join(project_root, ".git", "HEAD")
        try:
            with open(head, encoding="utf-8") as f:
                content = f.read().strip()
            if content.startswith("ref: refs/heads/"):
                return content[len("ref: refs/heads/"):]
            return content[:12]
        except OSError:
            return None