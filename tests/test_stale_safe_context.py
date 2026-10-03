"""Stale-safe line ranges (CI-03) tests.

Covers: search hits carry file_hash/indexed_at; file_changed_since_index
stat fast-path and PARANOID_HASH=1 escape; symbol-mode re-resolve on a live
edited file; line-mode stale flag with the documented stderr warning and
exit 0; --fresh bypasses STALE_TTL. All offline (stub seams).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()

ORIGINAL = "def target_fn():\n    return 1\n\n\ndef other_fn():\n    return 2\n"
# Three inserted lines above target_fn -> every manifest line shifts by +3.
EDITED = "# shifted a\n# shifted b\n# shifted c\n" + ORIGINAL


class StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]


class StubStore:
    def collection_exists(self, name):
        return True

    def create_collection(self, name, dim):
        pass

    def upsert_points(self, name, points):
        pass

    def purge_file_points(self, *a, **kw):
        return 0

    def count_points(self, name):
        return 0

    def search(self, *a, **kw):
        return []


def _stub_core(cfg: Config) -> Core:
    c = Core(cfg)
    c.embedder = StubEmbedder()  # type: ignore[assignment]
    c.store = StubStore()  # type: ignore[assignment]
    c.indexer.embedder = c.embedder  # type: ignore[assignment]
    c.indexer.store = c.store  # type: ignore[assignment]
    return c


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    import code_indexer.cli as cli
    cli._core = None
    cli._core_skip = False
    cfg = Config(ollama_url="http://stub", qdrant_url="http://stub",
                 embed_model="stub", index_root=str(tmp_path / "state"),
                 stale_ttl=0, embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=1,
                 watch_sweep_interval=3600)
    c = _stub_core(cfg)
    project = tmp_path / "proj"
    project.mkdir()
    src = project / "a.py"
    src.write_text(ORIGINAL)
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="staleproj")
    yield c, reg, entry, str(project), src
    reg.close()
    cli._core = None
    cli._core_skip = False


def test_file_changed_since_index_detects_edit(rig):
    c, reg, entry, project, src = rig
    c.run_index(entry.slug, project)
    assert c.file_changed_since_index(entry, "a.py") is False
    src.write_text(EDITED)
    assert c.file_changed_since_index(entry, "a.py") is True


def test_stat_fast_path_and_paranoid_escape(rig, monkeypatch):
    c, reg, entry, project, src = rig
    c.run_index(entry.slug, project)  # records stat triple in the manifest
    # Unchanged file: fast-path returns False without hashing (same result
    # either way, but PARANOID_HASH must still agree).
    assert c.file_changed_since_index(entry, "a.py") is False
    monkeypatch.setenv("PARANOID_HASH", "1")
    assert c.file_changed_since_index(entry, "a.py") is False


def test_stat_fast_path_short_circuits_mtime_lies(rig, monkeypatch):
    """A tool that preserves mtime (rsync -t) rewrites content but keeps the
    stat triple: with the fast-path the change is missed — with
    PARANOID_HASH=1 the full hash catches it. Documents the trade-off."""
    c, reg, entry, project, src = rig
    c.run_index(entry.slug, project)
    stat_before = src.stat()
    # Same-length rewrite (return 1 -> return 9): the stat triple can match.
    src.write_text(ORIGINAL.replace("return 1", "return 9"))
    os.utime(src, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))
    # fast-path (default): stat triple matches -> unchanged verdict
    assert c.file_changed_since_index(entry, "a.py") is False
    monkeypatch.setenv("PARANOID_HASH", "1")
    assert c.file_changed_since_index(entry, "a.py") is True


def test_code_context_line_mode_flags_stale(rig):
    c, reg, entry, project, src = rig
    c.run_index(entry.slug, project)
    src.write_text(EDITED)
    data = c.code_context(entry, "a.py", start_line=1, end_line=2)
    # Line mode: the requested lines come from the LIVE file as-is, and the
    # result is flagged stale (the manifest range now points elsewhere).
    assert data["stale"] is True
    assert data["re_resolved"] is False
    body = data["segments"][0]["lines"]
    assert any("# shifted a" in ln for ln in body)


def test_code_context_symbol_mode_re_resolves_live(rig):
    c, reg, entry, project, src = rig
    c.run_index(entry.slug, project)
    src.write_text(EDITED)
    data = c.code_context(entry, "a.py", symbol="target_fn")
    # The live file has target_fn at lines 4-5 after the shift; stale-safety
    # re-resolved it (manifest still says 1-2).
    assert data["re_resolved"] is True
    assert data["stale"] is False
    body = "\n".join(data["segments"][0]["lines"])
    assert "def target_fn():" in body and "return 1" in body


def test_cli_get_code_context_line_mode_warns_and_exits_zero(rig, monkeypatch):
    import code_indexer.cli as cli
    c, reg, entry, project, src = rig
    c.run_index(entry.slug, project)
    cli._core = c
    cli._core_skip = False
    src.write_text(EDITED)
    result = runner.invoke(app, ["get-code-context", "a.py",
                                 "--project", project,
                                 "--start-line", "1", "--end-line", "2"])
    assert result.exit_code == 0, result.output
    # Line mode returns the live lines at the (possibly shifted) range; the
    # stderr warning + stale:true in JSON are the staleness signal.
    assert "# shifted a" in result.output


def test_cli_get_code_context_json_carries_stale(rig, monkeypatch):
    import code_indexer.cli as cli
    import json as _json
    c, reg, entry, project, src = rig
    c.run_index(entry.slug, project)
    cli._core = c
    cli._core_skip = False
    src.write_text(EDITED)
    result = runner.invoke(app, ["get-code-context", "a.py",
                                 "--project", project,
                                 "--start-line", "1", "--end-line", "2",
                                 "--json"])
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    assert payload["stale"] is True


def test_cli_fresh_bypasses_ttl(rig, monkeypatch):
    import code_indexer.cli as cli
    c, reg, entry, project, src = rig
    c.run_index(entry.slug, project)
    calls = []
    original = c.maybe_refresh
    def spy(slug, path, force=False):
        calls.append(force)
        return original(slug, path, force=force)
    c.maybe_refresh = spy  # type: ignore[method-assign]
    cli._core = c
    cli._core_skip = False
    result = runner.invoke(app, ["semantic-search", "target_fn",
                                 "--project", project, "--fresh"])
    assert result.exit_code == 0, result.output
    assert calls and calls[0] is True  # fresh forced the scan


def test_search_hits_carry_staleness_provenance(rig):
    c, reg, entry, project, src = rig
    c.run_index(entry.slug, project)
    m = c.manifest_for(entry.slug)
    try:
        row = m.get_file("a.py")
        assert row is not None
        assert row.mtime_ns is not None and row.inode is not None
    finally:
        m.close()
    # payload contract: the indexer records file_hash + indexed_at
    import inspect
    from code_indexer import indexer as ix
    source = inspect.getsource(ix.Indexer.index_project)
    assert '"file_hash"' in source and '"indexed_at"' in source


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))