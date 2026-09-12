"""E2E test on a REAL throwaway repo: add_project -> index -> search ->
edit/delete/add -> search reflects changes -> remove_project -> everything
gone. Uses the MCP server's internal machinery directly (same code path the
stdio tools run) plus a real stdio handshake probe separately."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ["INDEX_ROOT"] = tempfile.mkdtemp(prefix="mci-e2e-")
# Rebind config/state after env set (server.py reads at import).
from mcp_code_indexer.config import load_config
cfg = load_config()

from mcp_code_indexer.embedder import Embedder
from mcp_code_indexer.indexer import Indexer
from mcp_code_indexer.locks import project_lock
from mcp_code_indexer.manifest import Manifest
from mcp_code_indexer.registry import Registry, slug_for
from mcp_code_indexer.store import Store

QDRANT = cfg.qdrant_url


def wait_for(condition, timeout_s, what):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if condition():
            return True
        time.sleep(5)
    raise TimeoutError(f"timed out waiting for {what}")


def _ensure_manifest_dir(slug: str) -> str:
    d = os.path.join(cfg.index_root, slug)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "manifest.db")


def make_repo(root: str) -> None:
    os.makedirs(os.path.join(root, "src"))
    with open(os.path.join(root, "src", "auth.py"), "w") as f:
        f.write(
            "class Authenticator:\n"
            "    \"\"\"Handles user login sessions and token validation.\"\"\"\n\n"
            "    def login(self, user, password):\n"
            "        \"\"\"Verify credentials and create a session.\"\"\"\n"
            "        if not user or not password:\n"
            "            raise ValueError('missing credentials')\n"
            "        token = self._issue_token(user)\n"
            "        return token\n\n"
            "    def _issue_token(self, user):\n"
            "        return f'tok-{user}'\n"
        )
    with open(os.path.join(root, "src", "payments.py"), "w") as f:
        f.write(
            "class Invoice:\n"
            "    \"\"\"Billing document for purchased subscriptions.\"\"\"\n\n"
            "    def total_cents(self):\n"
            "        return sum(l.amount_cents for l in self.lines)\n"
        )
    with open(os.path.join(root, "README.md"), "w") as f:
        f.write("# Throwaway repo\n\nUser authentication and billing docs.\n")
    with open(os.path.join(root, "junk.log"), "w") as f:
        f.write("noise\n" * 50)


def main() -> None:
    from mcp.server.fastmcp import FastMCP  # noqa: F401 — prove server deps importable
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    embedder = Embedder(cfg.ollama_url, cfg.embed_model, batch_size=cfg.embed_batch)
    store = Store(QDRANT, upsert_batch=cfg.upsert_batch)
    indexer = Indexer(cfg, embedder, store)

    repo = tempfile.mkdtemp(prefix="mci-repo-")
    make_repo(repo)
    print(f"repo: {repo}")

    # --- add_project equivalent ---
    entry = reg.add(repo)
    slug = entry.slug
    collection = f"idx_{slug}"
    manifest = Manifest(_ensure_manifest_dir(slug))
    lock_path = os.path.join(cfg.index_root, f"{slug}.lock")
    with project_lock(lock_path) as ok:
        assert ok, "lock should be free"
        r1 = indexer.index_project(repo, slug, manifest)
    print("initial index:", r1)
    assert store.collection_exists(collection), "collection must exist"
    count0 = store.count_points(collection)
    print("points after initial:", count0)

    # --- search: payments vs auth ---
    hits = store.search(collection, embedder.embed(["user login authentication session token"])[0], limit=3)
    top_auth = next(h for h in hits if h.payload["file"] == "src/auth.py")
    print("search 'login auth':", hits[0].payload["file"], f"score={hits[0].score:.4f}",
          f"lines {hits[0].payload['start_line']}-{hits[0].payload['end_line']}")
    assert hits[0].payload["file"] == "src/auth.py"

    hits2 = store.search(collection, embedder.embed(["billing invoice amount total"])[0], limit=3)
    print("search 'billing':", hits2[0].payload["file"], f"score={hits2[0].score:.4f}")
    assert hits2[0].payload["file"] == "src/payments.py"

    # --- incremental: edit auth.py, delete payments.py, add db.py ---
    with open(os.path.join(repo, "src", "auth.py"), "a") as f:
        f.write("\n    def logout(self, token):\n        self._sessions.discard(token)\n")
    os.remove(os.path.join(repo, "src", "payments.py"))
    with open(os.path.join(repo, "src", "db.py"), "w") as f:
        f.write("class ConnectionPool:\n    \"\"\"Database connection pooling.\"\"\"\n\n"
                "    def acquire(self):\n        return self._free.pop()\n")

    with project_lock(lock_path) as ok:
        assert ok
        r2 = indexer.index_project(repo, slug, manifest)
    print("incremental:", r2)

    hits3 = store.search(collection, embedder.embed(["billing invoice amount total cents"])[0], limit=3)
    files3 = [h.payload["file"] for h in hits3]
    print("search 'billing' after delete:", files3)
    assert "src/payments.py" not in files3, "deleted file must be purged"

    hits4 = store.search(collection, embedder.embed(["database connection pool acquire"])[0], limit=3)
    print("search 'db pool':", hits4[0].payload["file"], f"score={hits4[0].score:.4f}")
    assert hits4[0].payload["file"] == "src/db.py", "added file must be searchable"

    # --- remove_project equivalent ---
    reg.remove(repo)
    store.drop_collection(collection)
    assert not store.collection_exists(collection), "collection must be gone"
    assert reg.get_by_path(repo) is None, "registry entry must be gone"
    print("remove_project: collection dropped + registry entry gone ✓")

    manifest.close()
    reg.close()
    shutil.rmtree(repo, ignore_errors=True)
    shutil.rmtree(os.environ["INDEX_ROOT"], ignore_errors=True)
    print("E2E PASS")


if __name__ == "__main__":
    main()