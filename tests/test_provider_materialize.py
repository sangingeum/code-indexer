"""Provider edits -> line-aligned candidate plan edits (ADR-0006 section 6).

A provider edit is intra-line; a plan edit is line-granular. ``materialize``
expands each provider edit to the whole lines it touches, merging edits that
share a line in descending byte order, and refuses a cross-line edit rather
than approximating it. Bytes outside the provider's range survive exactly,
and each record's ``expected_old_text_hash`` covers the whole old line so a
region guard proves the bytes about to be replaced.
"""

from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer import plans  # noqa: E402
from code_indexer import refactoring_provider as provider  # noqa: E402
from code_indexer.errors import (  # noqa: E402
    EXIT_USAGE,
    NonLineAlignedProviderEditError,
    OverlappingEditsError,
)


def text_file(raw: bytes, rel_path: str = "mod.py") -> plans.TextFile:
    return plans.text_file_from_bytes(rel_path, raw)


def edit(start: tuple[int, int], end: tuple[int, int], new_text: str,
         rel_path: str = "mod.py") -> provider.ProviderEdit:
    return provider.ProviderEdit(
        path=rel_path,
        start=provider.ProviderPosition(*start),
        end=provider.ProviderPosition(*end),
        new_text=new_text)


# --- one edit becomes one whole-line edit -----------------------------------

def test_an_intra_line_edit_expands_to_the_whole_line_it_touches():
    tf = text_file(b"def greet(name):\n    return 1\n")
    edits = provider.materialize(tf, [edit((0, 4), (0, 9), "hello")])
    assert len(edits) == 1
    record = edits[0]
    assert record["kind"] == provider.PROVIDER_EDIT_KIND
    assert record["path"] == "mod.py"
    assert (record["start_line"], record["end_line"]) == (1, 1)
    assert (record["start_byte"], record["end_byte"]) == (0, 16)
    assert record["new_text"] == "def hello(name):"


def test_bytes_outside_the_provider_range_are_preserved_exactly():
    raw = b"  x = value  # tail\n"
    tf = text_file(raw)
    edits = provider.materialize(tf, [edit((0, 6), (0, 11), "other")])
    assert edits[0]["new_text"] == "  x = other  # tail"


def test_the_old_text_hash_covers_the_whole_old_line():
    raw = b"  x = value  # tail\n"
    tf = text_file(raw)
    record = provider.materialize(tf, [edit((0, 6), (0, 11), "other")])[0]
    whole_line = b"  x = value  # tail"
    assert record["expected_old_text"] == whole_line.decode("utf-8")
    assert record["expected_old_text_hash"] == (
        "sha256:" + hashlib.sha256(whole_line).hexdigest())
    # And it is the hash of exactly the bytes the record names.
    assert record["expected_old_text_hash"] == "sha256:" + hashlib.sha256(
        tf.body[record["start_byte"]:record["end_byte"]]).hexdigest()


# --- two edits on one line merge -------------------------------------------

def test_two_edits_on_one_line_merge_in_descending_byte_order():
    tf = text_file(b"x = a + b\n")
    edits = provider.materialize(tf, [
        edit((0, 4), (0, 5), "alpha"),
        edit((0, 8), (0, 9), "beta"),
    ])
    assert len(edits) == 1
    assert edits[0]["new_text"] == "x = alpha + beta"
    assert edits[0]["expected_old_text"] == "x = a + b"


def test_two_edits_on_different_lines_stay_separate_edits():
    tf = text_file(b"a = one\nb = two\n")
    edits = provider.materialize(tf, [
        edit((0, 4), (0, 7), "ONE"),
        edit((1, 4), (1, 7), "TWO"),
    ])
    assert [item["start_line"] for item in edits] == [1, 2]
    assert [item["new_text"] for item in edits] == ["a = ONE", "b = TWO"]


# --- refusals ---------------------------------------------------------------

def test_an_edit_crossing_a_line_terminator_is_refused():
    tf = text_file(b"one\ntwo\n")
    with pytest.raises(NonLineAlignedProviderEditError) as caught:
        provider.materialize(tf, [edit((0, 1), (1, 1), "x")])
    assert caught.value.exit_code != EXIT_USAGE
    assert "cross-line" in str(caught.value)


def test_two_overlapping_edits_on_one_line_are_refused():
    tf = text_file(b"abcdefgh\n")
    with pytest.raises(OverlappingEditsError):
        provider.materialize(tf, [
            edit((0, 2), (0, 5), "X"),
            edit((0, 4), (0, 7), "Y"),
        ])


def test_an_edit_naming_another_file_is_refused():
    tf = text_file(b"x = 1\n")
    with pytest.raises(Exception) as caught:
        provider.materialize(tf, [edit((0, 0), (0, 1), "y", rel_path="other.py")])
    assert "other.py" in str(caught.value)


def test_empty_edits_is_a_defect_not_an_empty_plan():
    tf = text_file(b"x = 1\n")
    with pytest.raises(Exception):
        provider.materialize(tf, [])


# --- the file's own geometry survives --------------------------------------

def test_a_crlf_file_materializes_to_lines_without_terminators():
    tf = text_file(b"a = one\r\nb = two\r\n")
    edits = provider.materialize(tf, [edit((1, 4), (1, 7), "TWO")])
    assert edits[0]["start_byte"] == 9
    assert edits[0]["end_byte"] == 16
    assert edits[0]["expected_old_text"] == "b = two"
    assert edits[0]["new_text"] == "b = TWO"
    assert "\r" not in edits[0]["new_text"]


def test_an_astral_character_before_the_edit_does_not_shift_it_for_the_plan():
    tf = text_file("label = '\U0001d54f'; greet = 1\n".encode("utf-8"))
    line = tf.lines[0]
    start = line.index("greet")
    byte_start = len(line[:start].encode("utf-8"))
    character = sum(2 if ord(ch) > 0xFFFF else 1 for ch in line[:start])
    edits = provider.materialize(tf, [
        edit((0, character), (0, character + 5), "hello")])
    assert edits[0]["start_byte"] == 0
    assert edits[0]["new_text"] == line.replace("greet", "hello")
    assert byte_start == len("label = '\U0001d54f'; ".encode("utf-8"))
