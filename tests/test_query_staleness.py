"""Query-path staleness tests: quiet by default, --refresh opt-in.

Covers solomon-review finding 3: the default staleness pass used to run a
mid-query re-index (latency + log noise). Now: within TTL it's a no-op;
--refresh forces a pass; --skip-stale-check stays a hard skip.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

REASONS: list[tuple] = []


@pytest.fixture()
def core():
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=tempfile.mkdtemp(prefix="ci-stale-"),
                 stale_ttl=60, embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=3,
                 watch_sweep_interval=300)
    core = Core(cfg)
    project = tempfile.mkdtemp(prefix="ci-stale-proj-")
    with open(os.path.join(project, "a.py"), "w") as f:
        f.write("def a():\n    return 1\n")
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(project, name="stale")
    yield core, entry
    reg.close()
    shutil.rmtree(cfg.index_root, ignore_errors=True)
    shutil.rmtree(project, ignore_errors=True)


@pytest.fixture()
def pass_spy():
    """Replace Core.run_index with a counting spy (no network)."""
    def install(core_obj: Core, verdict: dict[str, str] | None = None):
        calls: list[tuple] = []

        def spy(slug, path, force=False, verbose=False):
            calls.append((slug, force, verbose))
            if verdict is None:
                return {"state": "idle",
                        "result": {"files_indexed": 0, "chunks_embedded": 0}}
            return verdict

        core_obj.run_index = spy
        return calls
    return install


def test_within_ttl_no_index_pass(core, pass_spy):
    """Default query path: inside the TTL there is no re-index at all."""
    c, entry = core
    calls = pass_spy(c)
    c._last_scan[entry.slug] = time.monotonic()
    result = c.maybe_refresh(entry.slug, entry.path)
    assert result["state"] == "fresh"
    assert not calls, "staleness probe triggered an index pass inside TTL"


def test_after_ttl_one_quiet_pass(core, pass_spy):
    """Outside the TTL the pass runs, but marked quiet (verbose=False)."""
    c, entry = core
    calls = pass_spy(c)
    c._last_scan[entry.slug] = time.monotonic() - c.cfg.stale_ttl - 1
    result = c.maybe_refresh(entry.slug, entry.path)
    assert result["state"] == "idle"
    assert calls and calls[0][2] is False, "query-path pass was not quiet"


def test_refresh_flag_forces_pass(core, pass_spy):
    c, entry = core
    calls = pass_spy(c)
    result = c.maybe_refresh(entry.slug, entry.path, force=True)
    assert result["state"] == "idle"
    assert calls, "--refresh must force a pass even inside the TTL"
    assert calls[0][2] is True, "--refresh pass must be verbose"


def test_skip_stale_check_still_hard_skips(core, pass_spy):
    c, entry = core
    calls = pass_spy(c)
    cfg = c.cfg
    c2 = Core(cfg, skip_stale_check=True)
    c2.run_index = lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("skip_stale_check must never index"))
    r = c2.maybe_refresh(entry.slug, entry.path, force=True)
    assert r["state"] == "fresh"
    assert not calls
