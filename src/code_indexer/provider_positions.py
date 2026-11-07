"""The one position-conversion site between a provider and the plan format.

Providers speak 0-based lines and UTF-16 code units; the plan speaks 1-based
inclusive lines and byte offsets into the file's stored bytes (ADR-0004
section 3). Two functions convert, in both directions, and nowhere else in
the tool does a UTF-16 position exist:

``position_to_offset``  provider position -> byte offset into the file body
``offset_to_position``  byte offset -> provider position, for a request

The one boundary test is applied to every conversion, both ways: the offset
must land on a byte boundary of the decoded line and the decoded bytes must
be exactly the character prefix the provider named. An offset inside a
surrogate pair, or one that lands inside a multi-byte UTF-8 character, is
refused with ``PositionConversionFailed`` -- never rounded, because a
rounded offset is a coordinate nothing verifies.

The byte offsets are relative to the file body (the bytes after any
byte-order mark), which is the frame every plan offset uses.
"""

from __future__ import annotations

import bisect

from .errors import PositionConversionFailedError
from .plans import TextFile

__all__ = ["position_to_offset", "offset_to_position", "CONVERSION_VERSION"]

# The version of the conversion this module implements. ADR-0006 section 5 has
# the plan record it in its ``provider`` member, so a plan written under one
# conversion is not silently reinterpreted under another.
CONVERSION_VERSION = 1


def position_to_offset(tf: TextFile, line: int, character: int) -> int:
    """``(0-based line, UTF-16 code units)`` -> byte offset into ``tf.body``."""
    if not isinstance(line, int) or not isinstance(character, int):
        raise PositionConversionFailedError(
            f"{tf.rel_path}: a provider position must be integer line and "
            "character values")
    if line < 0 or line >= len(tf.lines):
        raise PositionConversionFailedError(
            f"{tf.rel_path}: provider line {line + 1} is outside the file's "
            f"{len(tf.lines)} line(s)")
    if character < 0:
        raise PositionConversionFailedError(
            f"{tf.rel_path}:{line + 1}: provider character {character} is "
            "negative")
    in_line = _utf16_to_byte_in_line(tf.lines[line], character,
                                     tf.rel_path, line)
    return tf.line_starts[line] + in_line


def offset_to_position(tf: TextFile, offset: int) -> tuple[int, int]:
    """Byte offset into ``tf.body`` -> ``(0-based line, UTF-16 code units)``.

    The offset must lie inside a line's text: an offset on a line terminator
    or past the end of the body names no position a provider could hold, and
    is refused rather than clamped.
    """
    if not isinstance(offset, int) or offset < 0 or offset > len(tf.body):
        raise PositionConversionFailedError(
            f"{tf.rel_path}: offset {offset!r} is outside the file body "
            f"(0..{len(tf.body)})")
    if not tf.lines:
        raise PositionConversionFailedError(
            f"{tf.rel_path}: the file has no line a position could name")
    line = bisect.bisect_right(tf.line_starts, offset) - 1
    if line < 0:
        line = 0
    start = tf.line_starts[line]
    end = tf.line_end_bytes[line]
    if offset > end:
        raise PositionConversionFailedError(
            f"{tf.rel_path}: offset {offset} falls on the line terminator after "
            f"line {line + 1}, which no provider position names")
    in_line = offset - start
    text = tf.lines[line]
    encoded = text.encode("utf-8")
    try:
        prefix = encoded[:in_line].decode("utf-8")
    except UnicodeDecodeError:
        raise PositionConversionFailedError(
            f"{tf.rel_path}:{line + 1}: offset {in_line} lands inside a UTF-8 "
            "character, so no provider position names it exactly") from None
    character = sum(2 if ord(ch) > 0xFFFF else 1 for ch in prefix)
    _require_boundary(encoded, in_line, tf.rel_path, line)
    return line, character


# ---------------------------------------------------------------------------
# the per-line conversions, private to this module
# ---------------------------------------------------------------------------

def _utf16_to_byte_in_line(text: str, character: int, rel_path: str,
                           line: int) -> int:
    units = 0
    byte_offset = 0
    for ch in text:
        if units == character:
            encoded = text.encode("utf-8")
            _require_boundary(encoded, byte_offset, rel_path, line)
            return byte_offset
        width = 2 if ord(ch) > 0xFFFF else 1
        units += width
        byte_offset += len(ch.encode("utf-8"))
        if units > character:
            raise PositionConversionFailedError(
                f"{rel_path}:{line + 1}: character {character} falls inside a "
                "surrogate pair; a position inside a character is refused, "
                "never rounded")
    if units == character:
        encoded = text.encode("utf-8")
        _require_boundary(encoded, byte_offset, rel_path, line)
        return byte_offset
    raise PositionConversionFailedError(
        f"{rel_path}:{line + 1}: character {character} is past the end of the "
        f"line ({units} UTF-16 code unit(s))")


def _require_boundary(encoded: bytes, offset: int, rel_path: str,
                      line: int) -> None:
    """The one boundary test: the offset must start a decoded character."""
    if offset < 0 or offset > len(encoded):
        raise PositionConversionFailedError(
            f"{rel_path}:{line + 1}: byte offset {offset} is outside the line's "
            f"{len(encoded)} byte(s)")
    if offset == len(encoded):
        return
    if encoded[offset] & 0xC0 == 0x80:
        raise PositionConversionFailedError(
            f"{rel_path}:{line + 1}: byte offset {offset} lands inside a "
            "multi-byte character, so it is not a byte boundary of the "
            "decoded line")
