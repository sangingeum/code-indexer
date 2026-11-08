"""Chunker seam: chunk(text, path) -> list[Chunk].

Per design §5: fallback line/regex chunker ships first; tree-sitter adapter
is a drop-in behind the same seam (optional milestone M4). Chunk size cap
~1000 chars with 10-line overlap windows.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass

CHUNK_CHAR_CAP = 1000
WINDOW_LINES = 80
OVERLAP_LINES = 10

# Real line terminators, and only these. ``str.splitlines`` also breaks on
# form feed, vertical tab, \x1c-\x1e, U+2028 and U+2029, which are characters
# INSIDE a line for every editor and every other tool that counts lines: a
# split there invents a line, so a reported range overshoots the file and
# ``get-code-context --start-line`` reads the wrong text.
_LINE_BREAK = re.compile(r"\r\n|\n|\r")


def split_lines(text: str) -> list[str]:
    """Split on real line terminators only (CRLF, LF, a lone CR).

    A trailing terminator does not produce an empty final line, so the count
    is the number of lines an editor shows for the same file. ``wc -l``
    counts terminators and therefore differs on the last line: the file
    ``printf 'a\\nb'`` writes is two lines here and one to ``wc -l``.
    """
    if not text:
        return []
    parts = _LINE_BREAK.split(text)
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def chunk_max_chars() -> int:
    """CHUNK_MAX_CHARS override (default stays 1000; 1500/2000 are eval
    candidates, not defaults)."""
    try:
        return max(200, int(os.environ.get("CHUNK_MAX_CHARS", CHUNK_CHAR_CAP)))
    except ValueError:
        return CHUNK_CHAR_CAP

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
    source: str = "regex"      # 'ast' | 'regex' — which chunker produced it
    symbol_type: str | None = None  # function|method|class|struct|enum|namespace
    signature: str | None = None    # v3: decl text up to body (best-effort)
    node_type: str | None = None    # v3: tree-sitter node type (visibility)
    container: str | None = None    # v7: qualified name of the enclosing unit


def declaration_hash(signature: str | None, first_line: str | None = None) -> str:
    """sha256 of a declaration's normalized signature (schema v7 identity).

    Normalization is whitespace-collapse: the signature is compared as text,
    so a definition shifted by a line keeps its hash while a reformat that
    changes the declaration changes it. Where the chunker produced no
    signature, the declaration's observed FIRST LINE is hashed instead, so
    the key stays total for every row (ADR-0002 section 1).

    The ``sha256:`` prefix is the same spelling chunk hashes use, so one hash
    vocabulary covers the manifest.
    """
    basis = signature if signature else (first_line or "")
    normalized = " ".join(basis.split())
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _guess_symbol(line: str) -> str | None:
    for pat in _SYMBOL_PATTERNS:
        m = pat.match(line)
        if m:
            # Different patterns name the symbol in different groups;
            # pick the last participating group (regexes list the
            # symbol name last). group(2) unconditionally raised
            # IndexError("no such group") on single-group patterns.
            return next((g for g in reversed(m.groups()) if g is not None), None)
    return None


def _make_chunk(lines: list[str], start: int, index: int) -> Chunk:
    """One chunk for these lines.

    Never truncates: an oversized window is the caller's business, and the
    caller's split branch is the only place a window is divided. While this
    function truncated, ``chunk``'s own split branch could never run and most
    of a long-line document silently never reached a chunk.
    """
    text = "\n".join(lines)
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
        for ln in split_lines(part):
            symbol = _guess_symbol(ln)
            if symbol:
                break
        h = hashlib.sha256(part.encode("utf-8")).hexdigest()
        out.append(Chunk(part, "sha256:" + h, symbol, line_no, line_no + n - 1, index + len(out)))
        line_no += n
    return out


def chunk(text: str) -> list[Chunk]:
    """Fallback chunker: blank-line/definition-boundary windows with overlap.

    ~80-line windows, 10-line overlap; an oversized window is split at a line
    boundary near the character cap, so no line of the file is left out of
    every chunk. A single line longer than the cap is emitted whole: a chunk
    boundary is never cut through a line.
    """
    lines = split_lines(text)
    if not lines:
        return []
    if len(text) <= CHUNK_CHAR_CAP:
        return [_make_chunk(lines, 1, 0)]

    chunks: list[Chunk] = []
    idx = 0
    pos = 0
    while pos < len(lines):
        window = lines[pos:pos + WINDOW_LINES]
        window_text = "\n".join(window)
        if len(window_text) > CHUNK_CHAR_CAP:
            # THIS is where an oversized window is divided; _make_chunk does
            # not truncate, so the branch is reachable by construction.
            for c in _chunks_from_parts(_split_text(window_text), pos + 1, idx):
                chunks.append(c)
                idx += 1
        else:
            chunks.append(_make_chunk(window, pos + 1, idx))
            idx += 1
        if pos + WINDOW_LINES >= len(lines):
            break
        pos += WINDOW_LINES - OVERLAP_LINES
    return chunks


def _split_text(text: str) -> list[str]:
    parts: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for ln in text.split("\n"):
        if cur_len + len(ln) + 1 > CHUNK_CHAR_CAP and cur:
            parts.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(ln)
        cur_len += len(ln) + 1
    if cur:
        parts.append("\n".join(cur))
    return parts


# ---------------------------------------------------------------------------
# Schema v3 best-effort fields for the regex-fallback path (design §3):
# signature = first line of the chunk, visibility per the Python underscore
# rule only (other languages → public).
# ---------------------------------------------------------------------------

REGEX_SIG_CAP = 120


def regex_signature(text: str) -> str:
    """First physical line of the chunk, whitespace-collapsed, capped."""
    first = split_lines(text)[0] if text else ""
    return " ".join(first.split())[:REGEX_SIG_CAP]


def regex_visibility(name: str | None) -> str:
    """Python-underscore rule only for the fallback path; else public."""
    if name and name.startswith("_"):
        return "private"
    return "public"