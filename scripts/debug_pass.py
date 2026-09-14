"""Debug: in-process Indexer pass, verify symbol rows land in manifest.

Uses a stub embedder/store so no network is touched.
"""

import os
import shutil
import tempfile

from mcp_code_indexer.config import Config
from mcp_code_indexer.indexer import Indexer
from mcp_code_indexer.manifest import Manifest
from mcp_code_indexer.store import point_id


class StubEmbedder:
    def dimension(self):
        return 4

    def embed(self, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class StubStore:
    def collection_exists(self, name):
        return True

    def create_collection(self, name, dim):
        pass

    def upsert_points(self, name, points):
        print("  upsert", len(points), "points")

    def purge_file_points(self, name, project, path, min_chunk_index=0):
        print("  purge", path)


HPP = """#pragma once
namespace ft {

class FileTransferSession {
 public:
  void start();
  void stop();
 private:
  int state_;
};

enum class Mode { kPush, kPull };

}  // namespace ft
"""

root = tempfile.mkdtemp()
os.makedirs(os.path.join(root, "src"))
with open(os.path.join(root, "src", "session.hpp"), "w") as f:
    f.write(HPP)

cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
             index_root=tempfile.mkdtemp(), stale_ttl=60, embed_batch=48,
             upsert_batch=256, max_file_bytes=1048576, watch_debounce=3)
idx = Indexer(cfg, StubEmbedder(), StubStore())
m = Manifest(os.path.join(cfg.index_root, "m.db"))
res = idx.index_project(root, "debugslug", m)
print("result:", res)
print("symbols:", m.find_symbols("FileTransferSession"))
print("all names:", m.all_symbol_names())
print("Mode:", m.find_symbols("Mode", symbol_type="enum"))
m.close()
shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(cfg.index_root, ignore_errors=True)
