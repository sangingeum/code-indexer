"""Embedding-text construction (contextual chunk headers).

Embedding raw code text alone scatters chunks into a tight similarity band:
a natural-language query against, say, "pathfinding" scores the true grid
helper BELOW test-helper chunks whose identifiers merely resemble the query
words. Prepending a small header (project-relative path + symbol) gives the
embedder the missing context and measurably lifts the relevant chunk into
the top ranks (see scripts/ diagnostic evidence, 2026-09-23 round).
"""

from __future__ import annotations

from .chunker import Chunk

CONTEXT_HEADER_SEPARATOR = "\n"


def embed_text(file: str, chunk: Chunk) -> str:
    """Chunk text with a contextual header for embedding.

    Header: project-relative file path, the symbol when the chunk carries
    one, then the raw chunk text. Deterministic; identical for identical
    (file, chunk) pairs so incremental re-indexing stays hash-stable.
    """
    parts = [file]
    if chunk.symbol:
        parts.append(f"symbol: {chunk.symbol}")
    parts.append(chunk.text)
    return CONTEXT_HEADER_SEPARATOR.join(parts)
