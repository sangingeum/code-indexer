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

from code_indexer import ts_chunker  # noqa: E402
from code_indexer.chunker import chunk, split_lines  # noqa: E402

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


# ---------------------------------------------------------------------------
# The AST chunker's oversized-unit split.
#
# ``_walk`` maps a piece's window onto the unit's own text, and the unit's
# text onto the file. It started the offset at the unit's first line and then
# added the piece's own start line (relative to the unit text, so 1-based),
# and advanced the offset by each piece's LENGTH -- so the unit's start was
# counted twice and every later piece drifted further. The reported range of
# a split declaration was then a range the file does not have: 392 of 823
# real files and 5053 chunk rows ran past EOF. A split piece's start_line is
# already relative to the unit text, so the file line is
# ``unit_start + piece_start - 1`` -- no running offset.
# ---------------------------------------------------------------------------

# A method body alone is over the 1000-character cap, so the unit is split.
AST_PY_BODY = "".join(f"        total = combine(total, {n})\n"
                      for n in range(1, 41))
AST_CS_BODY = "".join(f"        total = Combine(total, {n});\n"
                      for n in range(1, 41))

AST_PY = (
    "class Big:\n"
    "    def huge(self):\n"
    "        total = 0\n"
    + AST_PY_BODY
    + "        return total\n"
    "\n"
    "def tail():\n"
    "    return 1\n"
)

AST_CS = (
    "public class Big\n"
    "{\n"
    "    public int Huge()\n"
    "    {\n"
    "        int total = 0;\n"
    + AST_CS_BODY
    + "        return total;\n"
    "    }\n"
    "\n"
    "    public int Tail() { return 1; }\n"
    "}\n"
)

# One oversized declaration (a method over the character cap) followed by a
# short unit, in a parsed source language on each side of the AST path.
AST_CASES = [
    pytest.param("unit.py", AST_PY, AST_PY_BODY, id="python"),
    pytest.param("unit.cs", AST_CS, AST_CS_BODY, id="csharp"),
]


def _ast_chunks(name: str, text: str, body: str):
    chunks, verdict = ts_chunker.parse_file(name, text)
    assert verdict == "parsed", f"fixture must parse: {verdict}"
    assert any(c.source == "ast" for c in chunks), "fixture must take the AST path"
    # Premise: one declaration is over the cap, so the split branch runs.
    assert len(body) > ts_chunker._CHAR_CAP, len(body)
    return chunks


@pytest.mark.parametrize("name,text,body", AST_CASES)
def test_no_ast_chunk_range_runs_past_the_end_of_the_file(name, text, body):
    lines = split_lines(text)
    past_eof = [(c.start_line, c.end_line)
                for c in _ast_chunks(name, text, body)
                if c.start_line < 1 or c.end_line > len(lines)]
    assert past_eof == [], \
        f"{name} has {len(lines)} lines; chunks report {past_eof}"


@pytest.mark.parametrize("name,text,body", AST_CASES)
def test_every_ast_chunk_range_points_at_the_lines_it_came_from(name, text, body):
    # Read the file back and compare: the chunker's own arithmetic is not
    # evidence about a range.
    lines = split_lines(text)
    for c in _ast_chunks(name, text, body):
        assert 1 <= c.start_line <= c.end_line <= len(lines), (c.start_line, c.end_line)
        got = c.text.split("\n")
        assert len(got) == c.end_line - c.start_line + 1, \
            f"{name} chunk at {c.start_line}-{c.end_line} has {len(got)} lines"
        # A tree-sitter node starts at its declaration token, so its first
        # line is a suffix of the file's line (the indent is outside the
        # node); content is what must match, not the node's own slicing.
        for got_line, file_line in zip(got, lines[c.start_line - 1:c.end_line]):
            assert got_line.strip() == file_line.strip(), (
                f"{name} chunk at {c.start_line}-{c.end_line} holds "
                f"{got_line!r}, the file has {file_line!r}")


@pytest.mark.parametrize("name,text,body", AST_CASES)
def test_the_short_unit_after_an_oversized_one_keeps_its_own_range(name, text, body):
    # The split pieces must not swallow the declaration that follows them: the
    # short tail unit answers with its own line, not a drifted one.
    lines = split_lines(text)
    tail_line = [n for n, line in enumerate(lines, 1)
                 if "def tail" in line or "Tail()" in line][0]
    chunks = _ast_chunks(name, text, body)
    assert [c for c in chunks if c.start_line == tail_line], \
        f"no chunk starts at the tail unit on line {tail_line}"
    if name.endswith(".py"):
        assert chunks[-1].end_line == len(lines)
