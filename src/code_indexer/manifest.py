"""Per-project SQLite manifest (schema v3 adds signatures, v4 the index
fingerprint, v6 unifies the reference stores, v7 gives a symbol a line-free
identity and a file a parse verdict).

Schema:
    files(path TEXT PK, content_hash TEXT, size INT, chunk_count INT,
          status TEXT, mtime_ns INT, inode INT, loc INT, language TEXT,
          parse_verdict TEXT)                                          # v7
    meta(key TEXT PK, value TEXT)  # schema_version, last_full_scan, branch,
                                   # last_indexed, index_revision, embed_format
                                   # + fingerprint: embed_model, embed_dim,
                                   # embed_text_version, chunker_version,
                                   # extraction_version
    symbols(file, qualified_name, symbol_type, decl_hash,     # v7 key
            id, name, container, language, start_line, end_line,
            source, signature, visibility)                        # v7
    refs(file, line, from_symbol, to_name, relationship, confidence,
         receiver, target_symbol_id, resolution)              # v6 / v7
    chunks(file, chunk_index, start_line, end_line, symbol, symbol_type,
           chunk_hash, text)                                          # v7

The symbol identity is ``(file, qualified_name, symbol_type, decl_hash)``:
line-free, so a definition shifted by a line keeps its identity, and two
overloads differing only by their signature are two rows. ``start_line`` and
``end_line`` are attributes of the row. ``id`` is a single-column surrogate
assigned at insert from this manifest's own counter: it is revision-scoped
by contract, a reparse re-inserts every row, and no consumer may persist it
(ADR-0002 section 2).

``decl_hash`` is the sha256 of the normalized declaration signature the
chunker produced. A row that carries a signature but no ``decl_hash`` hashes
that signature; a row with neither hashes its name and kind. Every one of
those bases is LINE-FREE, which is what makes the identity survive a line
shift; a reformat that changes the signature changes the identity — stated,
not papered over. The last basis is a total-key fallback for rows no pass
writes (extraction always supplies a signature): two such rows of the same
name and kind in one file share an identity and collapse to one.

``refs.target_symbol_id`` and ``refs.resolution`` are written by the index
pass's resolver (``Manifest.resolve_ref_targets``): a reference is attributed
to the definition it resolves to, and one no rule may attribute keeps a NULL
target with ``resolution='none'`` -- the rule is stated once, on that method.
The
``chunks`` table is created by this revision and populated by the same pass
that writes the file's rows: it stores the chunk text and hash so a later
vectors-on pass embeds straight from the manifest without re-reading a
source file (ADR-0001 section 3).

The ``refs`` table is the ONE reference store (v6): one writer
(``graph_extract``), one row shape, one honest relationship and confidence
per row. The former ``symbol_refs`` table is dropped on migration; the
``receiver`` column carries the receiver expression of a member access as
written (plain identifier or NULL). Rows are re-extracted on the next pass —
the EXTRACTION_VERSION bump flags every existing index needs-reindex, so a
migrated manifest is never queried as if it were complete.

Rows are keyed by ``(file, line, to_name, relationship)``: ``receiver`` is
NOT part of the key, so two same-name member reads on ONE line with two
different receivers collapse to the first in document order. That collapse is
documented in ``graph_extract``; two reads on separate lines are both kept.

``files.parse_verdict`` is one of ``parsed``, ``parse_error`` or
``parse_unavailable``, read from the parser's own result (``has_error``,
``is_missing``) — never inferred from the chunker, which cannot fail
(ADR-0004 section 7).

Opened in WAL mode. All writes are short transactions — safe under the
per-project lock discipline; WAL allows concurrent readers during indexing.

Two open modes, two contracts. A WRITE open (``writable=True``, the default)
runs the migration, which is what a schema bump means: the recorded
``schema_version`` is advanced and, for a pre-revision-7 manifest, the
old-key ``symbols`` table is re-created empty (its rows cannot be derived).
A READ open (``writable=False``, every read path) sets no journal mode, runs
no DDL and cannot write a row: it exposes the RECORDED ``schema_version``
and leaves the migration to the next pass, so a reader can destroy nothing.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from urllib.parse import quote

from . import ordering

logger = logging.getLogger("code-indexer.manifest")

SCHEMA_VERSION = "7"

# The statements are executed one at a time inside the migration transaction
# (executescript would commit the transaction it is handed).
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    size INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    mtime_ns INTEGER,
    inode INTEGER,
    loc INTEGER,
    language TEXT,
    parse_verdict TEXT
)""",
    """CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
)""",
    # v7: line-free identity key + single-column surrogate id.
    """CREATE TABLE IF NOT EXISTS symbols (
    file TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    symbol_type TEXT NOT NULL,
    decl_hash TEXT NOT NULL,
    id INTEGER NOT NULL,
    name TEXT NOT NULL,
    container TEXT,
    language TEXT,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'regex',
    signature TEXT,
    visibility TEXT,
    PRIMARY KEY (file, qualified_name, symbol_type, decl_hash)
)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_symbols_id ON symbols(id)",
    "CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name)",
    "CREATE INDEX IF NOT EXISTS idx_symbols_qualified"
    " ON symbols(qualified_name)",
    "CREATE INDEX IF NOT EXISTS idx_symbols_container ON symbols(container)",
    "CREATE INDEX IF NOT EXISTS idx_symbols_file_start"
    " ON symbols(file, start_line)",
    """CREATE TABLE IF NOT EXISTS imports (
    file TEXT NOT NULL,
    raw_target TEXT NOT NULL,
    resolved_file TEXT,          -- best-effort project-relative resolution
    kind TEXT NOT NULL,          -- import|include|require|use|...
    line INTEGER NOT NULL,
    PRIMARY KEY (file, raw_target, line)
)""",
    """CREATE TABLE IF NOT EXISTS refs (
    file TEXT NOT NULL,
    line INTEGER NOT NULL,
    from_symbol TEXT,            -- NULL when the reference is module-level
    to_name TEXT NOT NULL,
    relationship TEXT NOT NULL,  -- call|type|base_class|member|includes|identifier
    confidence TEXT NOT NULL,    -- ast | heuristic
    receiver TEXT,               -- member-access receiver as written, else NULL
    target_symbol_id INTEGER,    -- v7: resolved target; NULL when unattributed
    resolution TEXT,             -- v7: same_file|same_project|none
    PRIMARY KEY (file, line, to_name, relationship)
)""",
    "CREATE INDEX IF NOT EXISTS idx_refs_to_name ON refs(to_name)",
    """CREATE TABLE IF NOT EXISTS index_errors (
    file TEXT NOT NULL,
    chunk_index INTEGER,
    error TEXT NOT NULL,
    at REAL NOT NULL,
    PRIMARY KEY (file, chunk_index)
)""",
    # v7: chunk text and hash, so a vectors-on pass embeds from the manifest
    # without reparsing (ADR-0001 section 3).
    """CREATE TABLE IF NOT EXISTS chunks (
    file TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    symbol TEXT,
    symbol_type TEXT,
    chunk_hash TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (file, chunk_index)
)""",
)


@dataclass
class ManifestFile:
    path: str
    content_hash: str
    size: int
    chunk_count: int
    status: str
    mtime_ns: int | None = None  # v4: stat fast-path (NULL = legacy row)
    inode: int | None = None
    loc: int | None = None       # v5: approximate line count from spans
    language: str | None = None  # v5: extension-derived language name
    parse_verdict: str | None = None  # v7: parsed|parse_error|parse_unavailable


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
    qualified_name: str = ""       # v7: container chain joined to the name
    container: str | None = None   # v7: parent qualified name, NULL at top
    language: str | None = None    # v7: copied from files.language
    decl_hash: str = ""            # v7: sha256 of the normalized signature
    symbol_id: int | None = None   # v7: revision-scoped surrogate (column id)
    # Read-only join: the file's parse verdict (parsed|parse_error|
    # parse_unavailable), carried so a row can be labelled ast|heuristic|
    # unknown without a second lookup. None for reads that do not join.
    parse_verdict: str | None = None


@dataclass
class ChunkRow:
    """One manifest chunk row: the text a vectors-on pass embeds.

    ``text`` is the raw chunk text WITHOUT the contextual path/symbol header
    — the header is composed at embed time by ``embed_text`` — so the table
    stays the source of truth for what a chunk is, not how it is embedded.
    """

    file: str
    chunk_index: int
    start_line: int
    end_line: int
    symbol: str | None
    symbol_type: str | None
    chunk_hash: str
    text: str

def _register_ordering(conn: sqlite3.Connection,
                       names: ordering.ClassNames
                       = ordering.DEFAULT_CLASS_NAMES) -> None:
    """Teach this connection the path classifier the ordering policy uses.

    A capped read orders in SQL, before ``LIMIT`` cuts the answer, so the
    ranking cannot be applied after the cap. The function is the SAME
    classifier the Python surfaces use (``ordering.class_weight``), never a
    second statement of the path rules, closed over the SAME directory names
    (``names``: the project's ladder, built-in plus its own additions), so the
    two layers cannot disagree about what a test or a sample tree is.
    Registering it per connection writes nothing to the file, so a read-only
    open keeps its contract.
    """
    conn.create_function(
        "file_class_weight", 1,
        lambda path: ordering.class_weight(path, names), deterministic=True)


def _open_read_only(db_path: str,
                    names: ordering.ClassNames
                    = ordering.DEFAULT_CLASS_NAMES) -> sqlite3.Connection:
    """A connection that cannot write, for every read path.

    ``mode=ro`` is the contract: no journal-mode write, no DDL, no migration,
    so a reader leaves the file byte-identical. SQLite cannot always serve a
    WAL database through a read-only file handle (it needs the shared-memory
    index), so the fallback is a plain connection pinned with
    ``query_only`` — the same refusal, one layer up. Nothing about the file
    is changed by either open.
    """
    uri = "file:" + quote(os.path.abspath(db_path)) + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        # A read-only WAL open can succeed at connect time and fail on the
        # first statement, so prove the file is readable before trusting it.
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        _register_ordering(conn, names)
        return conn
    except sqlite3.OperationalError as exc:
        logger.debug("read-only open unavailable for %s (%s); pinning "
                     "query_only instead", db_path, exc)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA query_only=1")
    _register_ordering(conn, names)
    return conn


def _recorded_schema(conn: sqlite3.Connection) -> int | None:
    """The schema revision the manifest records, or None when it records none."""
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.OperationalError:
        return None
    try:
        return int(row[0]) if row and row[0] is not None else None
    except (TypeError, ValueError):
        return None


class Manifest:
    """One project's manifest.

    ``writable=False`` is the read-path open: no journal mode, no DDL, no
    migration, and ``schema_version`` is what the FILE records rather than
    what this build would write. A caller that finds an older revision
    refuses rather than migrates — migrating is a write pass's job.
    """

    def __init__(self, db_path: str, *, writable: bool = True,
                 class_names: ordering.ClassNames | None = None):
        self.db_path = db_path
        self.writable = writable
        self.class_names = class_names or ordering.DEFAULT_CLASS_NAMES
        self._migrated_from: str | None = None
        if not writable:
            self._conn = _open_read_only(db_path, self.class_names)
            self.schema_version = _recorded_schema(self._conn)
            return
        self._conn = sqlite3.connect(db_path, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        _register_ordering(self._conn, self.class_names)
        with self._conn:
            # v7 replaces the symbols table instead of altering it: the old key
            # ``(file, name, start_line)`` is line-bearing, and the new columns
            # cannot be derived for rows the old extractor wrote —
            # ``qualified_name`` needs the container chain it never stored, and
            # ``decl_hash`` needs a signature a legacy row may not carry. The
            # extraction-version bump (same round) makes every existing index
            # needs-reindex, so the next ordinary pass repopulates the table
            # and no read ever answers from the empty one.
            symbol_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(symbols)")
            }
            if symbol_cols and "decl_hash" not in symbol_cols:
                self._conn.execute("DROP TABLE symbols")
            # One statement at a time: executescript() would commit the
            # transaction around it. Every statement is IF NOT EXISTS, so an
            # already-revision-7 manifest is left byte-identical here.
            for statement in _SCHEMA:
                self._conn.execute(statement)
            # v4/v5/v7 file columns. NULL on legacy rows until the pass that
            # rewrites them; readers tolerate NULL.
            file_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(files)")
            }
            for col, decl in (("mtime_ns", "INTEGER"), ("inode", "INTEGER"),
                              ("loc", "INTEGER"), ("language", "TEXT"),
                              ("parse_verdict", "TEXT")):
                if col not in file_cols:
                    self._conn.execute(
                        f"ALTER TABLE files ADD COLUMN {col} {decl}")
            # v6/v7 reference columns: the receiver as written (v6), and the
            # nullable resolved target plus its resolution channel (v7).
            # The index pass fills the v7 pair (``resolve_ref_targets``); a
            # manifest migrated here carries no targets until that pass runs,
            # and the extraction-version bump is what refuses to answer from
            # the unresolved rows in the meantime.
            ref_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(refs)")
            }
            for col, decl in (("receiver", "TEXT"),
                              ("target_symbol_id", "INTEGER"),
                              ("resolution", "TEXT")):
                if ref_cols and col not in ref_cols:
                    self._conn.execute(
                        f"ALTER TABLE refs ADD COLUMN {col} {decl}")
            self._conn.execute("DROP TABLE IF EXISTS symbol_refs")
            # schema_version must actually advance on old manifests
            # (INSERT OR IGNORE would leave v1 stuck forever).
            old_version = self.get_meta("schema_version")
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value "
                "WHERE CAST(excluded.value AS INTEGER) > CAST(value AS INTEGER)",
                (SCHEMA_VERSION,),
            )
            # The third version axis (ADR-0001 section 2): this one covers the
            # manifest DDL only. It is written on open — the migration above is
            # what a bump means — and never compared against the collection.
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES ('index_schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (SCHEMA_VERSION,),
            )
        self._migrated_from = old_version
        self.schema_version = _recorded_schema(self._conn)

    def close(self) -> None:
        self._conn.close()

    # -- files ---------------------------------------------------------

    # The dataclass order, and the column list a read selects. A manifest
    # older than this build's revision is READ, never migrated, so a column
    # the file never had comes back as NULL (its dataclass default).
    _FILE_COLUMNS = ("path", "content_hash", "size", "chunk_count", "status",
                     "mtime_ns", "inode", "loc", "language", "parse_verdict")

    def _file_select(self) -> str:
        present = {r[1] for r in self._conn.execute("PRAGMA table_info(files)")}
        return ", ".join(c if c in present else f"NULL AS {c}"
                         for c in self._FILE_COLUMNS)

    def all_files(self) -> dict[str, ManifestFile]:
        cur = self._conn.execute(f"SELECT {self._file_select()} FROM files")
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
            f"SELECT {self._file_select()} FROM files WHERE path = ?",
            (path,),
        ).fetchone()
        return ManifestFile(*row) if row else None

    def upsert_files(self, rows: list[ManifestFile]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT INTO files(path, content_hash, size, chunk_count, status, "
                "mtime_ns, inode, loc, language, parse_verdict) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET content_hash=excluded.content_hash, "
                "size=excluded.size, chunk_count=excluded.chunk_count, "
                "status=excluded.status, mtime_ns=excluded.mtime_ns, "
                "inode=excluded.inode, loc=excluded.loc, "
                "language=excluded.language, "
                "parse_verdict=excluded.parse_verdict",
                [(r.path, r.content_hash, r.size, r.chunk_count, r.status,
                  r.mtime_ns, r.inode, r.loc, r.language,
                  r.parse_verdict) for r in rows],
            )

    def delete_files(self, paths: list[str]) -> None:
        """Remove file rows AND their symbol/ref/import rows in one transaction.

        ``imports`` is purged here too. It was originally omitted, so every
        deletion left the file's import rows behind as orphans (the ``files``
        row is the authoritative "this path is indexed" record; a row in
        another table without one is residue). The orphan sweep
        (``orphan_rows``) clears what earlier deletions already left, and this
        keeps new deletions from adding to it.
        """
        with self._conn:
            self._conn.executemany(
                "DELETE FROM files WHERE path = ?", [(p,) for p in paths]
            )
            self._conn.executemany(
                "DELETE FROM symbols WHERE file = ?", [(p,) for p in paths]
            )
            self._conn.executemany(
                "DELETE FROM refs WHERE file = ?", [(p,) for p in paths]
            )
            self._conn.executemany(
                "DELETE FROM imports WHERE file = ?", [(p,) for p in paths]
            )
            self._conn.executemany(
                "DELETE FROM chunks WHERE file = ?", [(p,) for p in paths]
            )

    # -- chunks (chunk content lives here, not in the vector store) -------

    _CHUNK_COLS = ("SELECT file, chunk_index, start_line, end_line, "
                   "symbol, symbol_type, chunk_hash, text FROM chunks")

    def replace_file_chunks(self, file: str, rows: list[ChunkRow]) -> None:
        """Atomically replace a file's chunk rows (delete-then-insert).

        The same pass that writes the file's manifest row calls this, so a
        chunk removed from a file disappears here in the same transaction
        family; a removed FILE is purged by ``delete_files``.
        """
        with self._conn:
            self._conn.execute("DELETE FROM chunks WHERE file = ?", (file,))
            if not rows:
                return
            self._conn.executemany(
                "INSERT INTO chunks(file, chunk_index, start_line, end_line, "
                "symbol, symbol_type, chunk_hash, text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(r.file, r.chunk_index, r.start_line, r.end_line, r.symbol,
                  r.symbol_type, r.chunk_hash, r.text) for r in rows],
            )

    def all_chunks(self) -> list[ChunkRow]:
        """Every stored chunk, ordered by ``(file, chunk_index)`` — a total
        key, so a vectors-on pass walks the manifest deterministically."""
        return [ChunkRow(*row) for row in self._conn.execute(
            self._CHUNK_COLS + " ORDER BY file, chunk_index").fetchall()]

    def count_chunks(self) -> int:
        try:
            return int(self._conn.execute(
                "SELECT COUNT(*) FROM chunks").fetchone()[0])
        except sqlite3.OperationalError:
            return 0  # pre-revision-7 manifest records no chunk rows

    def chunk_text(self, file: str, chunk_index: int) -> str | None:
        row = self._conn.execute(
            "SELECT text FROM chunks WHERE file = ? AND chunk_index = ?",
            (file, chunk_index)).fetchone()
        return row[0] if row else None

    # -- orphan sweep ----------------------------------------------------

    _FILE_KEYED_TABLES = ("refs", "imports", "symbols", "chunks")

    def orphan_rows(self) -> dict[str, dict[str, int]]:
        """Rows in ``refs``/``imports``/``symbols`` whose ``file`` has NO row
        in ``files``, as ``{file: {table: row_count}}``.

        "No ``files`` row" and "the file is gone from the tree" are DIFFERENT
        predicates, and the schema does not tie them together, so this method
        answers only the first; the tree check is a separate guard applied by
        the caller (``Core.sweep_orphans``), which also owns the safety
        property (dry run by default + a reported count).
        """
        out: dict[str, dict[str, int]] = {}
        for table in self._FILE_KEYED_TABLES:
            rows = self._conn.execute(
                f"SELECT file, COUNT(*) FROM {table} "
                "WHERE file NOT IN (SELECT path FROM files) GROUP BY file"
            ).fetchall()
            for path, count in rows:
                out.setdefault(path, {})[table] = int(count)
        return out

    # -- symbols / refs --------------------------------------------------
    def replace_file_symbols(self, file: str, symbols: list[SymbolRow]) -> None:
        """Atomically replace a file's symbol rows.

        Delete-before-insert keyed by file: chunk ordering shifts make blind
        upserts duplicate rows. Must be called in the same pass that commits
        the file's manifest row (design: symbols never ahead of vectors).

        Identity fields are completed here so every writer gets a total key:
        ``qualified_name`` falls back to ``container.name`` (or the bare name
        at top level), ``decl_hash`` falls back to the hash of the normalized
        signature, and ``language`` stays NULL only when the caller had none.

        The surrogate ``id`` is assigned from this manifest's own counter, in
        the same transaction as the insert, and is revision-scoped: a reparse
        re-inserts every row, so an id means nothing outside the revision that
        wrote it (ADR-0002 section 2).
        """
        rows = [self._symbol_row_tuple(file, s, s.symbol_id)
                for s in symbols]
        with self._conn:
            self._conn.execute("DELETE FROM symbols WHERE file = ?", (file,))
            if not rows:
                return
            base = int(self.get_meta("symbol_id_seq") or "0")
            high = int(self._conn.execute(
                "SELECT COALESCE(MAX(id), 0) FROM symbols").fetchone()[0])
            base = max(base, high)
            self._conn.executemany(
                "INSERT OR REPLACE INTO symbols(file, qualified_name, "
                "symbol_type, decl_hash, id, name, container, language, "
                "start_line, end_line, source, signature, visibility) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(r[0], r[1], r[2], r[3], base + i, r[5], r[6], r[7],
                  r[8], r[9], r[10], r[11], r[12])
                 for i, r in enumerate(rows, 1)],
            )
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES ('symbol_id_seq', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(base + len(rows)),),
            )

    @staticmethod
    def _symbol_row_tuple(file: str, s: SymbolRow,
                          symbol_id: int | None) -> tuple:
        """One insert tuple, identity fields completed and in column order."""
        from .chunker import declaration_hash
        qualified = s.qualified_name or (
            f"{s.container}.{s.name}" if s.container else s.name)
        if s.decl_hash:
            decl = s.decl_hash
        elif s.signature:
            decl = declaration_hash(s.signature)
        else:
            # No declaration text at all to hash (extraction always supplies
            # a signature, so no pass reaches this). The basis must still be
            # LINE-FREE — the identity's promise — so it is the name and the
            # kind, never the observed range: two hand-written rows of one
            # name and kind in a file therefore share an identity and one of
            # them is absorbed, which is the honest cost of a line-free key.
            decl = declaration_hash(f"{s.name} ({s.symbol_type})")
        return (file, qualified, s.symbol_type, decl, symbol_id, s.name,
                s.container, s.language, s.start_line, s.end_line, s.source,
                s.signature, s.visibility)

    # ONE column list serves EVERY symbol surface, and the file's parse
    # verdict is joined in, so each row carries the evidence it can honestly
    # be labelled with (`identity.evidence_of`). A second column list without
    # that join would answer a different evidence question about the SAME row
    # — `unknown` on one surface and `ast` on another (ADR-0002 section 4).
    _SYMBOL_SELECT = (
        "SELECT s.file, s.name, s.symbol_type, s.start_line, s.end_line, "
        "s.source, s.signature, s.visibility, s.qualified_name, s.container, "
        "s.language, s.decl_hash, s.id, f.parse_verdict "
        "FROM symbols s LEFT JOIN files f ON f.path = s.file")

    def index_revision(self) -> str | None:
        """The revision these rows were written in (``meta.index_revision``).

        Printed beside every ``symbol_id`` so a consumer can tell that the id
        belongs to that revision and to no other (ADR-0002 section 2).
        """
        return self.get_meta("index_revision")

    def parse_verdict_counts(self) -> dict[str, int]:
        """Files per parse verdict: parsed, parse_error, parse_unavailable.

        Always all three keys, zero when absent — a count that is missing from
        the answer would read as "no such files" (ADR-0004 section 7).
        """
        out = {"parsed": 0, "parse_error": 0, "parse_unavailable": 0}
        try:
            rows = self._conn.execute(
                "SELECT parse_verdict, COUNT(*) FROM files GROUP BY 1").fetchall()
        except sqlite3.OperationalError:
            # A manifest older than this build's revision records no verdict:
            # three zeros is the honest answer, not a crash on a read.
            return out
        for verdict, n in rows:
            if verdict in out:
                out[verdict] = int(n)
        return out

    # The ordering policy (``code_indexer.ordering``): file class first, so a
    # definition in a test fixture, a shipped sample or a document never
    # outranks the source it exercises; then identity quality -- a row with a
    # recorded kind before one without, attributed syntax before a textual
    # row -- then the row's own identity fields so no tie falls to insertion
    # order. ``file_class_weight`` is the Python classifier registered per
    # connection, so this capped read ranks by the SAME rules every other
    # surface uses, over the SAME project ladder.
    #
    # There is no exact-name key here, and there cannot be one: the exact tier
    # and the substring tier are two QUERIES (`find_symbols(..., substring=)`),
    # and the substring tier runs only when the exact tier is empty, so no
    # single answer holds both an exact and a fuzzy row for a key to separate.
    _SYMBOL_ORDER = (
        " ORDER BY file_class_weight(s.file), "
        "CASE WHEN s.symbol_type IS NULL OR s.symbol_type = '' "
        "THEN 1 ELSE 0 END, "
        "CASE WHEN s.source = 'ast' THEN 0 ELSE 1 END, "
        "s.name, s.file, s.start_line, s.qualified_name, s.decl_hash")

    def _symbol_filters(self, name: str | None = None,
                        symbol_type: str | None = None,
                        substring: bool = False, file: str | None = None,
                        name_pattern: str | None = None,
                        container: str | None = None,
                        visibility: str | None = None,
                        language: str | None = None,
                        inherits: str | None = None,
                        has_call: str | None = None
                        ) -> tuple[list[str], list]:
        """The WHERE clauses every symbol read shares; filters compose (AND).

        ``name_pattern`` is a GLOB over the name and stays case-sensitive
        (shell semantics). No index was added for it: the existing name index
        answers a PREFIX pattern as a range seek, while a leading wildcard
        cannot be a range and is a scan of ``symbols`` (``SCAN s``) — cheap
        at this size, and measured rather than assumed. The planner plan is
        asserted in the tests (``symbol_query_plan``).

        ``inherits`` and ``has_call`` are answered from ATTRIBUTED syntax
        only — a ``base_class`` / ``call`` row whose ``from_symbol`` is this
        symbol's own name in this symbol's own file. A file with no AST
        extractor holds no such row, so an empty answer there means "not
        attributed", never "no": the caller labels that with
        ``identity.evidence_of`` and must not render it as a negative.

        That join is NAME-LEVEL and knowingly so: ``refs.from_symbol``
        carries no container, so two same-named symbols in one file are both
        credited with the union of both bodies' attributed references. There
        is no column to join on that would narrow it; reference resolution to
        a container is the staged work the identity model's section 3
        schedules, and no read here pretends to have it.
        """
        clauses: list[str] = []
        args: list = []
        if name:
            op = "LIKE" if substring else "="
            pattern = f"%{name}%" if substring else name
            clauses.append(f"s.name {op} ? COLLATE NOCASE")
            args.append(pattern)
        if name_pattern:
            clauses.append("s.name GLOB ?")
            args.append(name_pattern)
        if symbol_type:
            clauses.append("s.symbol_type = ?")
            args.append(symbol_type)
        if file:
            clauses.append("s.file = ?")
            args.append(file)
        if container:
            clauses.append("s.container = ?")
            args.append(container)
        if visibility == "public":
            # NULL visibility is the documented spelling of public.
            clauses.append("(s.visibility = 'public' OR s.visibility IS NULL)")
        elif visibility:
            clauses.append("s.visibility = ?")
            args.append(visibility)
        if language:
            clauses.append("s.language = ? COLLATE NOCASE")
            args.append(language)
        for relationship, target in (("base_class", inherits),
                                     ("call", has_call)):
            if not target:
                continue
            clauses.append(
                "EXISTS (SELECT 1 FROM refs r WHERE r.relationship = ?"
                " AND r.file = s.file AND r.from_symbol = s.name"
                " AND r.to_name = ? COLLATE NOCASE)")
            args.extend([relationship, target])
        return clauses, args

    def find_symbols(self, name: str | None, symbol_type: str | None = None,
                     substring: bool = False, limit: int | None = 25,
                     file: str | None = None, *,
                     name_pattern: str | None = None,
                     container: str | None = None,
                     visibility: str | None = None,
                     language: str | None = None,
                     inherits: str | None = None,
                     has_call: str | None = None) -> list[SymbolRow]:
        """Exact-name read in the ordering policy; optional capped substring tier.

        name=None is browse mode (design §2.3): filters only, capped by
        ``limit``. ``limit=None`` is the WHOLE read — a definition list is an
        answer, not a sample, so ``definitions_of`` uses it and the caller
        above owns whatever cap it applies (issue #22).

        Every structural filter composes with the name/file filters as AND.
        A capped read is never a claim about the population: ``count_symbols``
        answers that from the SAME clause set, so the two cannot drift.

        The order is ``code_indexer.ordering``'s policy, applied in SQL so it
        holds BEFORE ``limit`` cuts the answer: file class (source, examples,
        tests, docs, generated), then a recorded kind, then attributed syntax,
        then the row's own identity fields (``name``, ``file``,
        ``start_line``, ``qualified_name``, ``decl_hash``) — a total key, so a
        capped read is a reproducible prefix and no tie group can straddle the
        cut. An exact-name read carries no name key of its own: the exact and
        substring tiers are two queries, so one answer never holds both.
        """
        clauses, args = self._symbol_filters(
            name=name, symbol_type=symbol_type, substring=substring,
            file=file, name_pattern=name_pattern, container=container,
            visibility=visibility, language=language, inherits=inherits,
            has_call=has_call)
        sql = self._SYMBOL_SELECT
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += self._SYMBOL_ORDER
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return [SymbolRow(*row) for row in
                self._conn.execute(sql, args).fetchall()]

    def count_symbols(self, name: str | None, symbol_type: str | None = None,
                      substring: bool = False, file: str | None = None, *,
                      name_pattern: str | None = None,
                      container: str | None = None,
                      visibility: str | None = None,
                      language: str | None = None,
                      inherits: str | None = None,
                      has_call: str | None = None) -> int:
        """How many rows this filter set matches, uncapped.

        The honest companion of a capped read: the CLI reports it as
        ``match_count``, so a truncated answer names the population it was
        drawn from instead of looking complete.
        """
        clauses, args = self._symbol_filters(
            name=name, symbol_type=symbol_type, substring=substring,
            file=file, name_pattern=name_pattern, container=container,
            visibility=visibility, language=language, inherits=inherits,
            has_call=has_call)
        sql = "SELECT count(*) FROM symbols s"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return int(self._conn.execute(sql, args).fetchone()[0])

    def symbol_query_plan(self, name: str | None = None, *,
                          name_pattern: str | None = None,
                          file: str | None = None,
                          container: str | None = None,
                          language: str | None = None,
                          symbol_type: str | None = None,
                          visibility: str | None = None,
                          inherits: str | None = None,
                          has_call: str | None = None) -> list[str]:
        """SQLite's plan for the filter read (diagnostics and tests only).

        Exists so the claim "the name index answers a name pattern" is
        checked against the planner rather than asserted in a comment.
        """
        clauses, args = self._symbol_filters(
            name=name, symbol_type=symbol_type, file=file,
            name_pattern=name_pattern, container=container,
            visibility=visibility, language=language, inherits=inherits,
            has_call=has_call)
        sql = self._SYMBOL_SELECT
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += self._SYMBOL_ORDER
        return [str(row[3]) for row in
                self._conn.execute("EXPLAIN QUERY PLAN " + sql,
                                   args).fetchall()]

    def symbol_files_without_ast(self) -> int:
        """How many files holding symbol rows have no AST extraction.

        The count behind an honest empty answer: those files' references are
        unattributed, so "no attributed call" cannot be read as "no call".
        """
        row = self._conn.execute(
            "SELECT count(*) FROM (SELECT DISTINCT s.file FROM symbols s "
            "LEFT JOIN files f ON f.path = s.file WHERE f.parse_verdict "
            "IS NULL OR f.parse_verdict <> 'parsed')").fetchone()
        return int(row[0])

    def definitions_of(self, name: str,
                       symbol_type: str | None = None) -> list[SymbolRow]:
        """EVERY definition row of this exact name — never a capped sample.

        This is the read behind a graph walk's definition roots, the
        resolution of a dotted subject's qualifier, and ``_definer_of``.
        A name can have many definitions (40 ``__init__`` rows on one live
        index), and a walk handed 25 of them walks 25 subtrees while
        certifying the answer as complete — issue #22's filed symptom. The
        graph layer applies and announces its own node cap on the returned
        roots; the store does not decide how many definitions exist.
        """
        return self.find_symbols(name, symbol_type=symbol_type,
                                 substring=False, limit=None)

    def symbols_with_files(self) -> tuple[dict[str, ManifestFile], list[SymbolRow]]:
        """All files + all symbols for the skeleton projection (design §2.1).

        Both from one connection: under WAL a concurrent writer can't tear
        the pair apart (each replace* is atomic per file, and each SELECT is
        a point-in-time snapshot).
        """
        files = self.all_files()
        syms = [SymbolRow(*row) for row in self._conn.execute(
            self._SYMBOL_SELECT + " ORDER BY s.file, s.start_line").fetchall()]
        return files, syms

    def symbols_for_file(self, file: str) -> list[SymbolRow]:
        return [SymbolRow(*row) for row in self._conn.execute(
            self._SYMBOL_SELECT +
            " WHERE s.file = ? ORDER BY s.start_line", (file,)
        ).fetchall()]

    def all_symbol_names(self) -> set[str]:
        return {r[0] for r in self._conn.execute("SELECT DISTINCT name FROM symbols")}

    # -- the single reference store (v6) --------------------------------

    _REF_COLS = ("SELECT file, line, from_symbol, to_name, relationship, "
                 "confidence, receiver FROM refs")

    def refs_to(self, name: str,
                relationships: tuple[str, ...] | None = None,
                limit: int = 25) -> list[dict]:
        """Reference rows TARGETING ``name`` as dictionaries, optionally
        filtered to a set of stored relationships.

        Ordered by the policy (``code_indexer.ordering``), applied in SQL so
        it holds BEFORE ``limit`` cuts the answer: file class (source,
        examples, tests, docs, generated), then identity quality -- a
        reference inside a NAMED enclosing symbol before a module-level
        mention, which is the one identity fact that varies across the rows of
        one name -- then attributed syntax before a textual row, then
        ``(file, line, relationship, from_symbol, receiver)``, which is total,
        so a capped read is a reproducible prefix and no tie group can
        straddle the cut.

        The name takes no key, and no case policy: ``to_name = ?`` already
        pins it exactly, so every row compares equal on it.
        """
        sql = self._REF_COLS + " WHERE to_name = ?"
        args: list = [name]
        if relationships:
            sql += (" AND relationship IN ("
                    + ",".join("?" * len(relationships)) + ")")
            args.extend(relationships)
        sql += (" ORDER BY file_class_weight(file),"
                " CASE WHEN from_symbol IS NOT NULL AND from_symbol <> ''"
                " THEN 0 ELSE 1 END,"
                " CASE WHEN confidence = 'ast' THEN 0 ELSE 1 END,"
                " file, line, relationship, from_symbol, receiver LIMIT ?")
        args.append(limit)
        keys = ("file", "line", "from_symbol", "to_name", "relationship",
                "confidence", "receiver")
        return [dict(zip(keys, r))
                for r in self._conn.execute(sql, args).fetchall()]

    def ref_relationship_counts(self, name: str) -> list[tuple[str, int]]:
        """(stored relationship, count) for every row targeting ``name``,
        unfiltered — ONE aggregate query, used to tell a filtered-empty
        report apart from a genuinely absent name."""
        return [(r[0], int(r[1])) for r in self._conn.execute(
            "SELECT relationship, COUNT(*) FROM refs WHERE to_name = ?"
            " GROUP BY relationship"
            " ORDER BY COUNT(*) DESC, relationship", (name,))]

    # -- meta ----------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        if not self.writable:
            raise RuntimeError(
                f"manifest {self.db_path} was opened read-only; "
                f"refusing to write meta key {key!r}")
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
                          rows: list[tuple[int | None, str | None, str, str,
                                           str | None]],
                          confidence: str) -> None:
        """Replace a file's reference rows — the ONE reference writer.

        Rows are (line, from_symbol, to_name, relationship, receiver).
        ``confidence`` is stamped here for the whole file: 'ast' when the rows
        came from a tree-sitter query, 'heuristic' for the textual fallback.
        """
        cur = self._conn.cursor()
        cur.execute("DELETE FROM refs WHERE file = ?", (file,))
        cur.executemany(
            "INSERT OR REPLACE INTO refs(file, line, from_symbol, to_name,"
            " relationship, confidence, receiver)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(file, ln, frm, to, rel, confidence, recv)
             for (ln, frm, to, rel, recv) in rows])
        self._conn.commit()

    def resolve_ref_targets(self, file: str | None = None) -> int:
        """Attribute reference rows to the definitions they resolve to.

        The ONE writer of ``refs.target_symbol_id`` / ``refs.resolution``, run
        by the index pass once every file's symbol and reference rows are in
        the store (and by any test that builds a manifest by hand). One rule,
        applied to every row -- or to one file's rows when ``file`` is given:

        1. ``same_file`` -- the referencing file defines the name exactly
           once; that definition is the target. A local definition is what a
           reference in the same file means, whatever else shares the name.
        2. ``same_project`` -- the store defines the name exactly once, or
           defines it several times with the project's own file-class ladder
           strictly preferring one of them (source above examples, tests,
           documents and artifacts -- the ladder ``overview`` ranks hotspots
           by). A test module that mirrors a source name is therefore not
           where a reference outside it points.
        3. ``none`` -- anything still tied. The target stays NULL and the row
           is reported as unattributed by ``unattributed_ref_count``; taking
           one of several equals would be the old first-file guess wearing a
           new column. Two definitions of one name in the same class, or two
           in the same file, are a tie and stay unattributed.

        Rows whose target does not change are not rewritten, so a second call
        is a no-op. Returns the number of rows rewritten.
        """
        definitions: dict[str, list[tuple[str, int]]] = {}
        for name, path, symbol_id in self._conn.execute(
                "SELECT name, file, id FROM symbols"):
            definitions.setdefault(name, []).append((path, symbol_id))

        # The project-wide choice does not depend on which file is asking, so
        # it is computed once per name and reused by every referencing row.
        project_choice: dict[str, tuple[int | None, str]] = {}

        def choose(name: str) -> tuple[int | None, str]:
            cached = project_choice.get(name)
            if cached is not None:
                return cached
            candidates = definitions.get(name) or []
            if not candidates:
                choice: tuple[int | None, str] = (None, "none")
            elif len(candidates) == 1:
                choice = (candidates[0][1], "same_project")
            else:
                best = min(ordering.class_weight(path, self.class_names)
                           for path, _sid in candidates)
                preferred = [sid for path, sid in candidates
                             if ordering.class_weight(path, self.class_names)
                             == best]
                choice = ((preferred[0], "same_project")
                          if len(preferred) == 1 else (None, "none"))
            project_choice[name] = choice
            return choice

        def resolve(ref_file: str, name: str) -> tuple[int | None, str]:
            local = [sid for path, sid in (definitions.get(name) or [])
                     if path == ref_file]
            if len(local) == 1:
                return local[0], "same_file"
            return choose(name)

        sql = ("SELECT rowid, file, to_name, target_symbol_id, resolution"
               " FROM refs")
        args: list = []
        if file is not None:
            sql += " WHERE file = ?"
            args.append(file)
        updates = [
            (target, resolution, rowid)
            for rowid, ref_file, name, old_target, old_resolution
            in self._conn.execute(sql, args)
            for target, resolution in [resolve(ref_file, name)]
            if (target, resolution) != (old_target, old_resolution)]
        if updates:
            self._conn.executemany(
                "UPDATE refs SET target_symbol_id = ?, resolution = ?"
                " WHERE rowid = ?", updates)
            self._conn.commit()
        return len(updates)

    def unattributed_ref_counts(self, scope_prefix: str | None = None
                                ) -> dict[str, int]:
        """Reference rows no rule attributed, split by WHY they were not.

        Every row here has ``resolution = 'none'`` and is credited to NO
        file's fan-in. Two different facts produce that state, and one
        number cannot report both:

        * ``unresolved`` -- the store defines the NAME nowhere. There is no
          definition to point at: a builtin, an external or deleted symbol,
          or a word the heuristic extractor read as an identifier.
        * ``ambiguous`` -- the store defines the name more than once and no
          same-file definition settles it, so the project-wide choice is a
          tie; picking one would be the old first-definer guess.

        ``unattributed`` is their sum, and is what the field of that name
        has always reported, so the total keeps meaning what it meant.
        ``scope_prefix`` counts the rows that SIT in the prefix -- the file
        carrying the reference -- because the scope names the files being
        reported on.
        """
        sql = ("SELECT COUNT(*),"
               " SUM(CASE WHEN EXISTS (SELECT 1 FROM symbols s"
               " WHERE s.name = r.to_name) THEN 0 ELSE 1 END)"
               " FROM refs r WHERE r.resolution = 'none'")
        args: list = []
        if scope_prefix:
            sql += " AND substr(r.file, 1, ?) = ?"
            args += [len(scope_prefix), scope_prefix]
        row = self._conn.execute(sql, args).fetchone()
        total = int(row[0])
        unresolved = int(row[1] or 0)
        return {"unattributed": total, "unresolved": unresolved,
                "ambiguous": total - unresolved}

    def unattributed_ref_count(self, scope_prefix: str | None = None) -> int:
        """Reference rows the resolver could not attribute, counted in place.

        A row is unattributed when ``resolution`` is ``none``: the name is
        defined nowhere in the store, or its definitions tie. It is credited
        to NO file's fan-in, and this count is what keeps the omission
        visible instead of silent. ``unattributed_ref_counts`` reports the
        same total split into the two reasons. ``scope_prefix`` counts the
        rows that SIT in the prefix -- the file carrying the reference --
        because the scope names the files being reported on.
        """
        return self.unattributed_ref_counts(scope_prefix)["unattributed"]

    def callers_of(self, name: str, limit: int | None = None) -> list[dict]:
        """Fan-in edges: every stored row that references ``name``.

        ``limit=None`` — the default — is the WHOLE store read. The store hands
        over everything it holds; the graph layer above owns and announces its
        own node cap, and it cannot be honest about the population while a cap
        down here hands it a short list with no trace (issue #22). Pass an
        explicit ``limit`` only when the caller wants a bounded read and says
        so in its own output.

        The order key ``(file, line, relationship, to_name, receiver)`` is
        total — its first four columns are the table's primary-key column set
        (``(file, line, to_name, relationship)``, a different order) and the
        fifth is the receiver — so a capped read is a reproducible prefix of
        the full read and no tie group can straddle the cut. ``(file, line)``
        alone was not: a live index has 73,911 lines carrying more than one
        reference.
        """
        sql = ("SELECT file, line, from_symbol, relationship, confidence,"
               " receiver FROM refs WHERE to_name = ?"
               " ORDER BY file, line, relationship, to_name, receiver")
        args: list = [name]
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return [dict(zip(("file", "line", "from_symbol", "relationship",
                          "confidence", "receiver"), r))
                for r in self._conn.execute(sql, args)]

    def callees_of_file_symbol(self, file: str, symbol: str | None,
                               limit: int | None = None) -> list[dict]:
        """Fan-out edges originating from one symbol (or the whole file when
        symbol is None). ``limit=None`` is the whole store read (issue #22)."""
        if symbol is None:
            sql = ("SELECT line, to_name, relationship, confidence, receiver"
                   " FROM refs WHERE file = ? ORDER BY line")
            args: list = [file]
        else:
            sql = ("SELECT line, to_name, relationship, confidence, receiver"
                   " FROM refs WHERE file = ? AND from_symbol = ?"
                   " ORDER BY line")
            args = [file, symbol]
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        rows = self._conn.execute(sql, args).fetchall()
        keys = ("line", "to_name", "relationship", "confidence", "receiver")
        return [dict(zip(keys, r)) for r in rows]

    def file_fan_in(self, scope_prefix: str | None = None
                    ) -> list[tuple[str, int]]:
        """Reference fan-in per target FILE, computed by the store.

        Two complete aggregates, never a row sample: resolved imports INTO a
        file, plus references attributed to a definition that file holds (a
        reference from the file itself is not fan-in).

        The name half reads the RESOLVED target (``refs.target_symbol_id``,
        written by ``resolve_ref_targets``), so a reference credits the
        definition it was resolved to. A test module that sorts before the
        source owning the same name no longer takes the source's number, and
        a name whose definitions tie shares nothing, because no definition is
        chosen for it. A reference no rule attributes is credited to NO file:
        ``unattributed_ref_count`` reports that remainder beside the
        hotspots, so the number never reads as a complete tally of such a
        name.

        Pass ``scope_prefix`` (what ``overview --path-prefix`` reports) and
        the aggregate reports only targets in those files, imports scoped by
        their resolved file the same way. Sorted by count desc, then file.
        """
        counts: dict[str, int] = {}
        imports_sql = ("SELECT resolved_file, COUNT(*) FROM imports"
                       " WHERE resolved_file IS NOT NULL"
                       " AND resolved_file != file")
        imports_args: list = []
        targets_sql = ("SELECT s.file, COUNT(*) FROM refs r JOIN symbols s"
                       " ON s.id = r.target_symbol_id"
                       " WHERE r.file != s.file")
        targets_args: list = []
        if scope_prefix:
            # Exact prefix match: LIKE is case-insensitive for ASCII and
            # would scope by a different rule than the caller used.
            imports_sql += " AND substr(resolved_file, 1, ?) = ?"
            imports_args += [len(scope_prefix), scope_prefix]
            targets_sql += " AND substr(s.file, 1, ?) = ?"
            targets_args += [len(scope_prefix), scope_prefix]
        imports_sql += " GROUP BY resolved_file"
        targets_sql += " GROUP BY s.file"
        for target, n in self._conn.execute(imports_sql, imports_args):
            counts[target] = counts.get(target, 0) + int(n)
        for target, n in self._conn.execute(targets_sql, targets_args):
            counts[target] = counts.get(target, 0) + int(n)
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def import_rows(self, limit: int | None = None) -> list[dict]:
        """Every stored import row, ordered by ``(file, line, raw_target)`` —
        a total key, because the table's primary key is ``(file, raw_target,
        line)`` and two rows can share one ``(file, line)``. So a capped read
        is a reproducible prefix of the full read: no tie group can straddle
        the cut. ``limit=None`` (the default) is the whole store read (issue
        #22)."""
        sql = ("SELECT file, raw_target, resolved_file, kind, line"
               " FROM imports ORDER BY file, line, raw_target")
        args: list = []
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return [dict(zip(("file", "raw_target", "resolved_file", "kind",
                          "line"), r))
                for r in self._conn.execute(sql, args)]

    def all_symbols(self) -> list[SymbolRow]:
        cur = self._conn.execute(
            self._SYMBOL_SELECT + " ORDER BY s.file, s.start_line")
        return [SymbolRow(*row) for row in cur.fetchall()]

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
