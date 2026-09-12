"""Chunker seam: chunk(text, path) -> list[Chunk].

Per design §5: fallback line/regex chunker ships first; tree-sitter adapter
is a drop-in behind the same seam (optional milestone M4). Chunk size cap
~1000 chars with 10-line overlap windows.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

CHUNK_CHAR_CAP = 1000
WINDOW_LINES = 80
OVERLAP_LINES = 10

_SYMBOL_PATTERNS = [
    re.compile(r"^\s*(async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\s*(async\s+)?function\s+([A-Za-z_][A-Za-z0-9_$]*)"),
    re.compile(r"^\s*(pub\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\s*(public|private|protected|internal|static|final|abstract|override|\s)*\s*(?:[\w<>\[\],\s]+?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
]


@dataclass
class Chunk:
    text: str
    chunk_hash: str   # sha256 of the chunk text itself
    symbol: str | None
    start_line: int   # 1-based
    end_line: int
    chunk_index: int


def _guess_symbol(line: str) -> str | None:
    for pat in _SYMBOL_PATTERNS:
        m = pat.match(line)
        if m:
            return m.group(2) or m.group(1)
    return None


def _make_chunk(lines: list[str], start: int, index: int) -> Chunk:
    text = "\n".join(lines)
    if len(text) > CHUNK_CHAR_CAP:
        # Split oversized blocks on a line boundary near the cap.
        parts: list[str] = []
        cur: list[str] = []
        cur_len = 0
        for ln in lines:
            if cur_len + len(ln) + 1 > CHUNK_CHAR_CAP and cur:
                parts.append("\n".join(cur))
                cur, cur_len = [], 0
            cur.append(ln)
            cur_len += len(ln) + 1
        if cur:
            parts.append("\n".join(cur))
        # Only the first part is returned here; callers use _split_block.
        return _chunks_from_parts(parts, start, index)[0]

    symbol = None
    for ln in lines:
        symbol = _guess_symbol(ln)
        if symbol:
            break
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Chunk(
        text=text, chunk_hash="sha256:" + h, symbol=symbol,
        start_line=start, end_line=start + len(lines) - 1, chunk_index=index,
    )


def _chunks_from_parts(parts: list[str], start: int, index: int) -> list[Chunk]:
    """Rebuild chunk metadata after an oversized block was split."""
    out: list[Chunk] = []
    line_no = start
    for part in parts:
        n = part.count("\n") + 1
        symbol = None
        for ln in part.splitlines():
            symbol = _guess_symbol(ln)
            if symbol:
                break
        h = hashlib.sha256(part.encode("utf-8")).hexdigest()
        out.append(Chunk(part, "sha256:" + h, symbol, line_no, line_no + n - 1, index + len(out)))
        line_no += n
    return out


def chunk(text: str) -> list[Chunk]:
    """Fallback chunker: blank-line/definition-boundary windows with overlap.

    ~80-line windows, 10-line overlap; oversized blocks split at ~1000 chars.
    """
    lines = text.splitlines()
    if not lines:
        return []
    if len(text) <= CHUNK_CHAR_CAP:
        return [_make_chunk(lines, 1, 0)]

    chunks: list[Chunk] = []
    idx = 0
    pos = 0
    while pos < len(lines):
        window = lines[pos:pos + WINDOW_LINES]
        made = _make_chunk(window, pos + 1, idx)
        if len(made.text) > CHUNK_CHAR_CAP:
            for c in _chunks_from_parts(_split_text(made.text), pos + 1, idx):
                chunks.append(c)
                idx += 1
        else:
            chunks.append(made)
            idx += 1
        if pos + WINDOW_LINES >= len(lines):
            break
        pos += WINDOW_LINES - OVERLAP_LINES
    return chunks


def _split_text(text: str) -> list[str]:
    parts: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for ln in text.splitlines():
        if cur_len + len(ln) + 1 > CHUNK_CHAR_CAP and cur:
            parts.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(ln)
        cur_len += len(ln) + 1
    if cur:
        parts.append("\n".join(cur))
    return parts