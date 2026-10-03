"""Per-project SQLite manifest (design §3; schema v3 adds signatures, v4 adds
the index fingerprint).

Schema:
    files(path TEXT PK, content_hash TEXT, size INT, chunk_count INT, status TEXT)
    meta(key TEXT PK, value TEXT)  # schema_version, last_full_scan, branch,
                                   # last_indexed, embed_format + v4 fingerprint:
                                   # embed_model, embed_dim, embed_text_version,
                                   # chunker_version
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

SCHEMA_VERSION = "4"

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
CREATE TABLE IF NOT EXISTS imports (
    file TEXT NOT NULL,
    raw_target TEXT NOT NULL,
    resolved_file TEXT,          -- best-effort project-relative resolution
    kind TEXT NOT NULL,          -- import|include|require|use|...
    line INTEGER NOT NULL,
    PRIMARY KEY (file, raw_target, line)
);
CREATE TABLE IF NOT EXISTS refs (
    file TEXT NOT NULL,
    line INTEGER NOT NULL,
    from_symbol TEXT,            -- NULL when the call site is module-level
    to_name TEXT NOT NULL,
    relationship TEXT NOT NULL,  -- call|type|base_class|identifier
    confidence TEXT NOT NULL,    -- ast | heuristic
    PRIMARY KEY (file, line, to_name, relationship)
);
CREATE INDEX IF NOT EXISTS idx_refs_to_name ON refs(to_name);
CREATE TABLE IF NOT EXISTS index_errors (
    file TEXT NOT NULL,
    chunk_index INTEGER,
    error TEXT NOT NULL,
    at REAL NOT NULL,
    PRIMARY KEY (file, chunk_index)
);
"""


@dataclass
class ManifestFile:
    path: str
    content_hash: str
    size: int
    chunk_count: int
    status: str
    mtime_ns: int | None = None  # v4: stat fast-path (NULL = legacy row)
    inode: int | None = None


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
            # v4: stat fast-path columns on files (size/mtime_ns/inode from
            # the last completed pass; NULL on legacy rows -> full hash).
            file_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(files)")
            }
            for col, decl in (("mtime_ns", "INTEGER"), ("inode", "INTEGER")):
                if col not in file_cols:
                    self._conn.execute(
                        f"ALTER TABLE files ADD COLUMN {col} {decl}")
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
            "SELECT path, content_hash, size, chunk_count, status, "
            "mtime_ns, inode FROM files"
        )
        return {
            row[0]: ManifestFile(*row)
            for row in cur.fetchall()
        }

    def stat_map(self) -> dict[str, tuple[int, int, int, str]]:
        """path -> (size, mtime_ns, inode, content_hash) for the fast-path."""
        cur = self._conn.execute(
            "SELECT path, size, mtime_ns, inode, content_hash FROM files "
            "WHERE mtime_ns IS NOT NULL AND inode IS NOT NULL")
        return {r[0]: (r[1], r[2], r[3], r[4]) for r in cur.fetchall()}

    def get_file(self, path: str) -> ManifestFile | None:
        row = self._conn.execute(
            "SELECT path, content_hash, size, chunk_count, status, "
            "mtime_ns, inode FROM files WHERE path = ?",
            (path,),
        ).fetchone()
        return ManifestFile(*row) if row else None

    def upsert_files(self, rows: list[ManifestFile]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT INTO files(path, content_hash, size, chunk_count, status, "
                "mtime_ns, inode) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET content_hash=excluded.content_hash, "
                "size=excluded.size, chunk_count=excluded.chunk_count, "
                "status=excluded.status, mtime_ns=excluded.mtime_ns, "
                "inode=excluded.inode",
                [(r.path, r.content_hash, r.size, r.chunk_count, r.status,
                  r.mtime_ns, r.inode) for r in rows],
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

    # -- index errors (resilient indexing: poisoned chunks are recorded,
    #    not fatal) -------------------------------------------------------

    def record_index_error(self, file: str, chunk_index: int | None,
                           error: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO index_errors(file, chunk_index, error, at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(file, chunk_index) DO UPDATE SET "
                "error=excluded.error, at=excluded.at",
                (file, chunk_index, error, time.time()),
            )

    def clear_index_errors(self, file: str | None = None) -> None:
        if file is None:
            with self._conn:
                self._conn.execute("DELETE FROM index_errors")
        else:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM index_errors WHERE file = ?", (file,))

    def last_index_errors(self, limit: int = 3) -> list[dict]:
        rows = self._conn.execute(
            "SELECT file, chunk_index, error, at FROM index_errors "
            "ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        return [{"file": r[0], "chunk_index": r[1], "error": r[2], "at": r[3]}
                for r in rows]

    def count_index_errors(self) -> int:
        return int(self._conn.execute(
            "SELECT COUNT(*) FROM index_errors").fetchone()[0])

    # -- FTS5 lexical index (hybrid retrieval) ---------------------------

    def fts(self):
        """FTS wrapper over this manifest's connection (same transaction
        scope as the manifest writes — no drift)."""
        from .fts import FtsIndex
        return FtsIndex(self._conn)

    def fts_add_chunks(self, rows: list[tuple[str, int, str, str, str, int]]) -> None:
        """Insert chunk text into chunks_fts (call inside the same commit as
        the manifest rows). Rows are (file, chunk_index, content, symbol,
        path, start_line)."""
        if rows:
            self.fts().add_chunks(rows)

    def fts_purge_file(self, file: str) -> None:
        try:
            self.fts().purge_file(file)
        except Exception as exc:  # noqa: BLE001 — table may not exist yet
            logger.debug("fts purge skipped for %s: %s", file, exc)

    def fts_search(self, query: str, limit: int = 30) -> list[dict]:
        return self.fts().search(query, limit)

    def fts_backfill_needed(self) -> bool:
        """True when chunks exist in the manifest but chunks_fts is empty
        (legacy manifest): the caller backfills migration-style, idempotent."""
        total = sum(f.chunk_count for f in self.all_files().values())
        return self.fts().needs_backfill(total)

    def fts_backfill(self, chunk_texts: list[tuple[str, int, str, str, str, int]]) -> None:
        """Populate chunks_fts from (file, chunk_index, content, symbol, path,
        line) rows; idempotent via the empty-table check at the call site."""
        self.fts_add_chunks(chunk_texts)

    def replace_file_imports(self, file: str,
                             rows: list[tuple[str, str | None, str, int]]
                             ) -> None:
        """Replace a file's import rows: (raw_target, resolved_file, kind,
        line). Called in the same commit family as the symbol rows."""
        cur = self._conn.cursor()
        cur.execute("DELETE FROM imports WHERE file = ?", (file,))
        cur.executemany(
            "INSERT OR REPLACE INTO imports(file, raw_target, resolved_file,"
            " kind, line) VALUES (?, ?, ?, ?, ?)",
            [(file, rt, rf, kind, ln) for (rt, rf, kind, ln) in rows])
        self._conn.commit()

    def replace_file_refs(self, file: str,
                          rows: list[tuple[int | None, str, str, str]]
                          ) -> None:
        """Replace a file's reference rows: (line, from_symbol, to_name,
        relationship). Confidence is stamped here: 'ast' — rows produced by
        tree-sitter queries; the textual fallback stamps 'heuristic'."""
        cur = self._conn.cursor()
        cur.execute("DELETE FROM refs WHERE file = ?", (file,))
        cur.executemany(
            "INSERT OR REPLACE INTO refs(file, line, from_symbol, to_name,"
            " relationship, confidence) VALUES (?, ?, ?, ?, ?, 'ast')",
            [(file, ln, frm, to, rel) for (ln, frm, to, rel) in rows])
        self._conn.commit()

    def replace_file_refs_heuristic(self, file: str,
                                    rows: list[tuple[int | None, str, str, str]]
                                    ) -> None:
        """Textual-fallback variant of replace_file_refs (confidence=
        'heuristic'); languages with AST queries never land here."""
        cur = self._conn.cursor()
        cur.execute("DELETE FROM refs WHERE file = ?", (file,))
        cur.executemany(
            "INSERT OR REPLACE INTO refs(file, line, from_symbol, to_name,"
            " relationship, confidence) VALUES (?, ?, ?, ?, ?, 'heuristic')",
            [(file, ln, frm, to, rel) for (ln, frm, to, rel) in rows])
        self._conn.commit()

    def callers_of(self, name: str, limit: int = 200) -> list[dict]:
        """Fan-in edges: files/symbols that reference `name`."""
        return [dict(zip(("file", "line", "from_symbol", "relationship",
                          "confidence"), r))
                for r in self._conn.execute(
                    "SELECT file, line, from_symbol, relationship, confidence"
                    " FROM refs WHERE to_name = ? ORDER BY file, line"
                    " LIMIT ?", (name, limit))]

    def callees_of_file_symbol(self, file: str, symbol: str | None,
                               limit: int = 200) -> list[dict]:
        """Fan-out edges originating from one symbol (or the whole file when
        symbol is None)."""
        if symbol is None:
            rows = self._conn.execute(
                "SELECT line, to_name, relationship, confidence FROM refs"
                " WHERE file = ? ORDER BY line LIMIT ?",
                (file, limit)).fetchall()
            return [dict(zip(("line", "to_name", "relationship",
                              "confidence"), r)) for r in rows]
        rows = self._conn.execute(
            "SELECT line, to_name, relationship, confidence FROM refs"
            " WHERE file = ? AND from_symbol = ? ORDER BY line LIMIT ?",
            (file, symbol, limit)).fetchall()
        return [dict(zip(("line", "to_name", "relationship", "confidence"), r))
                for r in rows]

    def fan_in_counts(self, limit: int = 20) -> list[tuple[str, int]]:
        """Top N names by reference count (overview hotspots)."""
        return [(r[0], int(r[1])) for r in self._conn.execute(
            "SELECT to_name, COUNT(*) AS n FROM refs GROUP BY to_name"
            " ORDER BY n DESC, to_name LIMIT ?", (limit,))]

    def import_rows(self, limit: int = 5000) -> list[dict]:
        return [dict(zip(("file", "raw_target", "resolved_file", "kind",
                          "line"), r))
                for r in self._conn.execute(
                    "SELECT file, raw_target, resolved_file, kind, line"
                    " FROM imports LIMIT ?", (limit,))]

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
