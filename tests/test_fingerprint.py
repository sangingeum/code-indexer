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
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import (UNVERIFIED, UNVERIFIED_REASON,  # noqa: E402
                               Core)
from code_indexer.fingerprint import (ConfigError, Fingerprint,  # noqa: E402
                                      fingerprint_mismatches,
                                      manifest_newer_than,
                                      read_fingerprint)
from code_indexer.graph_extract import EXTRACTION_VERSION  # noqa: E402
from code_indexer.manifest import Manifest  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402


class StubEmbedder:
    def __init__(self, dim: int = 4):
        self.dim = dim

    def dimension(self) -> int:
        return self.dim

    def embed(self, texts):
        return [[0.1] * self.dim for _ in texts]


class UnreachableEmbedder(StubEmbedder):
    """An embedder whose dimension probe fails (Ollama unreachable)."""

    def dimension(self) -> int:
        raise RuntimeError("ollama unreachable")


runner = CliRunner()


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
    # Pin the extraction version explicitly (rather than the module default)
    # so this test describes the MECHANISM and does not need an edit on every
    # EXTRACTION_VERSION bump.
    fp = Fingerprint("m", 4096, "contextual-header-v1", 1, extraction_version=2)
    assert fp.describe() == (
        "m/4096/etext=contextual-header-v1/chunker=1/extr=2")
    assert fingerprint_mismatches(fp, fp) == []
    other = Fingerprint("other", 4096, "contextual-header-v1", 1,
                        extraction_version=2)
    diffs = fingerprint_mismatches(fp, other)
    assert diffs == ["embed_model: m -> other"]
    same_dim_other_model = fingerprint_mismatches(
        fp, Fingerprint("other", 4096, "contextual-header-v1", 1,
                        extraction_version=2))
    assert same_dim_other_model  # the CI-02 core case: 4096-dim vs 4096-dim
    graph_bump = fingerprint_mismatches(
        fp, Fingerprint("m", 4096, "contextual-header-v1", 1,
                        extraction_version=3))
    assert graph_bump == ["extraction_version: 2 -> 3"]


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
        # The extraction version round-trips, so bumping it flags a rebuild.
        assert fp.extraction_version == EXTRACTION_VERSION
    finally:
        m.close()
    assert core.fingerprint_status(entry)["ok"]


def test_recorded_extraction_version_is_read_back_and_flags_a_rebuild(tmp_path):
    m = Manifest(str(tmp_path / "m.db"))
    try:
        for key, value in (("embed_model", "stub"), ("embed_dim", "4"),
                           ("embed_text_version", "contextual-header-v1"),
                           ("chunker_version", "1"),
                           ("extraction_version", "1")):
            m.set_meta(key, value)
        fp = read_fingerprint(m)
        assert fp is not None and fp.extraction_version == 1
        # An index extracted by the previous version is a mismatch.
        assert fingerprint_mismatches(
            fp, Fingerprint("stub", 4, "contextual-header-v1", 1)
        ) == [f"extraction_version: 1 -> {EXTRACTION_VERSION}"]
    finally:
        m.close()


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


# ---------------------------------------------------------------------------
# direction-aware fingerprint guard (issue #10 item 3)
# ---------------------------------------------------------------------------

def test_manifest_newer_than_is_directional():
    """Only a version-ordered key recorded HIGHER says 'newer code'; a model
    or dimension difference is a plain mismatch, not a version ordering."""
    cur = Fingerprint("m", 4, "contextual-header-v1", 1, extraction_version=3)
    older = Fingerprint("m", 4, "contextual-header-v1", 1, extraction_version=1)
    newer = Fingerprint("m", 4, "contextual-header-v1", 1, extraction_version=4)
    assert manifest_newer_than(older, cur) is False   # upgrade path
    assert manifest_newer_than(cur, cur) is False
    assert manifest_newer_than(newer, cur) is True    # beyond this process
    assert manifest_newer_than(None, cur) is False
    # chunker_version counts too
    assert manifest_newer_than(
        Fingerprint("m", 4, "contextual-header-v1", 5, extraction_version=3),
        cur) is True
    # a model/dim difference is NOT a version ordering
    assert manifest_newer_than(
        Fingerprint("other", 8, "contextual-header-v1", 1,
                    extraction_version=3), cur) is False


def _recorded_extraction_version(core, entry) -> int | None:
    m = core.manifest_for(entry.slug)
    try:
        fp = read_fingerprint(m)
        return fp.extraction_version if fp is not None else None
    finally:
        m.close()


def test_older_manifest_rebuilds_and_restamps(rig):
    """Direction 1 (legitimate upgrade): a manifest built by OLDER code is
    rebuilt by the newer process and re-stamped to the current version.
    MEASURED — real re-embedding happens, it is not merely asserted."""
    core, reg, entry, project, store = rig
    core.run_index(entry.slug, project)
    m = core.manifest_for(entry.slug)
    m.set_meta("extraction_version", str(EXTRACTION_VERSION - 1))
    m.close()
    store.upserts.clear()
    result = core.run_index(entry.slug, project)
    assert result["state"] == "idle"
    assert result["result"]["chunks_embedded"] > 0, "upgrade path must re-embed"
    assert sum(n for _, n in store.upserts) > 0
    assert _recorded_extraction_version(core, entry) == EXTRACTION_VERSION


def test_newer_manifest_defers_the_restamp_and_does_not_reembed(rig, caplog):
    """Direction 2 (the failure): a manifest built by NEWER code must not be
    re-embedded by this older process, nor re-stamped with the older version.
    MEASURED — zero chunks embedded, zero upserts, recorded value untouched —
    and the warning names the project, both fingerprints and the remedy."""
    import logging

    core, reg, entry, project, store = rig
    core.run_index(entry.slug, project)
    newer = EXTRACTION_VERSION + 1
    m = core.manifest_for(entry.slug)
    m.set_meta("extraction_version", str(newer))
    m.close()
    store.upserts.clear()
    with caplog.at_level(logging.WARNING, logger="code-indexer.indexer"):
        result = core.run_index(entry.slug, project)
    assert result["state"] == "idle"
    # NOT re-embedded: the direction-blind path would have set rebuild=True
    # and re-embedded the whole project with this process's older code.
    assert result["result"]["chunks_embedded"] == 0
    assert sum(n for _, n in store.upserts) == 0
    # The newer recorded value is left alone — our older version is not stamped.
    assert _recorded_extraction_version(core, entry) == newer
    text = caplog.text
    assert entry.slug in text
    assert f"extr={newer}" in text          # the recorded (newer) fingerprint
    assert f"extr={EXTRACTION_VERSION}" in text  # this process's own
    assert "NEWER code" in text
    assert "unwatch" in text and "reindex-project" in text


# ---------------------------------------------------------------------------
# the unverifiable verdict (issue #18)
# ---------------------------------------------------------------------------

def test_unreachable_embedder_makes_the_verdict_unverified(rig):
    """A verdict that cannot be reached is its own value, never a pass.

    Reaching the verdict means probing the running configuration (the
    embedder dimension). With the embedder unreachable there is no way to
    compare the recorded fingerprint against the running one: that is
    `unverified`, and `ok` keeps its meaning ("no KNOWN mismatch") so the
    query-path gate refuses nothing new — the surfaces that need the
    embedder fail on their own.
    """
    core, reg, entry, project, store = rig
    core.run_index(entry.slug, project)      # a real recorded fingerprint
    assert core.fingerprint_status(entry)["state"] == "ok"

    core.embedder = UnreachableEmbedder()    # type: ignore[assignment]
    status = core.fingerprint_status(entry)
    assert status["state"] == UNVERIFIED
    assert status["reason"] == UNVERIFIED_REASON
    assert status["ok"] is True
    assert status["recorded"] is not None
    core.assert_fingerprint_ok(entry)        # unchanged: no new refusal


def test_index_status_prints_the_unverified_verdict(rig, monkeypatch):
    """`index-status` says "cannot verify" instead of saying nothing.

    The field carries the reason, so a missing `fingerprint=` field can only
    mean a verified match.
    """
    core, reg, entry, project, store = rig
    core.run_index(entry.slug, project)
    monkeypatch.setattr(cli, "_core", core)
    monkeypatch.setattr(cli, "_core_skip", False)

    verified = runner.invoke(app, ["index-status", entry.path])
    assert verified.exit_code == 0, verified.output
    assert "fingerprint=" not in verified.output   # matched: nothing to say
    assert "state=idle" in verified.output

    core.embedder = UnreachableEmbedder()  # type: ignore[assignment]
    unverified = runner.invoke(app, ["index-status", entry.path])
    assert unverified.exit_code == 0, unverified.output
    assert "fingerprint=unverified reason=embedder-unreachable" in \
        unverified.output
    assert "needs-reindex" not in unverified.output