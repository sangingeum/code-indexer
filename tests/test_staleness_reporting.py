"""Issue #13: fingerprint-state reporting.

Two state vocabularies must not be confusable: the PASS state
(``idle | indexing | never-indexed | error``) and the FINGERPRINT state
(``ok | needs-reindex``). ``index-status`` reported only the pass state, so a
project whose index no longer matched the running configuration printed a plain
``state=idle`` while ``list-projects``/``doctor`` said ``needs-reindex`` — two
readers, two answers. This file pins the fix:

1. ``index-status`` surfaces the fingerprint state on a mismatched project and
   prints nothing extra on a matching one.
2. the manifest-only surfaces (``outline`` is the deterministic representative)
   print ONE stderr warning when the stored rows may predate the current
   extraction code, and none on a match.
3. the warning deliberately ignores the embedding-configuration keys
   (``embed_model`` / ``embed_dim`` / ``embed_text_version``) — those affect
   vectors, not stored rows.
4. ``search`` stays fatal on a mismatch (unchanged behaviour).

All assertions are revert-sensitive: they pin the exact field and strings, so
deleting the code makes them fail.
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
from code_indexer.core import Core  # noqa: E402
from code_indexer.fingerprint import (ConfigError, Fingerprint,  # noqa: E402
                                      rows_staleness_warning)
from code_indexer.graph_extract import EXTRACTION_VERSION  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()


class StubEmbedder:
    def __init__(self, dim: int = 4):
        self.dim = dim

    def dimension(self) -> int:
        return self.dim

    def embed(self, texts):
        return [[0.1] * self.dim for _ in texts]


class StubStore:
    def __init__(self) -> None:
        self.collections: set[str] = set()
        self.aliases: dict[str, str] = {}

    def collection_exists(self, name: str) -> bool:
        return name in self.collections or name in self.aliases

    def create_collection(self, name: str, dim: int) -> None:
        self.collections.add(name)

    def drop_collection(self, name: str) -> None:
        self.collections.discard(name)
        self.aliases.pop(name, None)

    def upsert_points(self, name: str, points: list) -> int:
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


def _wire(cfg: Config, registry: Registry | None = None) -> Core:
    core = Core(cfg)
    stub = StubEmbedder()
    core.embedder = stub  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = stub  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    if registry is not None:
        core.registry = registry
    return core


@pytest.fixture()
def rig():
    """An indexed project whose manifest records a fingerprint."""
    tmp = tempfile.mkdtemp(prefix="ci-stale-")
    project = tempfile.mkdtemp(prefix="ci-stale-proj-")
    with open(os.path.join(project, "a.py"), "w") as f:
        f.write("def a():\n    return 1\n\n\ndef b():\n    return a()\n")
    reg = Registry(os.path.join(tmp, "registry.db"))
    entry = reg.add(project, name="staleproj")
    core = _wire(_config(tmp), registry=reg)
    result = core.run_index(entry.slug, project)
    assert result["state"] == "idle"
    yield core, reg, entry, project
    reg.close()
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(project, ignore_errors=True)


def _set_extraction_version(core: Core, slug: str, value: int) -> None:
    m = core.manifest_for(slug)
    try:
        m.set_meta("extraction_version", str(value))
    finally:
        m.close()


def _text(result) -> str:
    """The combined stream, deduped.

    click's CliRunner has changed how it reports stderr across versions:
    recent versions put the interleaved stream in ``result.output`` AND expose
    ``result.stderr``, so a naive concatenation double-counts a warning line.
    """
    out = result.output
    err = getattr(result, "stderr", "") or ""
    if err and err not in out:
        out += err
    return out


# ---------------------------------------------------------------------------
# the fingerprint vocabulary (unit)
# ---------------------------------------------------------------------------

def test_rows_warning_compares_only_version_ordered_keys():
    """A model/dimension difference is NOT a stored-row staleness: those keys
    describe vectors, not rows. The asymmetry is deliberate."""
    recorded = Fingerprint("stub", 4, "contextual-header-v1", 1,
                           extraction_version=2)
    # version keys equal, embedding keys differ -> no warning
    model_changed = Fingerprint("other", 8, "different-format", 1,
                                extraction_version=2)
    assert rows_staleness_warning(recorded, model_changed) is None
    # extraction version older -> warn, naming both values
    older = Fingerprint("stub", 4, "contextual-header-v1", 1,
                        extraction_version=3)
    warning = rows_staleness_warning(recorded, older)
    assert warning is not None
    assert "extraction_version: 2 -> 3" in warning
    assert "older extraction code" in warning
    # a match -> no warning
    assert rows_staleness_warning(recorded, recorded) is None
    # no recorded fingerprint -> nothing to warn about
    assert rows_staleness_warning(None, older) is None


# ---------------------------------------------------------------------------
# index-status surfaces the fingerprint state
# ---------------------------------------------------------------------------

def test_index_status_reports_the_fingerprint_state(rig, monkeypatch):
    core, reg, entry, project = rig
    # Simulate an index built by older extraction code: the recorded value is
    # below this process's own, so list-projects/doctor call it needs-reindex.
    _set_extraction_version(core, entry.slug, EXTRACTION_VERSION - 1)
    assert "state=needs-reindex" in core.status_summary(entry)

    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["index-status", str(project)])
    assert result.exit_code == 0, result.output
    text = _text(result)
    # the pass state is still idle (no pass is running) …
    assert "state=idle" in text
    # … but the rebuild is no longer hidden behind it.
    assert "fingerprint=needs-reindex" in text
    assert f"reason=extraction_version: {EXTRACTION_VERSION - 1} -> " \
           f"{EXTRACTION_VERSION}" in text


def test_index_status_quiet_on_a_matching_project(rig, monkeypatch):
    core, reg, entry, project = rig
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["index-status", str(project)])
    assert result.exit_code == 0, result.output
    text = _text(result)
    assert "state=idle" in text
    assert "fingerprint=needs-reindex" not in text


# ---------------------------------------------------------------------------
# the manifest-only surfaces warn once, and never refuse
# ---------------------------------------------------------------------------

def test_manifest_only_surface_warns_once_on_a_mismatch(rig, monkeypatch):
    core, reg, entry, project = rig
    _set_extraction_version(core, entry.slug, EXTRACTION_VERSION - 1)
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(
        app, ["outline", "a.py", "--project", str(project)])
    assert result.exit_code == 0, result.output  # a warning, never fatal
    warnings = [ln for ln in _text(result).splitlines()
                if ln.startswith("warning: stored rows were produced by")]
    assert len(warnings) == 1, warnings
    assert "older extraction code" in warnings[0]
    assert f"extraction_version: {EXTRACTION_VERSION - 1} -> " \
           f"{EXTRACTION_VERSION}" in warnings[0]


def test_manifest_only_surface_quiet_on_a_match(rig, monkeypatch):
    core, reg, entry, project = rig
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(
        app, ["outline", "a.py", "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert "stored rows were produced by" not in _text(result)


def test_warning_ignores_an_embed_model_change(rig):
    """A model change is a needs-reindex mismatch (search refuses) but it is
    NOT a stored-row staleness — the manifest-only surfaces stay quiet."""
    core, reg, entry, project = rig
    assert core.rows_staleness(entry) is None
    cfg2 = _config(core.cfg.index_root, embed_model="other-model")
    core2 = _wire(cfg2, registry=reg)
    # The mismatch is real for the query path …
    assert "state=needs-reindex" in core2.status_summary(entry)
    # … but tells the manifest-only surfaces nothing about their rows.
    assert core2.rows_staleness(entry) is None


# ---------------------------------------------------------------------------
# search stays fatal (unchanged behaviour)
# ---------------------------------------------------------------------------

def test_search_still_refuses_on_a_mismatch(rig):
    core, reg, entry, project = rig
    _set_extraction_version(core, entry.slug, EXTRACTION_VERSION - 1)
    with pytest.raises(ConfigError, match="reindex-project"):
        core.search("a", project=entry.path, skip_refresh=True)