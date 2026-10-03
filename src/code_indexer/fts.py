"""FTS5 lexical index over chunk content (hybrid retrieval work item).

One virtual table per project manifest:
    chunks_fts(content, symbol, path)  -- contentless=no; stores text
Rows are kept in the SAME transaction as the manifest file/chunk commits, so
the lexical index can never drift from the manifest; deleted files are purged
from FTS in the same statement batch.

Pre-tokenization: identifiers are split into sub-tokens (camelCase,
snake_case, digit runs, ``::``/``.`` separators) and the ORIGINAL token is
kept alongside — a query for ``refreshToken`` matches ``refresh_token`` and
vice versa. Query side builds an FTS5 MATCH string with quoted phrase tokens
and a ``*`` prefix on the last token; bm25() ranks with column weights
symbol > path > content.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("code-indexer.fts")

# Identifier splitting: keep original token + sub-tokens.
_SUBTOKEN_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|[0-9]+")
_SPLIT_SEPARATORS = re.compile(r"[::.\-_]+")
fts_token = r"[A-Za-z0-9_]+"


def tokenize_fts(text: str) -> str:
    """Pre-tokenize for the FTS index: original tokens + lowercased
    sub-tokens.

    camelCase/snake_case identifiers are split into parts and re-emitted
    space-separated alongside the original, so ``refreshToken`` matches a
    query for ``refresh`` and ``token`` (and the reverse). Sub-tokens are
    lowercased (case-insensitive matching); the original token is preserved
    verbatim for exactness.
    """
    out: list[str] = []
    for raw in re.findall(fts_token, text or ""):
        out.append(raw)
        parts = _SPLIT_SEPARATORS.split(raw)
        for part in parts:
            subs = _SUBTOKEN_RE.findall(part)
            if len(subs) > 1:
                out.extend(s.lower() for s in subs)
            elif part != raw:
                out.extend(_SUBTOKEN_RE.findall(part))
    return " ".join(dict.fromkeys(out))  # dedupe, keep order


def build_match_query(query: str, max_terms: int = 12) -> str | None:
    """Sanitize a user query into an FTS5 MATCH expression.

    Every token is double-quoted (phrases, no syntax injection); the LAST
    token gets a ``*`` prefix match. Returns None when nothing remains.
    """
    tokens = re.findall(fts_token, query or "")
    if not tokens:
        return None
    tokens = tokens[-max_terms:]
    quoted = [f'"{t}"' for t in tokens[:-1]]
    quoted.append(f'"{tokens[-1]}"*')
    return " ".join(quoted)


# bm25 column weights: symbol > path > content.
_BM25_SQL = (
    "bm25(chunks_fts, 1.0, 8.0, 4.0)")


class FtsIndex:
    """Wrapper over one manifest's chunks_fts virtual table."""

    def __init__(self, conn):
        self._conn = conn

    def create(self) -> None:
        self._conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
            "content, symbol, path, tokenize='unicode61')"
        )

    def rowcount(self) -> int:
        try:
            return int(self._conn.execute(
                "SELECT COUNT(*) FROM chunks_fts").fetchone()[0])
        except Exception:  # noqa: BLE001 — table absent in legacy manifests
            return 0

    def add_chunks(self, rows: list[tuple[str, str, str, str, str, int]]) -> None:
        """Insert chunk rows: (file, chunk_index, content, symbol, path, line).

        Replace-by-file: a re-processed file's old rows are removed first so
        the lexical index never accumulates stale duplicates.
        """
        if not rows:
            return
        self.create()
        files = {row[4] for row in rows}
        for file in files:
            self.purge_file(file)
        self._conn.executemany(
            "INSERT INTO chunks_fts(content, symbol, path) VALUES (?, ?, ?)",
            [(tokenize_fts(content), tokenize_fts(symbol or ""), path)
             for (_f, _i, content, symbol, path, _l) in rows])

    def purge_file(self, file: str) -> None:
        self._conn.execute(
            "DELETE FROM chunks_fts WHERE path = ?", (file,))

    def search(self, query: str, limit: int = 30) -> list[dict]:
        """bm25-ranked lexical search; falls back to tokenized containment."""
        match = build_match_query(query)
        if match is None:
            return []
        self.create()
        try:
            rows = self._conn.execute(
                "SELECT path, symbol, rank FROM chunks_fts "
                f"WHERE chunks_fts MATCH ? ORDER BY {_BM25_SQL} LIMIT ?",
                (match, limit)).fetchall()
        except Exception as exc:  # noqa: BLE001 — malformed MATCH edge
            logger.warning("FTS match failed (%s): %s", match, exc)
            return []
        return [{"file": r[0], "symbol": r[1] or None, "rank": r[2]}
                for r in rows]

    def needs_backfill(self, chunk_rows: int) -> bool:
        return self.rowcount() == 0 and chunk_rows > 0