"""Contextual embedding-text construction (embed_text) unit tests."""

from __future__ import annotations

import hashlib


from code_indexer.chunker import Chunk
from code_indexer.embed_text import embed_text


def _chunk(text: str, symbol: str | None = "Foo") -> Chunk:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Chunk(
        text=text, chunk_hash="sha256:" + h, symbol=symbol,
        start_line=1, end_line=text.count("\n") + 1, chunk_index=0,
    )


def test_header_contains_file_path_and_symbol():
    c = _chunk("void Tick() { }")
    out = embed_text("Vivarium.Sim/CommandBus.cs", c)
    lines = out.split("\n")
    assert lines[0] == "Vivarium.Sim/CommandBus.cs"
    assert lines[1] == "symbol: Foo"
    assert lines[2] == "void Tick() { }"


def test_no_symbol_drops_symbol_line():
    c = _chunk("{ json: true }", symbol=None)
    out = embed_text("pkg/data.json", c)
    lines = out.split("\n")
    assert lines[0] == "pkg/data.json"
    assert len(lines) == 2
    assert "symbol" not in lines[1]


def test_deterministic_for_identical_inputs():
    c = _chunk("int Add(int a, int b) => a + b;")
    assert embed_text("src/math.cs", c) == embed_text("src/math.cs", c)


def test_chunk_text_with_newlines_is_preserved_verbatim():
    body = "line one\nline two\nline three"
    c = _chunk(body, symbol="Multi")
    out = embed_text("src/a.py", c)
    assert out.endswith(body)
    # exactly one header line, one symbol line, then untouched body
    assert out == "src/a.py\nsymbol: Multi\n" + body


def test_multibyte_chunk_text_survives():
    body = "설명 주석 comment — em dash ✓"
    c = _chunk(body, symbol="Wide")
    out = embed_text("src/unicode.cs", c)
    assert body in out
