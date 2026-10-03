"""Concurrency and crash-resume tests (deliverable §4 of the CLI ruling).

1. flock concurrency: 4 parallel one-shot CLI invocations (searches that
   trigger the staleness probe) against ONE project must run exactly one
   indexer pass; the losers report 'indexing in progress'.
2. Incremental first-index resumability: a mid-crash (SIGKILL-equivalent:
   exception between embedding and manifest commit) leaves a manifest with
   partial rows, and the next pass completes idempotently — no duplicate
   embedding of already-committed chunk hashes, final state consistent.

No network: embedder/store are stubbed at the Core seam.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from code_indexer.config import Config
from code_indexer.core import Core
from code_indexer.locks import project_lock
from code_indexer.manifest import Manifest


class StubEmbedder:
    def __init__(self):
        self.embed_calls: list[list[str]] = []

    def dimension(self) -> int:
        return 4

    def embed(self, texts):
        self.embed_calls.append(list(texts))
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class StubStore:
    def __init__(self):
        self.upserted = 0

    def collection_exists(self, name):
        return True

    def create_collection(self, name, dim):
        pass

    def upsert_points(self, name, points):
        self.upserted += len(points)

    def purge_file_points(self, name, project, path, min_chunk_index=0):
        pass

    def search(self, name, vector, limit=8, file_filter=None,
               symbol_type=None, language=None):
        class _Hit:
            def __init__(self, payload, score):
                self.payload = payload
                self.score = score
        return [
            _Hit({"file": "app.py", "symbol": "main", "symbol_type": "function",
                  "start_line": 1, "end_line": 2, "snippet": "def main(): pass"},
                 0.99),
        ]


def _stub_core(cfg: Config) -> Core:
    """A Core with stubbed embedder/store (fresh instance per fake process)."""
    c = Core(cfg)
    c.embedder = StubEmbedder()
    c.store = StubStore()
    c.indexer.embedder = c.embedder
    c.indexer.store = c.store
    return c


@pytest.fixture()
def core(tmp_path, monkeypatch):
    """A Core wired to stubbed embedder/store and a throwaway index root."""
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("STALE_TTL", "0")  # always consider stale
    cfg = Config(
        ollama_url="http://stub", qdrant_url="http://stub", embed_model="stub",
        index_root=str(tmp_path / "state"), stale_ttl=0, embed_batch=48,
        upsert_batch=256, max_file_bytes=1048576, watch_debounce=3,
        watch_sweep_interval=300)
    yield _stub_core(cfg)


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text("def main():\n    pass\n")
    (root / "util.py").write_text("def helper():\n    pass\n")
    return root


def test_four_parallel_cli_searches_run_exactly_one_pass(core, project):
    """4 parallel one-shot search invocations -> exactly one indexer pass."""
    entry = core.registry.add(str(project))
    first = core.run_index(entry.slug, entry.path)
    assert first["state"] == "idle"

    # A fresh set of cores simulating 4 independent one-shot processes
    # (fresh staleness cache and registry connection each — the same way a
    # CLI subprocess would construct its own Core), all sharing the same
    # index root and lock file.
    cores = [_stub_core(core.cfg) for _ in range(4)]

    # Touch a file so the incremental pass has real work to (re)do.
    (project / "extra.py").write_text("def extra():\n    pass\n")

    results: list[dict] = []
    lock = threading.Lock()

    def worker(c: Core) -> None:
        r = c.maybe_refresh(entry.slug, entry.path)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker, args=(c,)) for c in cores]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    idle = [r for r in results if r["state"] == "idle"]
    held = [r for r in results if r["state"] == "indexing"]
    # Exactly one pass runs; everyone else reports 'indexing in progress'.
    assert len(idle) == 1, f"expected exactly one pass, got {len(idle)}: {results}"
    assert len(held) == 3
    assert all("indexing in progress" in r["detail"] for r in held)


def test_flock_reports_indexing_in_progress(tmp_path):
    """Direct lock test: a held flock makes a second acquire yield False."""
    lock_path = str(tmp_path / "p.lock")
    with project_lock(lock_path) as first:
        assert first is True
        with project_lock(lock_path) as second:
            assert second is False
    # After release, acquire works again.
    with project_lock(lock_path) as third:
        assert third is True


def test_lock_file_permanent_and_diagnostic(tmp_path):
    """Lock file survives release (permanent inode, never unlinked) and its
    content is pid + timestamp only (diagnostic)."""
    lock_path = tmp_path / "p.lock"
    with project_lock(str(lock_path)):
        assert lock_path.exists()
    assert lock_path.exists()  # permanent — no unlink on release
    content = lock_path.read_text()
    pid_str, ts = content.split(" ", 1)
    assert int(pid_str) == os.getpid()
    time.strptime(ts, "%Y-%m-%dT%H:%M:%S%z")


class CrashingEmbedder(StubEmbedder):
    """Succeeds for the first `ok_calls` single-text embed calls, then fails
    permanently — models a poisoned chunk / mid-pass Ollama death (CI-04)."""

    def __init__(self, batch_size: int = 1, ok_calls: int = 1):
        super().__init__()
        self.calls = 0
        self.batch_size = batch_size
        self.ok_calls = ok_calls

    def _one(self, texts):
        out = []
        for _ in texts:
            self.calls += 1
            if self.calls > self.ok_calls:
                raise RuntimeError("simulated crash mid-pass (SIGKILL equivalent)")
            out.append([0.1, 0.2, 0.3, 0.4])
        return out

    def embed(self, texts):
        return self._one(texts)

    def embed_with_errors(self, texts):
        vectors = []
        errors = []
        for i, text in enumerate(texts):
            try:
                vectors.append(self._one([text])[0])
            except Exception as exc:  # noqa: BLE001
                vectors.append(None)
                errors.append((i, str(exc)))
        return vectors, errors


def test_incremental_first_index_resumable_after_mid_crash(core, project):
    """A mid-pass embedder failure no longer aborts the pass (CI-04): the
    failing chunk is recorded in index_errors and the pass completes with
    the healthy chunks; the retry pass re-embeds only the failures (no
    duplicate points), and a third pass reuses everything (idempotent)."""
    entry = core.registry.add(str(project))

    crashing = CrashingEmbedder()
    core.embedder = crashing
    core.indexer.embedder = crashing
    # One embed call per chunk, so the crash lands between files.
    core.embedder.batch_size = 1

    # First pass: file 1's chunk embeds; file 2's chunk fails permanently.
    r1 = core.run_index(entry.slug, entry.path)
    assert r1["state"] == "idle"
    assert r1["result"]["chunks_embedded"] == 1
    assert r1["result"]["chunks_skipped"] == 1

    m = core.manifest_for(entry.slug)
    errs = m.last_index_errors(3)
    assert errs, "poisoned chunk recorded"
    m.close()

    # Recovery pass with a healthy embedder: only the failed chunk re-embeds.
    healthy = StubEmbedder()
    core.embedder = healthy
    core.indexer.embedder = healthy
    r2 = core.run_index(entry.slug, entry.path)
    assert r2["state"] == "idle"
    assert r2["result"]["chunks_embedded"] == 1
    # Deterministic point ids keep the upsert idempotent: no duplicate points.
    assert core.store.upserted == 2

    m = core.manifest_for(entry.slug)
    files = m.all_files()
    assert len(files) == 2
    assert m.get_meta("last_indexed") is not None
    m.close()

    # Third pass with no changes: everything reused, zero embeds (idempotent).
    before = len(healthy.embed_calls)
    r3 = core.run_index(entry.slug, entry.path)
    assert r3["state"] == "idle"
    assert r3["result"]["chunks_embedded"] == 0
    assert len(healthy.embed_calls) == before  # no new embed calls


def test_cli_search_json_contract(core, project):
    """CLI surface smoke: search_for_display returns the stable JSON fields."""
    entry = core.registry.add(str(project))
    core.run_index(entry.slug, entry.path)
    out = core.search_for_display("main", project=str(project), fmt="json")
    if out.startswith("error") or out == "no results":
        pytest.fail(f"unexpected search output: {out!r}")
    hits = json.loads(out)
    assert hits, "expected at least one hit"
    assert {"project", "file", "score", "symbol", "symbol_type",
            "start_line", "end_line", "snippet"} <= set(hits[0].keys())
