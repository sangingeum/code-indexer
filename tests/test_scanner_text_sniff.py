"""The textual sniff must judge the file, not the cut it was read through.

The scanner reads a bounded sample and decodes it strictly. When the sample
ends inside a multi-byte character -- the ordinary case for any source file
carrying box-drawing characters near byte 4096 -- strict decoding of the
SAMPLE fails and a perfectly good UTF-8 file is dropped from the index with
only a debug log. Eight tracked C# files were lost this way.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.scanner import _is_binary, _looks_utf8ish, scan_project  # noqa: E402
from pass_rig import index_project, stub_core  # noqa: E402

BOX = "\u2500".encode("utf-8")   # e2 94 80 -- the character the C# files carry


def _boundary_file(tmp_path, start_offset: int) \
        -> tuple[str, str]:
    """A file whose multi-byte character straddles the 4096-byte sample cut."""
    path = tmp_path / "Boundary.cs"
    prefix = b"// a C# source file\n"
    pad = start_offset - len(prefix)
    data = prefix + b"x" * pad + BOX + b" rest of the line\n"
    assert len(prefix) + pad == start_offset
    path.write_bytes(data)
    return str(path), path.name


@pytest.mark.parametrize("start_offset", [4094, 4095])
def test_sniff_accepts_a_character_the_sample_boundary_splits(tmp_path,
                                                             start_offset):
    path, _ = _boundary_file(tmp_path, start_offset)
    # The premise: byte 4096 really does fall inside the character.
    with open(path, "rb") as fh:
        sample = fh.read(4096)
    with pytest.raises(UnicodeDecodeError):
        sample.decode("utf-8")
    assert _is_binary(path) is False
    assert _looks_utf8ish(path) is True


def test_sniff_still_refuses_a_file_that_is_not_utf8(tmp_path):
    path = tmp_path / "latin.cs"
    path.write_bytes(b"// caf\xe9 au lait\n")
    assert _looks_utf8ish(str(path)) is False


def test_scan_keeps_a_boundary_split_file(tmp_path):
    _, name = _boundary_file(tmp_path, 4095)
    scanned = {f.path for f in scan_project(str(tmp_path))}
    assert name in scanned


def test_skeleton_answers_for_a_boundary_split_file(tmp_path):
    _, name = _boundary_file(tmp_path, 4095)
    core = stub_core(tmp_path)
    reg, entry = index_project(core, tmp_path)
    m = core.manifest_for(entry.slug)
    try:
        assert m.get_file(name) is not None
    finally:
        m.close()
    payload = core.skeleton(entry)
    files = {f["file"]: f for f in payload["files"]}
    assert name in files
    rendered = core.format_skeleton(payload)
    assert rendered != "(no files)"
    assert name in rendered
    # outline answers for the file instead of claiming it is not indexed
    outline = core.outline(entry, name)
    assert outline["file"] == name
    assert outline["declarations"] == []
