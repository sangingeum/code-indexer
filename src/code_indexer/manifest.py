"""Per-project SQLite manifest (design §3; schema v3 adds signatures).

Schema:
    files(path TEXT PK, content_hash TEXT, size INT, chunk_count INT, status TEXT)
    meta(key TEXT PK, value TEXT)  # schema_version, last_full_scan, branch, last_indexed
    symbols(file, name, symbol_type, start_line, end_line, source)   # v2
    symbols.signature, symbols.visibility                            # v3
    symbol_refs(file, line, src_symbol, relationship, target)        # v2

Opened in WAL mode. All writes are short transactions — safe under the
per-project lock discipline; WAL allows concurrent readers during indexing.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass

logger = logging.getLogger("code-indexer.manifest")

SCHEMA_VERSION = "3"

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
CREATE TABLE IF NOT EXISTS symbols (
    file TEXT NOT NULL,
    name TEXT NOT NULL,
    symbol_type TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'regex',
    PRIMARY KEY (file, name, start_line)
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_file_start ON symbols(file, start_line);
CREATE TABLE IF NOT EXISTS symbol_refs (
    file TEXT NOT NULL,
    line INTEGER NOT NULL,
    src_symbol TEXT,
    relationship TEXT NOT NULL,
    target TEXT NOT NULL,
    PRIMARY KEY (file, line, relationship, target)
);
CREATE INDEX IF NOT EXISTS idx_symbol_refs_target ON symbol_refs(target);
"""


@dataclass
class ManifestFile:
    path: str
    content_hash: str
    size: int
    chunk_count: int
    status: str


@dataclass
class SymbolRow:
    file: str
    name: str
    symbol_type: str
    start_line: int
    end_line: int
    source: str  # 'ast' | 'regex'
    signature: str | None = None  # v3: decl text up to body; NULL tolerated
    visibility: str | None = None  # v3: 'public' | 'private'; NULL → public


@dataclass
class RefRow:
    file: str
    line: int
    src_symbol: str | None
    relationship: str  # calls | inherits | includes | references
    target: str


class Manifest:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._conn:
            self._conn.executescript(_SCHEMA)
            # v2 -> v3 migration: add the two new columns before the version
            # bump (design §3). Existing rows keep NULL signature/visibility;
            # readers tolerate NULL (print no signature; treat as public).
            existing_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(symbols)")
            }
            for col in ("signature", "visibility"):
                if col not in existing_cols:
                    self._conn.execute(
                        f"ALTER TABLE symbols ADD COLUMN {col} TEXT")
            # schema_version must actually advance on old manifests
            # (INSERT OR IGNORE would leave v1 stuck forever).
            old_version = self.get_meta("schema_version")
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value "
                "WHERE CAST(excluded.value AS INTEGER) > CAST(value AS INTEGER)",
                (SCHEMA_VERSION,),
            )
        self._migrated_from = old_version

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
        """Remove file rows AND their symbol/ref rows in one transaction."""
        with self._conn:
            self._conn.executemany(
                "DELETE FROM files WHERE path = ?", [(p,) for p in paths]
            )
            self._conn.executemany(
                "DELETE FROM symbols WHERE file = ?", [(p,) for p in paths]
            )
            self._conn.executemany(
                "DELETE FROM symbol_refs WHERE file = ?", [(p,) for p in paths]
            )

    # -- symbols / refs --------------------------------------------------

    def replace_file_symbols(self, file: str, symbols: list[SymbolRow],
                             refs: list[RefRow]) -> None:
        """Atomically replace a file's symbol and ref rows.

        Delete-before-insert keyed by file: chunk ordering shifts make blind
        upserts duplicate rows. Must be called in the same pass that commits
        the file's manifest row (design: symbols never ahead of vectors).
        """
        with self._conn:
            self._conn.execute("DELETE FROM symbols WHERE file = ?", (file,))
            self._conn.execute("DELETE FROM symbol_refs WHERE file = ?", (file,))
            self._conn.executemany(
                "INSERT OR REPLACE INTO symbols(file, name, symbol_type, "
                "start_line, end_line, source, signature, visibility) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(s.file, s.name, s.symbol_type, s.start_line, s.end_line,
                  s.source, s.signature, s.visibility)
                 for s in symbols],
            )
            self._conn.executemany(
                "INSERT OR REPLACE INTO symbol_refs(file, line, src_symbol, "
                "relationship, target) VALUES (?, ?, ?, ?, ?)",
                [(r.file, r.line, r.src_symbol, r.relationship, r.target)
                 for r in refs],
            )

    _SYMBOL_COLS = ("SELECT file, name, symbol_type, start_line, end_line, "
                    "source, signature, visibility FROM symbols")

    def find_symbols(self, name: str | None, symbol_type: str | None = None,
                     substring: bool = False, limit: int = 25,
                     file: str | None = None) -> list[SymbolRow]:
        """Exact-first (ast rows above regex rows); optional capped substring tier.

        name=None is browse mode (design §2.3): --type/--file filters only,
        capped by limit.
        """
        clauses: list[str] = []
        args: list = []
        if name:
            op = "LIKE" if substring else "="
            pattern = f"%{name}%" if substring else name
            clauses.append(f"name {op} ? COLLATE NOCASE")
            args.append(pattern)
        if symbol_type:
            clauses.append("symbol_type = ?")
            args.append(symbol_type)
        if file:
            clauses.append("file = ?")
            args.append(file)
        sql = self._SYMBOL_COLS
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += (" ORDER BY CASE source WHEN 'ast' THEN 0 ELSE 1 END, "
                "name, start_line LIMIT ?")
        args.append(limit)
        return [SymbolRow(*row) for row in
                self._conn.execute(sql, args).fetchall()]

    def symbols_with_files(self) -> tuple[dict[str, ManifestFile], list[SymbolRow]]:
        """All files + all symbols for the skeleton projection (design §2.1).

        Both from one connection: under WAL a concurrent writer can't tear
        the pair apart (replace_file_symbols is atomic per file, and each
        SELECT is a point-in-time snapshot).
        """
        files = self.all_files()
        syms = [SymbolRow(*row) for row in self._conn.execute(
            self._SYMBOL_COLS + " ORDER BY file, start_line").fetchall()]
        return files, syms

    def symbols_for_file(self, file: str) -> list[SymbolRow]:
        return [SymbolRow(*row) for row in self._conn.execute(
            self._SYMBOL_COLS +
            " WHERE file = ? ORDER BY start_line", (file,)
        ).fetchall()]

    def all_symbol_names(self) -> set[str]:
        return {r[0] for r in self._conn.execute("SELECT DISTINCT name FROM symbols")}

    def find_refs(self, target: str, relationship: str | None = None,
                  limit: int = 25) -> list[RefRow]:
        sql = ("SELECT file, line, src_symbol, relationship, target "
               "FROM symbol_refs WHERE target = ?")
        args: list = [target]
        if relationship:
            sql += " AND relationship = ?"
            args.append(relationship)
        sql += " ORDER BY file, line LIMIT ?"
        args.append(limit)
        return [RefRow(*row) for row in self._conn.execute(sql, args).fetchall()]

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
