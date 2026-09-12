"""Concurrency smoke: two threads run the search/staleness path
(_maybe_refresh -> _run_index) concurrently on the same project.
Must not deadlock or corrupt; one wins the lock, other reports indexing."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ["INDEX_ROOT"] = tempfile.mkdtemp(prefix="mci-conc-")
from mcp_code_indexer.config import load_config
cfg = load_config()

from mcp_code_indexer.embedder import Embedder
from mcp_code_indexer.indexer import Indexer
from mcp_code_indexer.locks import project_lock
from mcp_code_indexer.manifest import Manifest
from mcp_code_indexer.registry import Registry
from mcp_code_indexer.store import Store

from e2e_helpers import make_repo  # noqa: E402


def _mdir(slug: str) -> str:
    d = os.path.join(cfg.index_root, slug)
    os.makedirs(d, exist_ok=True)
    return d


def _mfp(slug: str) -> str:
    return os.path.join(_mdir(slug), "manifest.db")


def _mpath(slug: str) -> str:
    return _mfp(slug)


def main() -> None:
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    embedder = Embedder(cfg.ollama_url, cfg.embed_model, batch_size=cfg.embed_batch)
    store = Store(cfg.qdrant_url, upsert_batch=cfg.upsert_batch)
    indexer = Indexer(cfg, embedder, store)

    repo = tempfile.mkdtemp(prefix="mci-conc-repo-")
    make_repo(repo)
    entry = reg.add(repo)
    slug = entry.slug

    def worker(n: int, results: list) -> None:
        # Simulate semantic_search's staleness path from two processes/threads.
        try:
            r = _search_flow(indexer, reg, slug, repo, embedder, store, f"database connection pool acquire {n}")
            results.append((n, r))
        except Exception as exc:  # noqa: BLE001
            results.append((n, f"ERROR: {exc}"))

    results: list = []
    # First: a full index in thread A while thread B tries the lock path too.
    t0 = time.time()
    threads = [threading.Thread(target=worker, args=(i, results)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    print(f"concurrent search flows done in {time.time()-t0:.1f}s")

    assert len(results) == 2, "both threads must finish (no deadlock)"
    for n, r in results:
        print(f"worker {n}: {r}")
    assert any("ERROR" not in str(r) for n, r in results)

    # Manifest intact after concurrent access.
    m = Manifest(_mpath(slug))
    files = m.all_files()
    m.close()
    assert len(files) == 4, f"manifest should have 4 files, has {len(files)}"
    print("manifest intact:", sorted(files))
    assert store.collection_exists(f"idx_{slug}")
    print("CONCURRENCY PASS")

    reg.close()
    shutil.rmtree(repo, ignore_errors=True)
    shutil.rmtree(os.environ["INDEX_ROOT"], ignore_errors=True)


def _search_flow(indexer, reg, slug, repo, embedder, store, query):
    """Mirror server.py's semantic_search: staleness refresh then search."""
    lock_path = os.path.join(cfg.index_root, f"{slug}.lock")
    with project_lock(lock_path) as acquired:
        if not acquired:
            return "state: indexing (lock held by other worker)"
        manifest = Manifest(_mfp(slug))
        try:
            r = indexer.index_project(repo, slug, manifest)
            vector = embedder.embed([query])[0]
            hits = store.search(f"idx_{slug}", vector, limit=3)
            return f"indexed={r.chunks_embedded} top={hits[0].payload['file'] if hits else None}"
        finally:
            manifest.close()


if __name__ == "__main__":
    main()