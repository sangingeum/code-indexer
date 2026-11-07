"""The position conversion, proved against the five fixed cases (ADR-0006 s.5).

A provider speaks 0-based lines and UTF-16 code units; the plan speaks
1-based lines and byte offsets into the file body. ``provider_positions`` is
the only conversion site, and a position that cannot be placed exactly is
refused rather than rounded. The cases fixed by the record are: an
astral-plane line, a CRLF file, a file with a byte-order mark, an empty last
line, and an offset inside a surrogate pair.
"""

from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer import plans  # noqa: E402
from code_indexer.errors import PositionConversionFailedError  # noqa: E402
from code_indexer.provider_positions import (  # noqa: E402
    offset_to_position,
    position_to_offset,
)


def text_file(raw: bytes, rel_path: str = "mod.py") -> plans.TextFile:
    return plans.text_file_from_bytes(rel_path, raw)


# --- an astral-plane line ---------------------------------------------------

def test_an_astral_character_counts_twice_in_utf16_and_four_times_in_bytes():
    tf = text_file("s = '\U0001d54f'; t = 1\n".encode("utf-8"))
    # Five single-unit characters precede the astral one.
    assert position_to_offset(tf, 0, 5) == 5
    # The astral character spans UTF-16 units 5-6 and four UTF-8 bytes.
    assert position_to_offset(tf, 0, 7) == 9
    assert offset_to_position(tf, 5) == (0, 5)
    assert offset_to_position(tf, 9) == (0, 7)
    # The whole line is 15 UTF-16 units and 20 bytes.
    assert position_to_offset(tf, 0, 15) == len(tf.lines[0].encode("utf-8"))


def test_a_mid_line_position_is_not_a_conversion_failure():
    tf = text_file(b"alpha = beta\n")
    offset = position_to_offset(tf, 0, 8)
    assert offset == 8
    assert offset_to_position(tf, offset) == (0, 8)


# --- a CRLF file ------------------------------------------------------------

def test_crlf_offsets_skip_the_terminator_and_exclude_it_from_the_line():
    tf = text_file(b"a = 1\r\nb = 2\r\n")
    assert tf.newline == "crlf"
    assert tf.line_starts == (0, 7)
    assert tf.line_end_bytes == (5, 12)
    assert position_to_offset(tf, 0, 5) == 5
    assert position_to_offset(tf, 1, 0) == 7
    assert offset_to_position(tf, 7) == (1, 0)
    # An offset on the terminator names no position and is refused.
    with pytest.raises(PositionConversionFailedError):
        offset_to_position(tf, 6)


# --- a file with a byte-order mark -----------------------------------------

def test_a_byte_order_mark_lies_before_every_offset_and_is_preserved():
    raw = b"\xef\xbb\xbfx = 1\n"
    tf = text_file(raw)
    assert tf.has_bom is True
    assert tf.body == b"x = 1\n"
    assert position_to_offset(tf, 0, 0) == 0
    assert position_to_offset(tf, 0, 5) == 5
    assert offset_to_position(tf, 0) == (0, 0)
    assert tf.content_hash() == "sha256:" + hashlib.sha256(raw).hexdigest()


# --- an empty last line -----------------------------------------------------

def test_an_empty_last_line_has_a_position_at_its_start():
    tf = text_file(b"one\n\n")
    assert tf.lines == ("one", "")
    assert position_to_offset(tf, 1, 0) == 4
    assert offset_to_position(tf, 4) == (1, 0)
    with pytest.raises(PositionConversionFailedError):
        position_to_offset(tf, 1, 1)


# --- a position inside a surrogate pair ------------------------------------

def test_a_position_inside_a_surrogate_pair_is_refused_not_rounded():
    tf = text_file("x = \U0001d54f\n".encode("utf-8"))
    with pytest.raises(PositionConversionFailedError) as caught:
        position_to_offset(tf, 0, 5)
    assert "surrogate pair" in str(caught.value)


def test_an_offset_inside_a_multibyte_character_is_refused():
    tf = text_file("price = 1\u20ac\n".encode("utf-8"))
    inside = tf.lines[0].encode("utf-8").index("\u20ac".encode("utf-8")) + 1
    with pytest.raises(PositionConversionFailedError):
        offset_to_position(tf, inside)


# --- the same boundary test in both directions ------------------------------

def test_every_character_boundary_round_trips_through_both_functions():
    tf = text_file("a\U0001d54f\u20acz\n".encode("utf-8"))
    text = tf.lines[0]
    units = 0
    byte_offset = 0
    for char in text + "\x00":
        if byte_offset <= len(tf.body):
            assert offset_to_position(tf, byte_offset) == (0, units)
            assert position_to_offset(tf, 0, units) == byte_offset
        if char == "\x00":
            break
        units += 2 if ord(char) > 0xFFFF else 1
        byte_offset += len(char.encode("utf-8"))


def test_a_line_past_the_end_of_the_file_is_refused():
    tf = text_file(b"only\n")
    with pytest.raises(PositionConversionFailedError):
        position_to_offset(tf, 3, 0)
    with pytest.raises(PositionConversionFailedError):
        position_to_offset(tf, -1, 0)
