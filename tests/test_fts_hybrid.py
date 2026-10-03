"""FTS5 lexical index + hybrid fusion tests (hybrid retrieval work item).

Offline: real SQLite FTS5 tables (in-memory / tmp files), stub embedders.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.fts import build_match_query, tokenize_fts  # noqa: E402
from code_indexer.manifest import Manifest  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()


def test_tokenize_splits_identifiers_keeps_original():
    tokens = tokenize_fts("refreshToken user_name x_http2")
    assert "refreshToken" in tokens and "refresh" in tokens and "token" in tokens
    assert "user_name" in tokens and "user" in tokens and "name" in tokens
    assert "x_http2" in tokens and "http" in tokens


def test_build_match_query_quotes_and_prefixes_last():
    q = build_match_query('where do we "refresh" tokens?')
    assert q == '"where" "do" "we" "\"refresh\"" "tokens"*' \
        or q.endswith('"tokens"*')
    assert '"refresh"' in q and '"tokens"*' in q
    assert build_match_query("!!!") is None


def test_fts_roundtrip_and_replace_by_file(tmp_path):
    m = Manifest(str(tmp_path / "m.db"))
    try:
        fts = m.fts()
        rows = [("a.py", 0, "def refresh_token(): pass", "refresh_token",
                 "a.py", 1),
                ("b.py", 0, "class UserAuth: ...", "UserAuth", "b.py", 1)]
        m.fts_add_chunks(rows)
        assert fts.rowcount() == 2
        # Replace-by-file: re-adding a.py keeps the table consistent.
        m.fts_add_chunks([("a.py", 0, "def renamed(): pass", "renamed",
                           "a.py", 1)])
        assert fts.rowcount() == 2
        hits = m.fts_search("renamed", 10)
        assert hits and hits[0]["file"] == "a.py"
        # Purge removes exactly one file's rows.
        m.fts_purge_file("a.py")
        assert fts.rowcount() == 1
    finally:
        m.close()


def test_hybrid_rrf_fusion_and_symbol_shortcut(rig):
    core, reg, entry, _ = rig
    result = core.search("maybe_refresh", project=entry.path, mode="hybrid")
    hits = result["hits"]
    assert hits, "hybrid must return hits"
    assert any(h.get("match") == "symbol" for h in hits), \
        "identifier-shaped query pins exact symbol matches first"


def test_lexical_mode_no_backend_call(rig, monkeypatch):
    core, reg, entry, _ = rig
    calls = []

    def fail_embed(*a, **kw):
        calls.append(1)
        raise AssertionError("lexical mode must not call the embedder")
    monkeypatch.setattr(core.embedder, "embed", fail_embed)
    result = core.search("refresh", project=entry.path, mode="lexical")
    assert result["hits"]
    assert not calls


def test_unknown_mode_rejected(rig):
    core, reg, entry, _ = rig
    with pytest.raises(ValueError, match="unknown search mode"):
        core.search("x", project=entry.path, mode="semantic")


def test_deleted_file_purged_from_fts(rig):
    core, reg, entry, project = rig[:4]
    src = os.path.join(project, "unique_module.py")
    with open(src, "w") as f:
        f.write("def unique_marker():\n    return 1\n")
    core.run_index(entry.slug, entry.path)
    m = core.manifest_for(entry.slug)
    try:
        assert m.fts_search("unique_marker", 5), "indexed into FTS"
    finally:
        m.close()
    os.remove(src)
    core.run_index(entry.slug, entry.path)
    m = core.manifest_for(entry.slug)
    try:
        assert not m.fts_search("unique_marker", 5), "purged from FTS"
    finally:
        m.close()


def test_legacy_manifest_backfills_fts(rig):
    core, reg, entry, project = rig[:4]
    core.run_index(entry.slug, entry.path)
    m = core.manifest_for(entry.slug)
    try:
        m._conn.execute("DELETE FROM chunks_fts")
        m._conn.commit()
        assert m.fts_backfill_needed()
    finally:
        m.close()
    # A later pass backfills idempotently.
    core.run_index(entry.slug, entry.path)
    m = core.manifest_for(entry.slug)
    try:
        assert m.fts_search("refresh", 5), "backfilled"
    finally:
        m.close()


def test_cli_mode_flag(rig, monkeypatch):
    import code_indexer.cli as cli
    core, reg, entry, _ = rig
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["semantic-search", "refresh",
                                 "--project", entry.path,
                                 "--mode", "lexical"])
    assert result.exit_code == 0, result.output
    assert "lexical" in result.output or "no results" in result.output


# ---------------------------------------------------------------------------

@pytest.fixture()
def rig(tmp_path, monkeypatch):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "core.py").write_text(
        "def maybe_refresh(slug):\n"
        "    '''Staleness probe for one slug.'''\n"
        "    return slug\n")
    core = Core(cfg)

    class StubEmbedder:
        def dimension(self):
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
            class _Hit:
                def __init__(self, payload, score):
                    self.payload, self.score = payload, score
            return [_Hit({"file": "core.py", "symbol": "maybe_refresh",
                          "symbol_type": "function", "lang": "python",
                          "start_line": 1, "end_line": 3,
                          "snippet": "def maybe_refresh(slug):"}, 0.9)]

    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="ftsproj")
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    yield core, reg, entry, project
    reg.close()
    monkeypatch.undo()
    shutil.rmtree(cfg.index_root, ignore_errors=True)


import shutil  # noqa: E402

if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))