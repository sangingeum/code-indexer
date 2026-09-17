"""lookup_project tool: registered path, unregistered path, path
normalization variants (tilde, relative, trailing slash, symlink).

Imports the server module with INDEX_ROOT pointed at a tmp dir so the
module-level CFG/REGISTRY bind to throwaway state. No Ollama/Qdrant
traffic is needed (Embedder/Store construction is lazy).
"""

from __future__ import annotations

import os
import tempfile

import pytest

# Bind config to throwaway state before importing the server module.
os.environ["INDEX_ROOT"] = tempfile.mkdtemp(prefix="mci-lookup-test-")

from code_indexer import server  # noqa: E402
from code_indexer.registry import Registry, sanitize_name  # noqa: E402


@pytest.fixture()
def reg(tmp_path, monkeypatch):
    """Bind the server's global CORE to a registry on tmp_path."""
    from code_indexer.core import Core
    from code_indexer.config import Config
    cfg = Config(
        ollama_url="http://stub", qdrant_url="http://stub", embed_model="stub",
        index_root=str(tmp_path / "state"), stale_ttl=60, embed_batch=48,
        upsert_batch=256, max_file_bytes=1048576, watch_debounce=3)
    c = Core(cfg)
    monkeypatch.setattr(server, "CORE", c)
    yield c.registry
    c.registry.close()


@pytest.fixture()
def proj(tmp_path):
    d = tmp_path / "myproj"
    d.mkdir()
    return d


def _register(r: Registry, path, name=None):
    return r.add(str(path), name=name)


def test_registered_path_reports_summary(reg, proj):
    e = _register(reg, proj)
    out = server.lookup_project(str(proj))
    assert out.startswith("registered")
    assert f"path={proj}" in out
    assert f"slug={e.slug}" in out
    assert f"collection=idx_{e.slug}" in out
    assert "files=" in out and "chunks=" in out and "last_indexed=" in out
    assert "state=" in out


def test_registered_custom_name_shown(reg, proj):
    e = _register(reg, proj, name="vivarium")
    out = server.lookup_project(str(proj))
    assert "name=vivarium" in out
    assert f"collection=idx_{e.slug}" in out  # idx_vivarium


def test_unregistered_path(reg, tmp_path):
    out = server.lookup_project(str(tmp_path / "nope"))
    assert out.startswith("not registered:")
    assert str(tmp_path / "nope") in out


def test_path_normalization_trailing_slash_and_relative(reg, proj, monkeypatch):
    _register(reg, proj)
    # Trailing slash and relative form must resolve to the same entry.
    assert server.lookup_project(str(proj) + "/").startswith("registered")
    monkeypatch.chdir(proj.parent)
    assert server.lookup_project("myproj").startswith("registered")


def test_path_normalization_symlink(reg, proj, tmp_path):
    _register(reg, proj)
    link = tmp_path / "link-to-proj"
    link.symlink_to(proj)
    assert server.lookup_project(str(link)).startswith("registered")


def test_unregistered_path_not_registered_by_lookup(reg, proj):
    """lookup must be side-effect free: no registry write, no index."""
    out = server.lookup_project(str(proj))
    assert out.startswith("not registered:")
    assert reg.get_by_path(str(proj)) is None