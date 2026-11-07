"""A reported line range is a range in the file as the file counts lines.

``chunk`` split on ``str.splitlines()``, which also breaks on form feed,
vertical tab, ``\x1c``-``\x1e``, U+2028 and U+2029 -- characters that live
INSIDE a line for every editor. Each break added a phantom line, so chunk
ranges ran past the end of the file and ``get-code-context --start-line``
read a different line than the range promised.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.chunker import chunk  # noqa: E402

MARKER = "MARKER the line a range must point at"


def _corpus() -> tuple[str, int]:
    """A 2002-line file carrying a form feed and U+2028 inside lines."""
    lines = [f"line {n} of the ordinary corpus" for n in range(1, 1501)]
    lines.append("before the form feed")
    lines.append("a line with a form feed\u000cafter it")
    lines.append("a line with a line separator\u2028after it")
    lines.append(MARKER)
    lines.extend(f"tail line {n}" for n in range(1, 501))
    return "\n".join(lines) + "\n", len(lines)


def test_no_chunk_range_runs_past_the_end_of_the_file():
    text, file_lines = _corpus()
    # The premise: the character really is inside a line as the file counts it.
    assert len(text.splitlines()) > file_lines
    chunks = chunk(text)
    assert chunks
    past_eof = [(c.start_line, c.end_line) for c in chunks
                if c.end_line > file_lines or c.start_line < 1]
    assert past_eof == []
    assert chunks[-1].end_line == file_lines


def test_a_range_points_at_the_lines_it_names():
    text, file_lines = _corpus()
    marker_line = [n for n, line in enumerate(text.split("\n"), start=1)
                   if line == MARKER][0]
    covering = [c for c in chunk(text)
                if c.start_line <= marker_line <= c.end_line]
    assert covering, f"no chunk covers line {marker_line}"
    for c in covering:
        assert MARKER in c.text
    assert file_lines == marker_line + 500


def test_a_reference_line_is_a_line_the_file_counts():
    # The textual reference scanner counted lines the same inflated way, so a
    # reported reference line could point at text that is not there.
    from code_indexer.graph_extract import extract_graph_textual

    text = "first line\nsecond\u000cstill second line\ncall_it()\n"
    _imports, refs = extract_graph_textual("notes.txt", text)
    call_lines = {r[0] for r in refs if r[2] == "call_it"}
    assert call_lines == {3}


@pytest.mark.parametrize("separator", ["\r\n", "\n", "\r"])
def test_only_real_line_terminators_split_a_line(separator):
    text = separator.join(["first", "second", "third"]) + separator
    chunks = chunk(text)
    joined = "\n".join(c.text for c in chunks)
    assert "first\nsecond\nthird" == joined.strip("\n")
    assert chunks[-1].end_line == 3
