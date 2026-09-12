"""Unit tests: scanner filters, chunker, manifest diff logic. No Ollama/Qdrant."""

import pytest

from mcp_code_indexer.chunker import chunk
from mcp_code_indexer.manifest import Manifest, ManifestFile
from mcp_code_indexer.scanner import scan_project


@pytest.fixture()
def repo(tmp_path):
    """A throwaway mini-repo with ignorable junk."""
    (tmp_path / "app.py").write_text("def main():\n    pass\n" * 20)
    (tmp_path / "README.md").write_text("# Test project\nSome text here.\n")
    (tmp_path / "notes.log").write_text("noise\n" * 100)
    (tmp_path / "binary.png").write_bytes(b"\x00\x01\x02binary")
    sub = tmp_path / "node_modules"
    sub.mkdir()
    (sub / "dep.js").write_text("var x = 1;")
    (tmp_path / ".gitignore").write_text("*.log\n")
    ignored = tmp_path / "secret"
    ignored.mkdir()
    (ignored / "key.txt").write_text("secret content")
    (tmp_path / ".codeindexignore").write_text("secret/\n")
    return tmp_path


def test_scanner_filters(repo):
    files = scan_project(str(repo))
    names = {f.path for f in files}
    assert "app.py" in names
    assert "README.md" in names
    assert "notes.log" not in names          # .gitignore
    assert "binary.png" not in names         # binary
    assert "node_modules/dep.js" not in names
    assert "secret/key.txt" not in names     # .codeindexignore


def test_content_hash_changes(repo):
    f1 = {f.path: f.content_hash for f in scan_project(str(repo))}["app.py"]
    p = repo / "app.py"
    p.write_text("def changed():\n    pass\n")
    f2 = {f.path: f.content_hash for f in scan_project(str(repo))}["app.py"]
    assert f1 != f2


def test_chunker_lines_and_overlap():
    text = "\n".join(f"line {i}" for i in range(200))
    chunks = chunk(text)
    assert len(chunks) > 1
    assert chunks[0].start_line == 1
    # Overlap: chunk 2 starts before chunk 1 ends.
    assert chunks[1].start_line < chunks[0].end_line
    for c in chunks:
        assert len(c.text) <= 1010  # ~cap tolerance
        assert c.chunk_hash.startswith("sha256:")


def test_chunker_symbol_detection():
    code = "\n".join(
        ["import os"] + [f"# filler {i}" for i in range(20)]
        + ["def my_function(x):", "    return x"]
    )
    chunks = chunk(code)
    assert any(c.symbol == "my_function" for c in chunks)


def test_manifest_roundtrip(tmp_path):
    m = Manifest(str(tmp_path / "manifest.db"))
    try:
        m.upsert_files([
            ManifestFile("a.py", "sha256:x", 10, 2, "ok"),
            ManifestFile("b.py", "sha256:y", 20, 1, "ok"),
        ])
        files = m.all_files()
        assert set(files) == {"a.py", "b.py"}
        m.delete_files(["a.py"])
        assert set(m.all_files()) == {"b.py"}
        m.set_meta("last_indexed", "123")
        assert m.get_meta("last_indexed") == "123"
    finally:
        m.close()


def test_manifest_deleted_detection(tmp_path):
    """The core incremental property: deleted = old set - new scan set."""
    m = Manifest(str(tmp_path / "manifest.db"))
    try:
        m.upsert_files([
            ManifestFile("a.py", "h1", 1, 1, "ok"),
            ManifestFile("gone.py", "h2", 1, 1, "ok"),
        ])
        new_scan = {"a.py"}
        old = m.all_files()
        deleted = [p for p in old if p not in new_scan]
        assert deleted == ["gone.py"]
    finally:
        m.close()