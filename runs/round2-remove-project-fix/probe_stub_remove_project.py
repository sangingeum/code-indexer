"""Stub twin of runs/round2-reverify/probe_remove_project.py.

The re-verify probe needs a reachable vector store. This twin makes the same
observation with no service at all: the REAL ``remove-project`` command and
the REAL ``Store`` adapter run against an in-memory client that keeps physical
collections and aliases in the two name spaces Qdrant keeps them in, so the
resolution the deleter performs is the code under test, not a mock of it.

Usage: uv run python runs/round2-remove-project-fix/probe_stub_remove_project.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "src"))

import code_indexer.store as store_mod  # noqa: E402
from code_indexer import cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

runner = CliRunner()


class FakeClient:
    """The Qdrant client slice the store adapter uses."""

    def __init__(self):
        self.collections: dict[str, set[str]] = {}
        self.aliases: dict[str, str] = {}
        self.ops: list[str] = []

    def get_collections(self):
        return SimpleNamespace(collections=[SimpleNamespace(name=n)
                                            for n in self.collections])

    def get_aliases(self):
        return SimpleNamespace(aliases=[SimpleNamespace(alias_name=a,
                                                       collection_name=c)
                                        for a, c in self.aliases.items()])

    def collection_exists(self, name):
        return name in self.collections

    def create_collection(self, collection_name, vectors_config):
        self.collections[collection_name] = set()

    def create_payload_index(self, **kwargs):
        pass

    def delete_collection(self, collection_name):
        del self.collections[collection_name]
        for alias in [a for a, c in self.aliases.items()
                      if c == collection_name]:
            del self.aliases[alias]
        self.ops.append(f"delete_collection {collection_name}")

    def update_collection_aliases(self, change_aliases_operations):
        for op in change_aliases_operations:
            create = getattr(op, "create_alias", None)
            delete = getattr(op, "delete_alias", None)
            if delete is not None:
                self.aliases.pop(delete.alias_name, None)
                self.ops.append(f"delete_alias {delete.alias_name}")
            if create is not None:
                self.aliases[create.alias_name] = create.collection_name
                self.ops.append(f"create_alias {create.alias_name} -> "
                                f"{create.collection_name}")


class StubEmbedder:
    def dimension(self):
        return 4

    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]

    def embed_with_errors(self, texts):
        return [[0.1] * 4 for _ in texts], []

    def query_instruction_text(self, query):
        return query

    def close(self):
        pass


def make_core(root):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=os.path.join(root, "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    store_mod._load_qdrant()
    client = FakeClient()
    store_mod.QdrantClient = lambda *a, **kw: client  # type: ignore[assignment]
    core = Core(cfg)
    core.store = store_mod.Store(url="http://stub")  # type: ignore[assignment]
    core.embedder = StubEmbedder()  # type: ignore[assignment]
    cli._get_core = lambda skip: core  # type: ignore[assignment]
    return core


def state(client, slug):
    cols = sorted(n for n in client.collections if slug in n)
    aliases = {a: c for a, c in client.aliases.items()
               if slug in a or slug in c}
    return cols, aliases


def report(label, proc):
    print(f"  $ code-indexer {label}  ->  exit={proc.exit_code}")
    for line in (proc.stdout or "").splitlines():
        print("    | " + line.replace(os.environ.get("PROBE_ROOT", ""),
                                       "<workdir>"))
    for line in (proc.stderr or "").splitlines():
        print("    ! " + line.replace(os.environ.get("PROBE_ROOT", ""),
                                       "<workdir>"))


def main() -> int:
    root = tempfile.mkdtemp(prefix="remove-project-stub-",
                            dir=os.environ.get("TMPDIR"))
    os.environ["PROBE_ROOT"] = root
    print("=" * 72)
    print("  remove-project vs the identity-stamped physical collection")
    print("  (stub twin: real command + real Store, in-memory Qdrant client)")
    print("=" * 72)
    try:
        # --- a project whose alias serves an identity-stamped collection ---
        project = os.path.join(root, "proj")
        os.makedirs(project)
        with open(os.path.join(project, "a.py"), "w", encoding="utf-8") as fh:
            fh.write("def alpha(x):\n    return x + 1\n")
        core = make_core(root)
        report("add-project", runner.invoke(app, ["add-project", project,
                                                  "--vectors", "off"]))
        slug = core.registry.get_by_path(project).slug
        client = core.store.client
        live = f"idx_{slug}__dc5868ea"
        stale = f"idx_{slug}__0f0f0f0f"
        client.collections[stale] = set()
        client.collections[live] = set()
        client.aliases[f"idx_{slug}"] = live
        print(f"    seeded: collections={sorted(client.collections)} "
              f"aliases={dict(client.aliases)}")
        proc = runner.invoke(app, ["remove-project", project])
        report("remove-project", proc)
        cols, aliases = state(client, slug)
        print(f"    after remove-project: collections={cols} aliases={aliases}")
        print(f"    store calls: {client.ops}")
        print(f"    residue left behind: {bool(cols or aliases)}")

        # --- a project that never had a collection ---
        second = os.path.join(root, "plain")
        os.makedirs(second)
        with open(os.path.join(second, "b.py"), "w", encoding="utf-8") as fh:
            fh.write("def beta():\n    return 2\n")
        report("add-project", runner.invoke(app, ["add-project", second,
                                                  "--vectors", "off"]))
        slug2 = core.registry.get_by_path(second).slug
        client.ops.clear()
        proc = runner.invoke(app, ["remove-project", second])
        report("remove-project", proc)
        cols, aliases = state(client, slug2)
        print(f"    after remove-project: collections={cols} aliases={aliases}")
        print(f"    store calls: {client.ops}")
        print(f"    claimed a drop: {'dropped' in (proc.stdout or '')}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
