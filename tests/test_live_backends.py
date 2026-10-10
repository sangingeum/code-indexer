"""Live-backend smoke tests (marker: live; excluded from default runs).

Run with `pytest -m live` when Ollama + Qdrant are up. Kept minimal: one
handshake-style assertion per backend plus one end-to-end index+search —
enough to catch backend drift without duplicating the offline suite.

The end-to-end test REGISTERS a project in the operator's real registry, so
it must deregister it again (try/finally, on failure too): a leaked
registration whose collection is later cleaned away makes every
project-less search skip or fail on a project nobody indexed.
"""

from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.config import load_config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.embedder import Embedder  # noqa: E402


def _backends_configured() -> bool:
    cfg = load_config()
    return bool(cfg.ollama_url) and bool(cfg.qdrant_url)


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not _backends_configured(),
                       reason="ollama/qdrant not configured"),
]


def _cleanup_registration(core: Core, entry) -> None:
    """Drop this test's collection + registry entry + state dir (idempotent).

    Best-effort by design: cleanup failures must not mask the test's own
    assertion failure, and a partially-removed project is still better than
    a leaked registration.
    """
    collection = f"idx_{entry.slug}"
    try:
        if core.store.collection_exists(collection):
            core.store.drop_collection(collection)
    except Exception:  # noqa: BLE001 — cleanup is best-effort
        pass
    try:
        core.registry.remove(entry.path)
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(os.path.join(core.cfg.index_root, entry.slug),
                  ignore_errors=True)


def test_ollama_embed():
    cfg = load_config()
    emb = Embedder(cfg.ollama_url, cfg.embed_model)
    vec = emb.embed(["hello"])[0]
    assert len(vec) == emb.dimension() > 0


def test_end_to_end_index_and_search(tmp_path):
    cfg = load_config()
    project = tmp_path / "liveproj"
    project.mkdir()
    (project / "m.py").write_text(
        "def staleness_probe(slug):\n"
        "    '''Refresh check for one collection.'''\n"
        "    return slug\n")
    import uuid

    core = Core(cfg)
    entry = core.registry.add(str(project), name=f"live-{uuid.uuid4().hex[:8]}")
    try:
        result = core.run_index(entry.slug, str(project))
        assert result["state"] == "idle", result
        out = core.search_for_display("staleness probe", project=str(project),
                                      fmt="compact")
        assert "staleness_probe" in out or "m.py" in out
    finally:
        _cleanup_registration(core, entry)
