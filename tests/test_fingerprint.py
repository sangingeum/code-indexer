"""Index fingerprint (v4) tests: mismatch protection, backfill, swap rebuild.

Mocked embedder/store seams; no Ollama/Qdrant. The acceptance triple from the
improvement plan: EMBED_MODEL change -> ConfigError on query paths; a swap
rebuild clears the mismatch; a legacy (fingerprint-less) manifest migrates
without forcing a reindex.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.fingerprint import (ConfigError, Fingerprint,  # noqa: E402
                                      current_fingerprint,
                                      fingerprint_mismatches,
                                      read_fingerprint, write_fingerprint)
from code_indexer.manifest import Manifest  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402


class StubEmbedder:
    def __init__(self, dim: int = 4):
        self.dim = dim

    def dimension(self) -> int:
        return self.dim

    def embed(self, texts):
        return [[0.1] * self.dim for _ in texts]


class StubStore:
    """Records collection ops so the swap sequence can be asserted."""

    def __init__(self) -> None:
        self.collections: set[str] = set()
        self.aliases: dict[str, str] = {}
        self.dropped: list[str] = []
        self.upserts: list[tuple[str, int]] = []

    def collection_exists(self, name: str) -> bool:
        return name in self.collections or name in self.aliases

    def create_collection(self, name: str, dim: int) -> None:
        self.collections.add(name)

    def drop_collection(self, name: str) -> None:
        self.dropped.append(name)
        self.collections.discard(name)
        self.aliases.pop(name, None)

    def physical_name(self, name: str) -> str | None:
        if name in self.aliases:
            return self.aliases[name]
        return name if name in self.collections else None

    def _alias_exists(self, alias_name: str) -> bool:
        return alias_name in self.aliases

    def swap_collection(self, tmp: str, target: str) -> None:
        old = self.physical_name(target)
        if old == tmp:
            return
        if old == target and not self._alias_exists(target):
            # target is a REAL collection: dropped so the name is free
            # (mirrors Store.swap_collection).
            self.collections.discard(target)
            self.dropped.append(target)
        self.aliases[target] = tmp
        if old is not None and old != target and old != tmp:
            self.dropped.append(old)
            self.collections.discard(old)

    def upsert_points(self, name: str, points: list) -> int:
        self.upserts.append((name, len(points)))
        return len(points)

    def purge_file_points(self, *a, **kw) -> int:
        return 0

    def count_points(self, name: str) -> int:
        return 0

    def search(self, name: str, vector: list[float], limit: int = 8,
               file_filter=None, symbol_type=None, language=None):
        return []


def _config(tmp: str, embed_model: str = "stub-model") -> Config:
    return Config(ollama_url="", qdrant_url="", embed_model=embed_model,
                  index_root=tmp, stale_ttl=60, embed_batch=48,
                  upsert_batch=256, max_file_bytes=1048576,
                  watch_debounce=3, watch_sweep_interval=300)


@pytest.fixture()
def rig():
    tmp = tempfile.mkdtemp(prefix="ci-fp-")
    project = tempfile.mkdtemp(prefix="ci-fp-proj-")
    with open(os.path.join(project, "a.py"), "w") as f:
        f.write("def a():\n    return 1\n")
    store = StubStore()
    cfg = _config(tmp)
    core = Core(cfg)
    stub = StubEmbedder()
    core.embedder = stub  # type: ignore[assignment]
    core.store = store  # type: ignore[assignment]
    core.indexer.embedder = stub  # type: ignore[assignment]
    core.indexer.store = store  # type: ignore[assignment]
    reg = Registry(os.path.join(tmp, "registry.db"))
    entry = reg.add(project, name="fpproj")
    yield core, reg, entry, project, store
    reg.close()
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(project, ignore_errors=True)


# ---------------------------------------------------------------------------
# fingerprint unit behavior
# ---------------------------------------------------------------------------

def test_fingerprint_roundtrip_and_mismatch_detection():
    fp = Fingerprint("m", 4096, "contextual-header-v1", 1)
    assert fp.describe() == (
        "m/4096/etext=contextual-header-v1/chunker=1/extr=1")
    assert fingerprint_mismatches(fp, fp) == []
    other = Fingerprint("other", 4096, "contextual-header-v1", 1)
    diffs = fingerprint_mismatches(fp, other)
    assert diffs == ["embed_model: m -> other"]
    same_dim_other_model = fingerprint_mismatches(
        fp, Fingerprint("other", 4096, "contextual-header-v1", 1))
    assert same_dim_other_model  # the CI-02 core case: 4096-dim vs 4096-dim
    graph_bump = fingerprint_mismatches(
        fp, Fingerprint("m", 4096, "contextual-header-v1", 1,
                        extraction_version=2))
    assert graph_bump == ["extraction_version: 1 -> 2"]


def test_read_fingerprint_tolerates_blank_and_partial(tmp_path):
    m = Manifest(str(tmp_path / "m.db"))
    try:
        assert read_fingerprint(m) is None  # fresh manifest
        m.set_meta("embed_model", "stub")
        m.set_meta("embed_dim", "")
        assert read_fingerprint(m) is None  # partial -> unknown, not a crash
        m.set_meta("embed_dim", "not-an-int")
        assert read_fingerprint(m) is None
    finally:
        m.close()


# ---------------------------------------------------------------------------
# the acceptance triple
# ---------------------------------------------------------------------------

def test_fingerprint_recorded_after_a_pass(rig):
    core, reg, entry, project, store = rig
    result = core.run_index(entry.slug, project)
    assert result["state"] == "idle"
    m = core.manifest_for(entry.slug)
    try:
        fp = read_fingerprint(m)
        assert fp is not None
        assert fp.embed_model == "stub-model"
        assert fp.embed_dim == 4
        assert fp.chunker_version >= 1
    finally:
        m.close()
    assert core.fingerprint_status(entry)["ok"]


def test_model_change_config_error_and_reindex_fixes_it(rig):
    core, reg, entry, project, store = rig
    core.run_index(entry.slug, project)

    # Simulate EMBED_MODEL change: a new core with a different model config
    # (same dimension — the dangerous case) over the same index root.
    cfg2 = _config(core.cfg.index_root, embed_model="other-model")
    core2 = Core(cfg2)
    stub2 = StubEmbedder()
    core2.embedder = stub2  # type: ignore[assignment]
    core2.store = store  # type: ignore[assignment]
    core2.indexer.embedder = stub2  # type: ignore[assignment]
    core2.indexer.store = store  # type: ignore[assignment]
    core2.registry = core.registry  # same registry file

    # status_summary reports needs-reindex with a reason
    summary = core2.status_summary(entry)
    assert "state=needs-reindex" in summary
    assert "embed_model" in summary

    # search refuses with ConfigError, even with skip_refresh (the
    # --skip-stale-check path must NOT bypass the fingerprint gate)
    with pytest.raises(ConfigError, match="other-model"):
        core2.search("x", project=entry.path, skip_refresh=True)

    # reindex (swap rebuild) clears the mismatch
    result = core2.reindex_with_swap(entry.slug, entry.path)
    assert result["state"] == "idle"
    summary_after = core2.status_summary(entry)
    assert "needs-reindex" not in summary_after
    # search works again
    hits = core2.search("x", project=entry.path, skip_refresh=True)["hits"]
    assert isinstance(hits, list)
    # the swap: the live name is an alias to the temp build, old dropped
    assert store.aliases.get(f"idx_{entry.slug}") == f"idx_{entry.slug}__new"
    assert f"idx_{entry.slug}" in store.dropped


def test_legacy_manifest_backfills_without_reindex(rig):
    core, reg, entry, project, store = rig
    core.run_index(entry.slug, project)  # fingerprint now recorded
    # Wipe the fingerprint keys to simulate a pre-v4 manifest.
    m = core.manifest_for(entry.slug)
    try:
        for key in ("embed_model", "embed_dim", "embed_text_version",
                    "chunker_version"):
            m.set_meta(key, "")
    finally:
        m.close()
    # A read-only query path must NOT raise (unknown fingerprint is not a
    # mismatch)…
    core.assert_fingerprint_ok(entry)
    assert core.fingerprint_status(entry)["ok"]
    # …and the next indexing pass backfills the current config without
    # treating the project as mismatched.
    result = core.run_index(entry.slug, project)
    assert result["state"] == "idle"
    m = core.manifest_for(entry.slug)
    try:
        fp = read_fingerprint(m)
        assert fp is not None and fp.embed_model == "stub-model"
    finally:
        m.close()