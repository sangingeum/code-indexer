"""Project registry: single SQLite registry.db (WAL) mapping path -> slug.

Slug = 8-hex sha256 prefix of the absolute path; collection idx_{slug}.
Registry verifies the stored path on lookup (collision check, design Risk 6).

Optional custom names (v2 schema): add_project(path, name=...) registers a
caller-chosen name; its collection is idx_<name> (sanitized) instead of the
hash slug. Schema migration is additive: the ``name`` column is added with
ALTER TABLE when missing; existing rows keep name=NULL and their hash slugs.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
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

# v2: optional display/custom collection name.
_MIGRATE_ADD_NAME = "ALTER TABLE projects ADD COLUMN name TEXT"
_NAME_UNIQUE_IDX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_name "
    "ON projects(name) WHERE name IS NOT NULL"
)


@dataclass
class ProjectEntry:
    path: str
    slug: str
    created_at: float
    name: str | None = None


def slug_for(path: str) -> str:
    return hashlib.sha256(os.path.abspath(path).encode()).hexdigest()[:8]


def sanitize_name(name: str) -> str:
    """Sanitize a caller-provided collection name.

    Allowed: letters, digits, underscore, hyphen; 1-64 chars. Everything
    else collapses to '_' (collections become idx_<sanitized>). Leading
    non-alphanumerics are stripped so the name can't start oddly.
    """
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_-")
    if not cleaned:
        raise ValueError("name sanitizes to empty — use letters/digits/_/-")
    return cleaned[:64]


class Registry:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        with self._conn:
            self._conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Additive migration: add the nullable `name` column if missing."""
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(projects)")]
        if cols and "name" not in cols:
            self._conn.execute(_MIGRATE_ADD_NAME)
            logger.info("registry migrated: added nullable 'name' column")

    def close(self) -> None:
        self._conn.close()

    def list_projects(self) -> list[ProjectEntry]:
        rows = self._conn.execute(
            "SELECT path, slug, created_at, name FROM projects ORDER BY created_at"
        ).fetchall()
        return [ProjectEntry(*row) for row in rows]

    def get_by_path(self, path: str) -> ProjectEntry | None:
        row = self._conn.execute(
            "SELECT path, slug, created_at, name FROM projects WHERE path = ?",
            (os.path.abspath(path),),
        ).fetchone()
        return ProjectEntry(*row) if row else None

    def get_by_slug(self, slug: str) -> ProjectEntry | None:
        row = self._conn.execute(
            "SELECT path, slug, created_at, name FROM projects WHERE slug = ?", (slug,)
        ).fetchone()
        return ProjectEntry(*row) if row else None

    def get_by_name(self, name: str) -> ProjectEntry | None:
        row = self._conn.execute(
            "SELECT path, slug, created_at, name FROM projects WHERE name = ?",
            (name,),
        ).fetchone()
        return ProjectEntry(*row) if row else None

    def add(self, path: str, name: str | None = None) -> ProjectEntry:
        """Register a project; errors on duplicate path, slug or name collision.

        With ``name`` given, the sanitized name becomes the slug (collection
        idx_<name>) instead of the hash slug.
        """
        path = os.path.abspath(path)
        existing = self.get_by_path(path)
        if existing:
            raise ValueError(f"already registered: {path}")
        if name:
            clean = sanitize_name(name)
            clash = self.get_by_name(clean) or self.get_by_slug(clean)
            if clash:
                raise ValueError(
                    f"name collision: {clean!r} already used by {clash.path}"
                )
            slug = clean
        else:
            slug = slug_for(path)
            # Collision check (design Risk 6): same slug, different path.
            other = self.get_by_slug(slug)
            if other:
                raise ValueError(
                    f"slug collision: {slug} already used by {other.path}"
                )
        with self._conn:
            self._conn.execute(
                "INSERT INTO projects(path, slug, created_at, name) VALUES (?, ?, ?, ?)",
                (path, slug, time.time(), name),
            )
        logger.info("registered project %s -> %s", path, slug)
        return ProjectEntry(path, slug, time.time(), name)

    def remove(self, path: str) -> ProjectEntry | None:
        path = os.path.abspath(path)
        existing = self.get_by_path(path)
        if not existing:
            return None
        with self._conn:
            self._conn.execute("DELETE FROM projects WHERE path = ?", (path,))
        logger.info("removed project %s (%s)", path, existing.slug)
        return existing