"""Project registry: single SQLite registry.db (WAL) mapping path -> slug.

Slug = 8-hex sha256 prefix of the absolute path; collection idx_{slug}.
Registry verifies the stored path on lookup (collision check, design Risk 6).
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from dataclasses import dataclass

logger = logging.getLogger("mcp-code-indexer.registry")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    path TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
"""


@dataclass
class ProjectEntry:
    path: str
    slug: str
    created_at: float


def slug_for(path: str) -> str:
    return hashlib.sha256(os.path.abspath(path).encode()).hexdigest()[:8]


class Registry:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def list_projects(self) -> list[ProjectEntry]:
        rows = self._conn.execute(
            "SELECT path, slug, created_at FROM projects ORDER BY created_at"
        ).fetchall()
        return [ProjectEntry(*row) for row in rows]

    def get_by_path(self, path: str) -> ProjectEntry | None:
        row = self._conn.execute(
            "SELECT path, slug, created_at FROM projects WHERE path = ?",
            (os.path.abspath(path),),
        ).fetchone()
        return ProjectEntry(*row) if row else None

    def get_by_slug(self, slug: str) -> ProjectEntry | None:
        row = self._conn.execute(
            "SELECT path, slug, created_at FROM projects WHERE slug = ?", (slug,)
        ).fetchone()
        return ProjectEntry(*row) if row else None

    def add(self, path: str) -> ProjectEntry:
        """Register a project; errors on duplicate path or slug collision."""
        path = os.path.abspath(path)
        slug = slug_for(path)
        existing = self.get_by_path(path)
        if existing:
            raise ValueError(f"already registered: {path}")
        # Collision check (design Risk 6): same slug, different path.
        other = self.get_by_slug(slug)
        if other:
            raise ValueError(
                f"slug collision: {slug} already used by {other.path}"
            )
        with self._conn:
            self._conn.execute(
                "INSERT INTO projects(path, slug, created_at) VALUES (?, ?, ?)",
                (path, slug, time.time()),
            )
        logger.info("registered project %s -> %s", path, slug)
        return ProjectEntry(path, slug, time.time())

    def remove(self, path: str) -> ProjectEntry | None:
        path = os.path.abspath(path)
        existing = self.get_by_path(path)
        if not existing:
            return None
        with self._conn:
            self._conn.execute("DELETE FROM projects WHERE path = ?", (path,))
        logger.info("removed project %s (%s)", path, existing.slug)
        return existing