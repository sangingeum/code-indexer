"""Multi-project search isolation + dead-registration reporting.

One project whose store cannot answer (a registered project with a missing
collection — the live `liveproj` shape) must not kill a whole-project
search: the remaining projects answer, the skipped one is named in a stderr
warning, and the result reports the count. The fingerprint gate stays fatal
(a wrong-model index aborts, it is never skipped). `list-projects` surfaces a
dead registration as `state=error collection=missing` from ONE collection
listing. Entirely offline: stub embedder/store only.
"""

from __future__ import annotations

import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.fingerprint import ConfigError as FingerprintConfigError  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()


# ---------------------------------------------------------------------------
# stubs (no network, no Ollama, no Qdrant)
# ---------------------------------------------------------------------------

class _Hit:
    """One Qdrant point: .score + .payload (search_one reads both)."""

    def __init__(self, file: str) -> None:
        self.score = 0.9
        self.payload = {
            "file": file, "symbol": "f", "symbol_type": "function",
            "lang": "python", "start_line": 1, "end_line": 2,
            "snippet": "def f(): pass", "file_hash": "sha256:x",
            "indexed_at": 1,
        }


class StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]

    def query_instruction_text(self, q):
        return q

    def embed_with_errors(self, texts):
        return [[0.1] * 4 for _ in texts], []

    def close(self):
        pass


class StubStore:
    """Answers only for `live_collection`; anything else raises like Qdrant.

    `list_collections` reports exactly the collections that "exist", so a
    project whose collection is absent is a dead registration.
    """

    def __init__(self, live_collection: str | None = None,
                 collections: set[str] | None = None,
                 listing_available: bool = True) -> None:
        self.live_collection = live_collection
        self.collections = (collections if collections is not None
                            else ({live_collection} if live_collection else set()))
        self.listing_available = listing_available

    def collection_exists(self, name):
        return name in self.collections

    def create_collection(self, name, dim):
        pass

    def upsert_points(self, name, points):
        pass

    def purge_file_points(self, *a, **kw):
        return 0

    def count_points(self, name):
        return 0

    def search(self, name, vector, limit=8, file_filter=None,
               symbol_type=None, language=None):
        if name != self.live_collection:
            raise RuntimeError(
                f"UnexpectedResponse: Collection `{name}` doesn't exist")
        return [_Hit("a.py")]

    def list_collections(self):
        if not self.listing_available:
            raise RuntimeError("qdrant unreachable")
        return set(self.collections)


# ---------------------------------------------------------------------------
# rig: two registered projects (one live, one dead)
# ---------------------------------------------------------------------------

def _rig(tmp_path):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    live = tmp_path / "live"
    dead = tmp_path / "dead"
    for p in (live, dead):
        p.mkdir()
        (p / "a.py").write_text("def f():\n    return 0\n")
    core = Core(cfg, skip_stale_check=True)
    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    live_entry = reg.add(str(live), name="liveproj")
    dead_entry = reg.add(str(dead), name="deadproj")
    core.registry = reg
    core.store = StubStore(f"idx_{live_entry.slug}")  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    return core, live_entry, dead_entry


def _mark_indexed(core, slug):
    """Give the slug a manifest that records a completed pass (state idle)."""
    m = core.manifest_for(slug)
    try:
        m.set_meta("last_indexed", str(time.time()))
    finally:
        m.close()


# ---------------------------------------------------------------------------
# search isolation
# ---------------------------------------------------------------------------

def test_search_skips_unresolvable_project(tmp_path, caplog):
    core, live, dead = _rig(tmp_path)
    with caplog.at_level(logging.WARNING, logger="code-indexer.core"):
        result = core.search("q", skip_refresh=True)
    # The live project answered; the dead one did not kill the run.
    assert result["hits"], result
    assert [s["project"] for s in result["skipped"]] == [dead.path]
    # It is named, loudly, on the warning stream (stdout stays result-only).
    assert dead.path in caplog.text
    assert "skipped" in caplog.text


def test_search_all_projects_unresolvable_errors(tmp_path):
    core, live, dead = _rig(tmp_path)
    core.registry.remove(live.path)  # only the dead project remains
    with pytest.raises(ValueError, match="every project"):
        core.search("q", skip_refresh=True)


def test_search_single_dead_project_errors(tmp_path):
    core, live, dead = _rig(tmp_path)
    with pytest.raises(ValueError, match="every project"):
        core.search("q", project=dead.path, skip_refresh=True)


def test_fingerprint_gate_stays_fatal(tmp_path, monkeypatch):
    """A needs-reindex project aborts the whole run — never a skip."""
    core, live, dead = _rig(tmp_path)

    def gate(entry):
        if entry.slug == dead.slug:
            raise FingerprintConfigError(
                "error: index for x does not match the current configuration "
                "(embed_model); run: code-indexer reindex-project x")

    monkeypatch.setattr(core, "assert_fingerprint_ok", gate)
    with pytest.raises(FingerprintConfigError):
        core.search("q", skip_refresh=True)


# ---------------------------------------------------------------------------
# list-projects: dead registration is visible
# ---------------------------------------------------------------------------

def test_list_projects_marks_missing_collection(tmp_path, monkeypatch):
    core, live, dead = _rig(tmp_path)
    _mark_indexed(core, live.slug)
    _mark_indexed(core, dead.slug)
    core.store = StubStore(collections={f"idx_{live.slug}"})  # type: ignore[assignment]
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["list-projects"])
    assert result.exit_code == 0, result.output
    lines = {ln.split("slug=")[1].split()[0]: ln
             for ln in result.output.splitlines() if "slug=" in ln}
    # The dead registration is an error, not a healthy idle.
    assert "state=error" in lines[dead.slug], lines[dead.slug]
    assert "collection=missing" in lines[dead.slug], lines[dead.slug]
    # The project whose collection exists stays idle — no false alarm.
    assert "state=idle" in lines[live.slug], lines[live.slug]
    assert "collection=missing" not in lines[live.slug]


def test_list_projects_survives_unavailable_listing(tmp_path, monkeypatch):
    """A store that cannot be listed must not fail the listing command."""
    core, live, dead = _rig(tmp_path)
    core.store = StubStore(listing_available=False)  # type: ignore[assignment]
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["list-projects"])
    assert result.exit_code == 0, result.output
    assert "collection=missing" not in result.output


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
