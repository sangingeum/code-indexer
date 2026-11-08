"""No part of a document may vanish between the file and its chunks.

``chunk`` called ``_make_chunk``, which already truncated an oversized window
to its first part; ``chunk``'s own "too long, split it" branch could therefore
never run. A file of long lines lost ~86% of its text with nothing reported,
which is silent recall loss for semantic search.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from code_indexer.chunker import (CHUNK_CHAR_CAP, OVERLAP_LINES, WINDOW_LINES,  # noqa: E402
                                  chunk)


def _long_line_corpus(count: int = 200, width: int = 92) -> list[str]:
    return [f"line {n:03d} " + "x" * width for n in range(1, count + 1)]


def test_every_input_line_reaches_a_chunk():
    lines = _long_line_corpus()
    text = "\n".join(lines) + "\n"
    chunks = chunk(text)
    seen = {line for c in chunks for line in c.text.split("\n")}
    missing = [line for line in lines if line not in seen]
    assert missing == []


def test_the_emitted_chunks_cover_the_whole_text():
    lines = _long_line_corpus()
    text = "\n".join(lines) + "\n"
    chunks = chunk(text)
    covered = sum(len(c.text) for c in chunks)
    assert covered >= len(text)
    assert chunks[-1].end_line == len(lines)


def test_a_window_of_ordinary_lines_splits_at_the_cap():
    """A window of ordinary lines is divided at the cap, never truncated.

    This is what the corpus can observe. It is NOT a global invariant: a
    single line longer than the cap is emitted whole (the next test), and
    the shipped index has 136 chunks over 1,000 characters for exactly that
    reason. A name promising a global cap would still pass if the cap were
    removed from the split branch, so it names the property it pins.
    """
    text = "\n".join(_long_line_corpus()) + "\n"
    chunks = chunk(text)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.text) <= CHUNK_CHAR_CAP, (c.chunk_index, len(c.text))


def test_a_single_long_line_stays_whole():
    """A boundary is never cut through a line: a minified one-line file is
    one chunk over the cap, not several chunks with lines cut mid-way."""
    line = "x" * (CHUNK_CHAR_CAP * 4)
    chunks = chunk(line + "\n")
    assert len(chunks) == 1, [len(c.text) for c in chunks]
    assert chunks[0].text == line
    assert len(chunks[0].text) > CHUNK_CHAR_CAP


def test_overlap_between_windows_is_kept():
    # 80-line windows stepping 70 lines: consecutive windows overlap by 10.
    lines = [f"line {n}" for n in range(1, 401)]
    chunks = [c for c in chunk("\n".join(lines) + "\n")]
    assert len(chunks) > 1
    first, second = chunks[0], chunks[1]
    assert second.start_line == first.start_line + WINDOW_LINES - OVERLAP_LINES
    assert second.start_line <= first.end_line
